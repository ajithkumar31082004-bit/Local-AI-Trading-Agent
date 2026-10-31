"""Chronological Walk-Forward Evaluation for XAUTUSD Market-Regime-Aware Strategy.

Splits:
1. Dev / In-Sample: Aug 5, 2026 - Sep 15, 2026
2. Validation:     Sep 16, 2026 - Oct 15, 2026
3. Out-of-Sample:  Oct 16, 2026 - Oct 30, 2026
4. Full Period:    Aug 5, 2026 - Oct 30, 2026

Models:
- Model A: Baseline Trend-Following (use_regime_filter=False)
- Model B: Trend-Following + Market-Regime Filter (use_regime_filter=True, ADX >= 22, EMA separation >= 0.3 ATR)
- Model C: No-Trade Baseline
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from typing import Dict, Any, List
from config.orb_settings import ORBSettings
from backtest.orb_backtest import run_backtest
from strategy.trend_following import evaluate_trend_following


def compute_detailed_metrics(res, label: str) -> Dict[str, Any]:
    trades = res.trades if hasattr(res, "trades") and isinstance(res.trades, list) else res.trade_log
    count = len(trades)
    if count == 0:
        return {
            "model": label,
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "total_r": 0.0,
            "net_pnl": 0.0,
            "max_dd_pct": 0.0,
            "avg_win_cash": 0.0,
            "avg_loss_cash": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
        }

    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    win_rate = (len(wins) / count) * 100.0

    total_gross_gain = sum(t["pnl_cash"] for t in wins)
    total_gross_loss = abs(sum(t["pnl_cash"] for t in losses))
    if total_gross_loss > 0:
        pf = total_gross_gain / total_gross_loss
    else:
        pf = 999.0 if total_gross_gain > 0 else 0.0

    avg_win_cash = (total_gross_gain / len(wins)) if wins else 0.0
    avg_loss_cash = (sum(t["pnl_cash"] for t in losses) / len(losses)) if losses else 0.0
    avg_win_r = (sum(t["r"] for t in wins) / len(wins)) if wins else 0.0
    avg_loss_r = (sum(t["r"] for t in losses) / len(losses)) if losses else 0.0

    total_r = sum(t["r"] for t in trades)
    expectancy_r = total_r / count

    return {
        "model": label,
        "trades": count,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(pf, 2),
        "expectancy_r": round(expectancy_r, 4),
        "total_r": round(total_r, 2),
        "net_pnl": round(res.net_pnl, 2),
        "max_dd_pct": round(res.max_drawdown_pct, 2),
        "avg_win_cash": round(avg_win_cash, 2),
        "avg_loss_cash": round(avg_loss_cash, 2),
        "avg_win_r": round(avg_win_r, 4),
        "avg_loss_r": round(avg_loss_r, 4),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
    }


def run_walk_forward():
    csv_path = ROOT / "data" / "xautusd_multi_month_5m.csv"
    if not csv_path.exists():
        print(f"Dataset not found at {csv_path}")
        return

    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("Asia/Kolkata")
    else:
        df.index = df.index.tz_convert("Asia/Kolkata")
    df = df.sort_index()

    splits = {
        "1. Dev / In-Sample (Aug 5 - Sep 15)": (df.index >= "2026-08-05") & (df.index <= "2026-09-15 23:59:59+05:30"),
        "2. Validation (Sep 16 - Oct 15)": (df.index >= "2026-09-16") & (df.index <= "2026-10-15 23:59:59+05:30"),
        "3. Out-of-Sample / Test (Oct 16 - Oct 30)": (df.index >= "2026-10-16") & (df.index <= "2026-10-30 23:59:59+05:30"),
        "4. Full Period (Aug 5 - Oct 30)": (df.index >= "2026-08-05") & (df.index <= "2026-10-30 23:59:59+05:30"),
    }

    base_kwargs = {
        "symbol": "XAUTUSD",
        "strategy_type": "TREND_FOLLOWING",
        "max_trades_per_day": 2,
        "cooldown_bars_after_loss": 6,
        "daily_loss_limit_r": 2.0,
        "no_entry_minutes_after_open": 45,
        "last_entry_time": "22:00",
        "session_start": "09:00",
        "session_end": "23:30",
        "risk_pct": 0.5,
        "max_margin_pct_equity": 10.0,
        "leverage": 2.0,
        "fee_pct_per_side": 0.01,
        "spread_pct_round_trip": 0.02,
        "slippage_pct_round_trip": 0.02,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
    }

    cfg_a = ORBSettings(**base_kwargs, use_regime_filter=False)
    cfg_b = ORBSettings(
        **base_kwargs,
        use_regime_filter=True,
        min_15m_adx=22.0,
        min_ema_separation_atr=0.3,
        min_stop_distance_atr=1.0,
        max_friction_to_risk_ratio=0.25,
    )

    print("=" * 100)
    print("CHRONOLOGICAL WALK-FORWARD EXPERIMENT: XAUTUSD TREND-FOLLOWING")
    print("=" * 100)

    all_results = []

    for split_name, mask in splits.items():
        sub_df = df[mask].copy()
        print(f"\n--- {split_name} (Bars: {len(sub_df):,}, Days: {len(dict.fromkeys(sub_df.index.date))}) ---")

        # Model A: Baseline
        res_a = run_backtest(sub_df, cfg=cfg_a, initial_equity=10000.0)
        m_a = compute_detailed_metrics(res_a, "Model A (Baseline)")
        m_a["split"] = split_name
        all_results.append(m_a)

        # Model B: Regime Filter
        res_b = run_backtest(sub_df, cfg=cfg_b, initial_equity=10000.0)
        m_b = compute_detailed_metrics(res_b, "Model B (+Regime Filter)")
        m_b["split"] = split_name
        all_results.append(m_b)

        # Model C: No-trade
        m_c = {
            "model": "Model C (No-Trade)",
            "split": split_name,
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "total_r": 0.0,
            "net_pnl": 0.0,
            "max_dd_pct": 0.0,
            "avg_win_cash": 0.0,
            "avg_loss_cash": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
        }
        all_results.append(m_c)

        print(f"{'Model':<26} | {'Trades':<6} | {'Win Rate':<8} | {'PF':<5} | {'Exp (R)':<8} | {'Total R':<8} | {'Net P&L ($)':<12} | {'Max DD':<7}")
        print("-" * 100)
        for m in [m_a, m_b, m_c]:
            print(f"{m['model']:<26} | {m['trades']:<6} | {m['win_rate']:>7.2f}% | {m['profit_factor']:>5.2f} | {m['expectancy_r']:>8.4f} | {m['total_r']:>8.2f} | ${m['net_pnl']:>10.2f} | {m['max_dd_pct']:>6.2f}%")

    # Format into comprehensive DataFrame
    df_res = pd.DataFrame(all_results)
    out_csv = ROOT / "data" / "walk_forward_regime_results.csv"
    df_res.to_csv(out_csv, index=False)
    print(f"\nDetailed walk-forward results saved to: {out_csv}")


if __name__ == "__main__":
    run_walk_forward()

