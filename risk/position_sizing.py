"""Lot sizing with contract value, round-trip costs, leverage and hard risk limits."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import json
from config.orb_settings import ORBSettings, settings

@dataclass
class RiskDecision:
    approved: bool
    lots: int
    max_risk_cash: float
    estimated_loss_at_sl: float
    estimated_fees: float
    estimated_spread_slippage: float
    estimated_margin: float
    reason: str
    def to_dict(self): return asdict(self)

def estimate_lot_risk(entry: float, stop: float, cfg: ORBSettings = settings) -> tuple[float,float,float]:
    notional = entry * cfg.contract_size_xaut
    gross = abs(entry-stop) * cfg.contract_size_xaut
    fee = notional * 2 * cfg.fee_pct_per_side / 100.0
    spread_slip = notional * (cfg.spread_pct_round_trip + cfg.slippage_pct_round_trip) / 100.0
    return gross + fee + spread_slip, fee, spread_slip

def size_position(entry: float, stop: float, equity: float, cfg: ORBSettings = settings) -> RiskDecision:
    if min(entry, stop, equity, cfg.contract_size_xaut, cfg.leverage) <= 0:
        return RiskDecision(False,0,0,0,0,0,0,"Entry, stop, equity, contract size and leverage must be positive.")
    if entry == stop:
        return RiskDecision(False,0,0,0,0,0,0,"Stop distance is zero.")
    budget = equity * cfg.risk_pct / 100
    per_lot, fee, costs = estimate_lot_risk(entry, stop, cfg)
    if per_lot <= 0 or per_lot > budget:
        return RiskDecision(False,0,budget,per_lot,fee,costs,0,
            f"NO TRADE: minimum lot risk ({per_lot:.4f}) exceeds risk budget ({budget:.4f}).")
    lots = int(budget // per_lot)
    margin_per_lot = entry * cfg.contract_size_xaut / cfg.leverage
    max_margin = equity * cfg.max_margin_pct_equity / 100
    lots = min(lots, int(max_margin // margin_per_lot))
    if lots < 1:
        return RiskDecision(False,0,budget,per_lot,fee,costs,0,"NO TRADE: minimum lot exceeds configured margin budget.")
    total_risk = per_lot * lots
    margin = margin_per_lot * lots
    if total_risk > budget + 1e-9:
        return RiskDecision(False,0,budget,total_risk,fee*lots,costs*lots,margin,"NO TRADE: sized risk exceeds risk budget.")
    return RiskDecision(True,lots,budget,total_risk,fee*lots,costs*lots,margin,
                        f"Paper sizing only: {lots} lot(s), estimated stop loss including costs {total_risk:.4f}.")

class DailyRiskState:
    """Crash-persistent daily stop and trade-count guard."""
    def __init__(self, path: Path = settings.state_path, cfg: ORBSettings = settings):
        self.path, self.cfg = Path(path), cfg
        self.path.parent.mkdir(parents=True, exist_ok=True)
    def _today(self, now=None):
        dt=now or datetime.now(timezone.utc)
        if dt.tzinfo is None: raise ValueError("now must be timezone-aware")
        return dt.astimezone(__import__("zoneinfo").ZoneInfo(self.cfg.timezone)).date().isoformat()
    def load(self, now=None):
        day=self._today(now)
        try: data=json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError,ValueError): data={}
        if data.get("date") != day: return {"date":day,"realized_pnl":0.0,"trades":0,"last_loss_at":None}
        return data
    def save(self, state):
        tmp=self.path.with_suffix(self.path.suffix+".tmp")
        tmp.write_text(json.dumps(state,indent=2),encoding="utf-8")
        tmp.replace(self.path)
    def can_trade(self, now=None):
        state=self.load(now)
        if state["realized_pnl"] <= -abs(self.cfg.daily_loss_limit_pct)/100:
            return False,"Daily loss limit hit.",state
        if int(state["trades"]) >= self.cfg.max_trades_per_day:
            return False,"Maximum trades for today already taken.",state
        last=state.get("last_loss_at")
        if last:
            then=datetime.fromisoformat(last)
            cur=now or datetime.now(timezone.utc)
            if cur.tzinfo is None: raise ValueError("now must be timezone-aware")
            if (cur-then).total_seconds() < self.cfg.cooldown_after_loss_minutes*60:
                return False,"Loss cooldown active; block revenge/FOMO entries.",state
        return True,"Daily risk guards passed.",state
    def record_trade(self, pnl_cash: float, equity_at_start: float, now=None):
        state=self.load(now)
        state["trades"]=int(state["trades"])+1
        state["realized_pnl"]=float(state["realized_pnl"])+float(pnl_cash)/max(float(equity_at_start),1e-9)
        if pnl_cash < 0:
            dt=now or datetime.now(timezone.utc)
            if dt.tzinfo is None: raise ValueError("now must be timezone-aware")
            state["last_loss_at"]=dt.isoformat()
        self.save(state)
        return state
