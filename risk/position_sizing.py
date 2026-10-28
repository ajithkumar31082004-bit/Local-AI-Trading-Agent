"""Deterministic lot sizing with official contract specifications, trading costs, leverage, and crash-persistent daily risk guard."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
import json
from zoneinfo import ZoneInfo
from filelock import FileLock
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_lot_risk(entry: float, stop: float, cfg: ORBSettings = settings) -> Tuple[float, float, float]:
    """Calculate total financial risk per 1 tradable lot.
    
    Returns (total_risk_per_lot, round_trip_fee, spread_and_slippage).
    Uses the official contract multiplier (contract_size_xaut) and P&L model.
    """
    notional = entry * cfg.contract_size_xaut
    gross_risk = abs(entry - stop) * cfg.contract_size_xaut
    round_trip_fee = notional * 2.0 * (cfg.fee_pct_per_side / 100.0)
    spread_and_slippage = notional * (cfg.spread_pct_round_trip + cfg.slippage_pct_round_trip) / 100.0
    total_risk = gross_risk + round_trip_fee + spread_and_slippage
    return total_risk, round_trip_fee, spread_and_slippage


def size_position(entry: float, stop: float, equity: float, cfg: ORBSettings = settings) -> RiskDecision:
    """Calculate paper position size adhering to strict risk budget, margin limit, and lot increment.
    
    Refuses any trade where the minimum lot exceeds the risk budget.
    """
    if min(entry, stop, equity, cfg.contract_size_xaut, cfg.leverage) <= 0:
        return RiskDecision(
            approved=False,
            lots=0,
            max_risk_cash=0.0,
            estimated_loss_at_sl=0.0,
            estimated_fees=0.0,
            estimated_spread_slippage=0.0,
            estimated_margin=0.0,
            reason="Invalid parameters: entry, stop, equity, contract size, and leverage must be positive.",
        )

    if abs(entry - stop) < 1e-6:
        return RiskDecision(
            approved=False,
            lots=0,
            max_risk_cash=0.0,
            estimated_loss_at_sl=0.0,
            estimated_fees=0.0,
            estimated_spread_slippage=0.0,
            estimated_margin=0.0,
            reason="Stop-loss distance is zero.",
        )

    risk_budget = equity * (cfg.risk_pct / 100.0)
    per_lot_risk, fee_per_lot, costs_per_lot = estimate_lot_risk(entry, stop, cfg)

    if per_lot_risk <= 0 or per_lot_risk > risk_budget:
        return RiskDecision(
            approved=False,
            lots=0,
            max_risk_cash=round(risk_budget, 4),
            estimated_loss_at_sl=round(per_lot_risk, 4),
            estimated_fees=round(fee_per_lot, 4),
            estimated_spread_slippage=round(costs_per_lot, 4),
            estimated_margin=0.0,
            reason=f"NO TRADE: minimum lot risk (${per_lot_risk:.4f}) exceeds configured risk budget (${risk_budget:.4f}).",
        )

    raw_lots = int(risk_budget // per_lot_risk)
    lots = (raw_lots // cfg.lot_increment) * cfg.lot_increment

    notional_per_lot = entry * cfg.contract_size_xaut
    margin_per_lot = notional_per_lot / cfg.leverage
    max_margin_budget = equity * (cfg.max_margin_pct_equity / 100.0)
    max_margin_lots = int(max_margin_budget // margin_per_lot) if margin_per_lot > 0 else 0

    lots = min(lots, max_margin_lots)
    if lots < 1:
        return RiskDecision(
            approved=False,
            lots=0,
            max_risk_cash=round(risk_budget, 4),
            estimated_loss_at_sl=round(per_lot_risk, 4),
            estimated_fees=round(fee_per_lot, 4),
            estimated_spread_slippage=round(costs_per_lot, 4),
            estimated_margin=round(margin_per_lot, 4),
            reason=f"NO TRADE: minimum lot requires margin (${margin_per_lot:.4f}) exceeding allowed margin budget (${max_margin_budget:.4f}).",
        )

    total_risk = per_lot_risk * lots
    total_margin = margin_per_lot * lots

    if total_risk > risk_budget + 1e-9:
        return RiskDecision(
            approved=False,
            lots=0,
            max_risk_cash=round(risk_budget, 4),
            estimated_loss_at_sl=round(total_risk, 4),
            estimated_fees=round(fee_per_lot * lots, 4),
            estimated_spread_slippage=round(costs_per_lot * lots, 4),
            estimated_margin=round(total_margin, 4),
            reason="NO TRADE: sized lot risk exceeds the risk budget.",
        )

    return RiskDecision(
        approved=True,
        lots=lots,
        max_risk_cash=round(risk_budget, 4),
        estimated_loss_at_sl=round(total_risk, 4),
        estimated_fees=round(fee_per_lot * lots, 4),
        estimated_spread_slippage=round(costs_per_lot * lots, 4),
        estimated_margin=round(total_margin, 4),
        reason=f"Paper sizing approved: {lots} lot(s) (total risk ${total_risk:.4f} vs budget ${risk_budget:.4f}, margin ${total_margin:.4f}).",
    )


class DailyRiskState:
    """Crash-persistent and concurrency-safe daily risk limit and cooldown guard."""

    def __init__(self, path: Path = settings.state_path, cfg: ORBSettings = settings):
        self.path = Path(path)
        self.cfg = cfg
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _today(self, now: Optional[datetime] = None) -> str:
        dt = now or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return dt.astimezone(ZoneInfo(self.cfg.timezone)).date().isoformat()

    def _load_unlocked(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        day = self._today(now)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("date") != day:
            return {
                "date": day,
                "realized_pnl": 0.0,
                "trades": 0,
                "last_loss_at": None,
            }
        return data

    def _save_unlocked(self, state: Dict[str, Any]):
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _can_trade_unlocked(self, state: Dict[str, Any], now: Optional[datetime] = None) -> Tuple[bool, str]:
        if state["realized_pnl"] <= -abs(self.cfg.daily_loss_limit_pct) / 100.0:
            return False, f"Daily loss limit hit ({state['realized_pnl'] * 100:.2f}% <= -{self.cfg.daily_loss_limit_pct}%)."

        if int(state["trades"]) >= self.cfg.max_trades_per_day:
            return False, f"Maximum trades ({self.cfg.max_trades_per_day}) for today already reached."

        last_loss = state.get("last_loss_at")
        if last_loss:
            then = datetime.fromisoformat(last_loss)
            cur = now or datetime.now(timezone.utc)
            if cur.tzinfo is None:
                raise ValueError("now must be timezone-aware")
            elapsed_minutes = (cur - then).total_seconds() / 60.0
            if elapsed_minutes < self.cfg.cooldown_after_loss_minutes:
                remaining = self.cfg.cooldown_after_loss_minutes - elapsed_minutes
                return False, f"Loss cooldown active ({remaining:.1f} minutes remaining); revenge/FOMO trading blocked."

        return True, "Daily risk guards passed."

    def load(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        with FileLock(str(self.lock_path), timeout=5):
            return self._load_unlocked(now)

    def save(self, state: Dict[str, Any]):
        with FileLock(str(self.lock_path), timeout=5):
            self._save_unlocked(state)

    def can_trade(self, now: Optional[datetime] = None) -> Tuple[bool, str, Dict[str, Any]]:
        with FileLock(str(self.lock_path), timeout=5):
            state = self._load_unlocked(now)
            allowed, reason = self._can_trade_unlocked(state, now)
            return allowed, reason, state

    def reserve_trade(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Reserve a trade slot atomically to prevent concurrent alert executions."""
        with FileLock(str(self.lock_path), timeout=5):
            state = self._load_unlocked(now)
            allowed, reason = self._can_trade_unlocked(state, now)
            if not allowed:
                raise RuntimeError(reason)
            state["trades"] = int(state["trades"]) + 1
            self._save_unlocked(state)
            return state

    def record_result(self, pnl_cash: float, equity_at_start: float, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Record the realized outcome of an already-reserved trade."""
        with FileLock(str(self.lock_path), timeout=5):
            state = self._load_unlocked(now)
            pnl_fraction = float(pnl_cash) / max(float(equity_at_start), 1e-9)
            state["realized_pnl"] = float(state["realized_pnl"]) + pnl_fraction
            if pnl_cash < 0:
                dt = now or datetime.now(timezone.utc)
                if dt.tzinfo is None:
                    raise ValueError("now must be timezone-aware")
                state["last_loss_at"] = dt.isoformat()
            self._save_unlocked(state)
            return state

    def record_trade(self, pnl_cash: float, equity_at_start: float, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Record trade slot and outcome together when no prior reservation was made."""
        with FileLock(str(self.lock_path), timeout=5):
            state = self._load_unlocked(now)
            state["trades"] = int(state["trades"]) + 1
            pnl_fraction = float(pnl_cash) / max(float(equity_at_start), 1e-9)
            state["realized_pnl"] = float(state["realized_pnl"]) + pnl_fraction
            if pnl_cash < 0:
                dt = now or datetime.now(timezone.utc)
                if dt.tzinfo is None:
                    raise ValueError("now must be timezone-aware")
                state["last_loss_at"] = dt.isoformat()
            self._save_unlocked(state)
            return state
