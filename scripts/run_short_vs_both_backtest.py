"""Comparative Backtest: Evaluating Portfolio Ensemble under BOTH vs SHORT_ONLY Mode.

Simulates 7-strategy ensemble under full exchange friction:
- 0.05% taker fee, 18% GST, 0.02% spread, 0.02% slippage.
- Micro-lot sizing (1 lot = 0.001 XAUT).
- 4-Stage Stop-Loss Ratchet Ladder enabled (0.8R BE, 1.0R -> +0.50R, 1.6R -> +1.10R, 2.2R -> +1.70R).
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.orb_settings import ORBSettings
from backtest.orb_backtest import run_backtest
from strategy.arbitrator import evaluate_arbitrated_portfolio


def compute_metrics(res, label: str) -> dict:
    trades = res.trades if hasattr(res, "trades") and isinstance(res.trades, list) else res.trade_log
    count = len(trades)
    if count == 0:
        return {
            "Configuration": label,
            "Trades": 0,
            "Win Rate (%)": 0.0,
            "Net P&L ($)": 0.0,
            "Profit Factor": 0.0,
            "Max DD (%)": 0.0,
            "Expectancy (R)": 0.0,
            "Total R": 0.0,
            "Wins": 0,
            "Losses": 0,
        }

    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    win_rate = (len(wins) / count) * 100.0
    r_multiples = [t["r"] for t in trades]
    sum_wins = sum(w["pnl_cash"] for w in wins)
    sum_losses = abs(sum(l["pnl_cash"] for l in losses))
    pf = (sum_wins / sum_losses) if sum_losses > 0 else (999.0 if sum_wins > 0 else 0.0)
    exp_r = sum(r_multiples) / count if count > 0 else 0.0

    return {
        "Configuration": label,
        "Trades": count,
        "Win Rate (%)": round(win_rate, 2),
        "Net P&L ($)": round(res.net_pnl, 2),
        "Profit Factor": round(pf, 2),
        "Max DD (%)": round(res.max_drawdown_pct, 2),
        "Expectancy (R)": round(exp_r, 4),
        "Total R": round(res.total_r, 4),
        "Wins": len(wins),
        "Losses": len(losses),
    }


def main():
    csv_path = ROOT / "data" / "xautusd_multi_month_5m.csv"
    if not csv_path.exists():
        print(f"[ERROR] Data file not found: {csv_path}")
        return

    print("Loading historical data...", flush=True)
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("Asia/Kolkata")
    else:
        df.index = df.index.tz_convert("Asia/Kolkata")
    df = df.sort_index()

    base_cfg_kwargs = {
        "symbol": "XAUTUSD",
        "session_start": "09:00",
        "session_end": "23:30",
        "no_entry_minutes_after_open": 45,
        "last_entry_time": "22:00",
        "max_trades_per_day": 1,
        "cooldown_bars_after_loss": 6,
        "daily_loss_limit_r": 2.0,
        "risk_pct": 0.5,
        "daily_loss_limit_pct": 1.5,
        "max_margin_pct_equity": 10.0,
        "leverage": 2.0,
        "fee_pct_per_side": 0.05,
        "apply_gst": True,
        "gst_rate_pct": 18.0,
        "spread_pct_round_trip": 0.02,
        "slippage_pct_round_trip": 0.02,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
        "min_signal_quality_score": 0.75,
        "min_15m_adx_long": 26.0,
        "min_continuation_volume_ratio_long": 1.0,
        "breakeven_trigger_r": 0.8,
        "enable_multi_stage_exits": True,
        "strategy_type": "PORTFOLIO",
    }

    # Evaluate across Out-of-Sample (Oct 16 - Oct 30) and Full Period
    splits = {
        "Out-of-Sample (Oct 16 - Oct 30)": (df.index >= "2026-10-16") & (df.index <= "2026-10-30 23:59:59+05:30"),
        "In-Sample Dev (Aug 5 - Sep 15)": (df.index >= "2026-08-05") & (df.index <= "2026-09-15 23:59:59+05:30"),
    }

    print("\n" + "=" * 105, flush=True)
    print("BACKTEST COMPARISON: BOTH (LONG + SHORT) vs SHORT_ONLY MODE", flush=True)
    print("=" * 105, flush=True)

    for split_label, mask in splits.items():
        sub_df = df[mask].copy()
        days_count = len(dict.fromkeys(sub_df.index.date))
        print(f"\n>>> Split: {split_label} ({len(sub_df):,} bars, {days_count} days) <<<", flush=True)

        # 1. BOTH Directions
        cfg_both = ORBSettings(**base_cfg_kwargs, allowed_trade_directions="BOTH")
        res_both = run_backtest(sub_df, cfg=cfg_both, strategy_fn=evaluate_arbitrated_portfolio, initial_equity=10000.0)
        m_both = compute_metrics(res_both, "Portfolio (BOTH Directions)")

        # 2. SHORT_ONLY Mode
        cfg_short = ORBSettings(**base_cfg_kwargs, allowed_trade_directions="SHORT_ONLY")
        res_short = run_backtest(sub_df, cfg=cfg_short, strategy_fn=evaluate_arbitrated_portfolio, initial_equity=10000.0)
        m_short = compute_metrics(res_short, "Portfolio (SHORT_ONLY Mode)")

        print(f"{'Configuration':<32} | {'Trades':<6} | {'Win Rate':<8} | {'PF':<5} | {'Net P&L':<10} | {'Wins/Losses':<12} | {'Total R':<8}", flush=True)
        print("-" * 105, flush=True)
        for m in (m_both, m_short):
            wl = f"{m['Wins']}W / {m['Losses']}L"
            print(f"{m['Configuration']:<32} | {m['Trades']:<6} | {m['Win Rate (%)']:>6.1f}%  | {m['Profit Factor']:>5.2f} | ${m['Net P&L ($)']:>8.2f}  | {wl:<12} | {m['Total R']:>7.2f}R", flush=True)


if __name__ == "__main__":
    main()
