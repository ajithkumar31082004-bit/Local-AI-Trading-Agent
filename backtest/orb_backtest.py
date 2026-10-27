"""Day-by-day replay of the same deterministic ORB strategy used by live alerts."""
from dataclasses import dataclass, asdict
import pandas as pd
from config.orb_settings import ORBSettings, settings
from strategy.orb_ema_volume import evaluate_orb
from risk.position_sizing import estimate_lot_risk

@dataclass
class ORBBacktestResult:
    days: int
    trades: int
    skipped_days: int
    win_rate_pct: float
    average_r: float
    profit_factor: float
    max_drawdown_pct: float
    longest_losing_streak: int
    total_r: float
    equity_curve: list[dict]
    trade_log: list[dict]
    def to_dict(self): return asdict(self)

def run_backtest(candles: pd.DataFrame, initial_equity: float = 10000.0,
                 cfg: ORBSettings = settings) -> ORBBacktestResult:
    if candles.empty: raise ValueError("No candles supplied.")
    df=candles.copy()
    idx=pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is None: idx=idx.tz_localize(cfg.timezone,ambiguous="raise",nonexistent="raise")
    else: idx=idx.tz_convert(cfg.timezone)
    df.index=idx
    if not df.index.is_monotonic_increasing: df=df.sort_index()
    days=list(dict.fromkeys(df.index.date))
    equity=float(initial_equity); peak=equity; max_dd=0.0
    curve=[]; trades=[]; skipped=0; losing_streak=0; longest=0
    for day in days:
        day_frame=df[[ts.date()==day for ts in df.index]]
        day_traded=False; pending=None; day_r=0.0
        # Replay prefixes; evaluate strategy only on information available at each close.
        for i in range(1,len(day_frame)+1):
            prefix=day_frame.iloc[:i]
            ts=prefix.index[-1]
            # If in a simulated position, use next candle's high/low to test SL/TP.
            if pending and i-1 > pending["entry_i"]:
                candle=prefix.iloc[-1]
                side=pending["direction"]; sl=pending["sl"]; tp=pending["tp"]
                hit_sl=float(candle["Low"])<=sl if side=="LONG" else float(candle["High"])>=sl
                hit_tp=float(candle["High"])>=tp if side=="LONG" else float(candle["Low"])<=tp
                if hit_sl or hit_tp:
                    # Conservative rule: if both hit in one bar, stop wins.
                    exit_price=sl if hit_sl else tp
                    risk=abs(pending["entry"]-sl)
                    gross_r=((exit_price-pending["entry"])/risk) if side=="LONG" else ((pending["entry"]-exit_price)/risk)
                    # Deduct modeled round-trip fee + spread/slippage, expressed in price-risk units.
                    _, fee_cost, spread_slip_cost = estimate_lot_risk(pending["entry"], sl, cfg)
                    per_lot_risk = max(abs(pending["entry"]-sl)*cfg.contract_size_xaut, 1e-12)
                    cost_r = (fee_cost + spread_slip_cost) / per_lot_risk
                    net_r=gross_r-cost_r
                    equity += equity*cfg.risk_pct/100*net_r
                    day_r += net_r
                    trades.append({"date":str(day),"direction":side,"entry_time":str(pending["time"]),
                                   "exit_time":str(ts),"entry":pending["entry"],"sl":sl,"tp":tp,
                                   "exit":exit_price,"r":round(net_r,4),"exit_reason":"SL" if hit_sl else "TP"})
                    if net_r<0: losing_streak+=1; longest=max(longest,losing_streak)
                    else: losing_streak=0
                    pending=None
                    # one trade per day regardless of stop/target result
                    break
            sig=evaluate_orb(prefix,cfg)
            if sig.direction in {"LONG","SHORT"} and sig.timestamp==str(ts) and not day_traded:
                pending={"direction":sig.direction,"entry":float(sig.entry),"sl":float(sig.sl),
                         "tp":float(sig.tp),"entry_i":i-1,"time":ts}
                day_traded=True
        # Close any position still open at session end at the last completed close.
        # This avoids silently dropping overnight/open positions from the reported results.
        if pending is not None:
            last_ts = day_frame.index[-1]
            exit_price = float(day_frame["Close"].iloc[-1])
            side = pending["direction"]
            risk = abs(pending["entry"] - pending["sl"])
            gross_r = ((exit_price-pending["entry"])/risk) if side == "LONG" else ((pending["entry"]-exit_price)/risk)
            _, fee_cost, spread_slip_cost = estimate_lot_risk(pending["entry"], pending["sl"], cfg)
            per_lot_risk = max(abs(pending["entry"]-pending["sl"])*cfg.contract_size_xaut, 1e-12)
            net_r = gross_r - (fee_cost + spread_slip_cost) / per_lot_risk
            equity += equity * cfg.risk_pct / 100 * net_r
            day_r += net_r
            trades.append({"date":str(day),"direction":side,"entry_time":str(pending["time"]),
                           "exit_time":str(last_ts),"entry":pending["entry"],"sl":pending["sl"],"tp":pending["tp"],
                           "exit":exit_price,"r":round(net_r,4),"exit_reason":"END_OF_DAY"})
            if net_r < 0:
                losing_streak += 1
                longest = max(longest, losing_streak)
            else:
                losing_streak = 0
        if not day_traded: skipped+=1
        peak=max(peak,equity)
        max_dd=max(max_dd,(peak-equity)/peak*100 if peak else 0)
        curve.append({"date":str(day),"equity":round(equity,4),"daily_r":round(day_r,4)})
    r_values=[t["r"] for t in trades]
    wins=[r for r in r_values if r>0]; losses=[r for r in r_values if r<0]
    pf=sum(wins)/abs(sum(losses)) if losses else (float("inf") if wins else 0.0)
    return ORBBacktestResult(len(days),len(trades),skipped,len(wins)/len(trades)*100 if trades else 0.0,
        sum(r_values)/len(r_values) if r_values else 0.0,pf,max_dd,longest,sum(r_values),curve,trades)
