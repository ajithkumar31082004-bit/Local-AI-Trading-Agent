"""Chronological day-by-day replay of historical 5m candles with identical strategy and risk rules."""
import logging
import tempfile
from dataclasses import dataclass, asdict
from datetime import time as time_of_day
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from config.orb_settings import ORBSettings, settings
from strategy.orb_ema_volume import evaluate_orb
from risk.position_sizing import estimate_lot_risk, size_position

logger = logging.getLogger(__name__)


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
    net_pnl: float
    equity_curve: List[Dict[str, Any]]
    trade_log: List[Dict[str, Any]]
    max_drawdown_r: float = 0.0
    no_setup_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def save_equity_curve_html(equity_curve: List[Dict[str, Any]], output_path: str | Path = "data/orb_equity_curve.html") -> Optional[Path]:
    """Generate and save an interactive Plotly HTML equity curve chart.

    output_path is resolved and restricted to the workspace or system temp directory
    to prevent path traversal when the argument originates from untrusted input.
    """
    if not equity_curve:
        return None
    out = Path(output_path).resolve()
    safe_bases = (Path.cwd().resolve(), Path(tempfile.gettempdir()).resolve())
    if not any(out.is_relative_to(base) for base in safe_bases):
        logger.warning("Rejected equity curve output path outside allowed directories: %s", out)
        return None
    try:
        import plotly.graph_objects as go
        dates = [p["date"] for p in equity_curve]
        equities = [p["equity"] for p in equity_curve]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates,
            y=equities,
            mode="lines+markers",
            name="Portfolio Equity",
            line=dict(color="#2563EB", width=2),
            marker=dict(size=4),
        ))
        fig.update_layout(
            title="XAUTUSD ORB Strategy Backtest Equity Curve",
            xaxis_title="Trading Session Date",
            yaxis_title="Account Equity ($)",
            template="plotly_white",
            hovermode="x unified",
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(out)
        return out
    except Exception as exc:  # noqa: BLE001  – plotly is optional
        logger.debug("Could not save equity curve HTML (plotly unavailable?): %s", exc)
        return None


def run_backtest(
    candles: pd.DataFrame,
    initial_equity: float = 10000.0,
    cfg: ORBSettings = settings,
    chart_output_path: Optional[str | Path] = "data/orb_equity_curve.html",
    strategy_fn: Optional[Any] = None,
) -> ORBBacktestResult:
    """Execute realistic chronological backtest replay across historical 5m bars.
    
    - Replays each day chronologically without future lookahead.
    - Retains prior candles for indicator warm-up.
    - Applies conservative same-candle stop/target collision resolution (SL hit).
    - Closes open positions at session close.
    - Strictly deducts commissions, spread, and slippage.
    - Accounts for zero-trade and skipped days as 0R.
    """
    if candles.empty:
        raise ValueError("No candles supplied for backtest replay.")

    if strategy_fn is None:
        if getattr(cfg, "strategy_type", "ORB") in {"TREND", "TREND_FOLLOWING"}:
            from strategy.trend_following import evaluate_trend_following
            strategy_eval_fn = evaluate_trend_following
        else:
            strategy_eval_fn = evaluate_orb
    else:
        strategy_eval_fn = strategy_fn

    df = candles.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is None:
        idx = idx.tz_localize(cfg.timezone, ambiguous="raise", nonexistent="raise")
    else:
        idx = idx.tz_convert(cfg.timezone)
    df.index = idx
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()

    ts_to_iloc = {t: idx for idx, t in enumerate(df.index)}
    days = list(dict.fromkeys(df.index.date))
    equity = float(initial_equity)
    peak_equity = equity
    max_drawdown = 0.0

    curve: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    skipped_days = 0
    current_losing_streak = 0
    longest_losing_streak = 0

    for day in days:
        day_frame = df[[ts.date() == day for ts in df.index]]
        pending_trade = None
        day_r = 0.0
        trades_today = 0
        last_loss_exit_i: Optional[int] = None
        session_end_t = time_of_day.fromisoformat(getattr(cfg, "session_end", "23:30"))

        for i in range(1, len(day_frame) + 1):
            ts = day_frame.index[i - 1]
            loc = ts_to_iloc[ts]
            prefix = df.iloc[:loc + 1]
            candle = prefix.iloc[-1]

            # If in an active position, check if current bar hits Stop Loss or Take Profit or Session End
            if pending_trade and (i - 1) > pending_trade["entry_i"]:
                side = pending_trade["direction"]
                sl = pending_trade["sl"]
                tp = pending_trade["tp"]

                hit_sl = float(candle["Low"]) <= sl if side == "LONG" else float(candle["High"]) >= sl
                hit_tp = float(candle["High"]) >= tp if side == "LONG" else float(candle["Low"]) <= tp
                hit_session_end = ts.time() >= session_end_t

                if hit_sl or hit_tp or hit_session_end:
                    # Conservative rule: if both SL and TP are touched in the same 5m bar, SL wins
                    if hit_sl:
                        exit_price = sl
                        exit_reason = "SL"
                    elif hit_tp:
                        exit_price = tp
                        exit_reason = "TP"
                    else:
                        exit_price = float(candle["Close"])
                        exit_reason = "SESSION_END"

                    # Deduct trading friction (fees + spread + slippage)
                    contract_qty = pending_trade["lots"] * cfg.contract_size_xaut
                    price_delta = (exit_price - pending_trade["entry"]) if side == "LONG" else (pending_trade["entry"] - exit_price)
                    gross_pnl_cash = contract_qty * price_delta

                    _, fee_cost, spread_slip_cost = estimate_lot_risk(pending_trade["entry"], sl, cfg)
                    total_friction = (fee_cost + spread_slip_cost) * pending_trade["lots"]
                    pnl_cash = gross_pnl_cash - total_friction

                    risk_budget = equity * (cfg.risk_pct / 100.0)
                    net_r = pnl_cash / risk_budget if risk_budget > 0 else 0.0

                    equity += pnl_cash
                    day_r += net_r
                    trades_today += 1

                    trades.append({
                        "date": str(day),
                        "direction": side,
                        "entry_time": str(pending_trade["time"]),
                        "exit_time": str(ts),
                        "entry": pending_trade["entry"],
                        "sl": sl,
                        "tp": tp,
                        "exit": exit_price,
                        "r": round(net_r, 4),
                        "pnl_cash": round(pnl_cash, 2),
                        "exit_reason": exit_reason,
                    })

                    if net_r < 0:
                        current_losing_streak += 1
                        longest_losing_streak = max(longest_losing_streak, current_losing_streak)
                        last_loss_exit_i = i - 1
                    else:
                        current_losing_streak = 0

                    pending_trade = None

                    # If hit max trades or daily loss limit, stop trading for the day
                    max_trades = getattr(cfg, "max_trades_per_day", 1)
                    daily_loss_limit = getattr(cfg, "daily_loss_limit_r", 2.0)
                    if trades_today >= max_trades or day_r <= -daily_loss_limit:
                        break

            # Evaluate strategy on current completed candle prefix if no active trade and guards pass
            max_trades = getattr(cfg, "max_trades_per_day", 1)
            daily_loss_limit = getattr(cfg, "daily_loss_limit_r", 2.0)
            cooldown_bars = getattr(cfg, "cooldown_bars_after_loss", 6)
            cooldown_active = (last_loss_exit_i is not None) and ((i - 1) < last_loss_exit_i + cooldown_bars)

            can_evaluate = (
                pending_trade is None
                and trades_today < max_trades
                and day_r > -daily_loss_limit
                and not cooldown_active
            )

            if can_evaluate:
                sig = strategy_eval_fn(prefix, cfg)
                if sig.direction in {"LONG", "SHORT"} and sig.timestamp == str(ts):
                    # Size trade with risk engine
                    decision = size_position(sig.entry, sig.sl, equity, cfg)
                    if decision.approved:
                        pending_trade = {
                            "direction": sig.direction,
                            "entry": float(sig.entry),
                            "sl": float(sig.sl),
                            "tp": float(sig.tp),
                            "lots": decision.lots,
                            "entry_i": i - 1,
                            "time": ts,
                        }

        # Close any position still open at the end of the day at the day's final close price
        if pending_trade is not None:
            last_ts = day_frame.index[-1]
            exit_price = float(day_frame["Close"].iloc[-1])
            side = pending_trade["direction"]
            contract_qty = pending_trade["lots"] * cfg.contract_size_xaut
            price_delta = (exit_price - pending_trade["entry"]) if side == "LONG" else (pending_trade["entry"] - exit_price)
            gross_pnl_cash = contract_qty * price_delta

            _, fee_cost, spread_slip_cost = estimate_lot_risk(pending_trade["entry"], pending_trade["sl"], cfg)
            total_friction = (fee_cost + spread_slip_cost) * pending_trade["lots"]
            pnl_cash = gross_pnl_cash - total_friction

            risk_budget = equity * (cfg.risk_pct / 100.0)
            net_r = pnl_cash / risk_budget if risk_budget > 0 else 0.0

            equity += pnl_cash
            day_r += net_r
            trades_today += 1

            trades.append({
                "date": str(day),
                "direction": side,
                "entry_time": str(pending_trade["time"]),
                "exit_time": str(last_ts),
                "entry": pending_trade["entry"],
                "sl": pending_trade["sl"],
                "tp": pending_trade["tp"],
                "exit": exit_price,
                "r": round(net_r, 4),
                "pnl_cash": round(pnl_cash, 2),
                "exit_reason": "END_OF_DAY",
            })

            if net_r < 0:
                current_losing_streak += 1
                longest_losing_streak = max(longest_losing_streak, current_losing_streak)
            else:
                current_losing_streak = 0

        if trades_today == 0:
            skipped_days += 1

        peak_equity = max(peak_equity, equity)
        drawdown_pct = ((peak_equity - equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown_pct)

        curve.append({
            "date": str(day),
            "equity": round(equity, 2),
            "daily_r": round(day_r, 4),
        })

    r_multiples = [t["r"] for t in trades]
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r < 0]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    avg_r = (sum(r_multiples) / len(r_multiples)) if r_multiples else 0.0

    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else (float("inf") if sum_wins > 0 else 0.0)
    net_pnl = round(equity - float(initial_equity), 2)

    # Compute max drawdown in R terms
    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in trades:
        cum_r += t["r"]
        if cum_r > peak_r:
            peak_r = cum_r
        dd_r = peak_r - cum_r
        if dd_r > max_dd_r:
            max_dd_r = dd_r

    no_setup_pct = round((skipped_days / len(days) * 100.0) if days else 0.0, 2)

    if chart_output_path:
        save_equity_curve_html(curve, chart_output_path)

    return ORBBacktestResult(
        days=len(days),
        trades=len(trades),
        skipped_days=skipped_days,
        win_rate_pct=round(win_rate, 2),
        average_r=round(avg_r, 4),
        profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
        max_drawdown_pct=round(max_drawdown, 2),
        longest_losing_streak=longest_losing_streak,
        total_r=round(sum(r_multiples), 4),
        net_pnl=net_pnl,
        equity_curve=curve,
        trade_log=trades,
        max_drawdown_r=round(max_dd_r, 4),
        no_setup_pct=no_setup_pct,
    )
