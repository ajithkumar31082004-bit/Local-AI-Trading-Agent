"""Comparative backtester for Baseline vs Proposed Rules on XAUTUSD historical datasets."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import replace
import pandas as pd
from config.orb_settings import ORBSettings, settings
from backtest.orb_backtest import run_backtest, ORBBacktestResult
from strategy.trend_following import evaluate_trend_following
from data.csv_loader import load_candles_csv


def run_comparison(csv_path: str, label: str):
    print("=" * 80)
    print(f"RUNNING COMPARISON ON DATASET: {label} ({csv_path})")
    print("=" * 80)
    
    df = load_candles_csv(csv_path, settings.timezone)
    print(f"Candles loaded: {len(df):,} | Date range: {df.index[0]} to {df.index[-1]}")
    
    # 1. Baseline Rules:
    # - max_trades_per_day: 1
    # - no_entry_minutes_after_open: 0
    # - last_entry_time: "23:30"
    # - cooldown_bars_after_loss: 0
    # - daily_loss_limit_r: 10.0
    cfg_base = ORBSettings(
        symbol="XAUTUSD",
        timezone="Asia/Kolkata",
        session_start="09:00",
        session_end="23:30",
        no_entry_minutes_after_open=0,
        last_entry_time="23:30",
        max_trades_per_day=1,
        cooldown_bars_after_loss=0,
        daily_loss_limit_r=10.0,
        risk_pct=0.5,
    )
    
    # 2. Proposed Rules:
    # - max_trades_per_day: 2
    # - no_entry_minutes_after_open: 45 (cutoff 09:45)
    # - last_entry_time: "22:00"
    # - cooldown_bars_after_loss: 6 (30 minutes)
    # - daily_loss_limit_r: 2.0
    cfg_prop = ORBSettings(
        symbol="XAUTUSD",
        timezone="Asia/Kolkata",
        session_start="09:00",
        session_end="23:30",
        no_entry_minutes_after_open=45,
        last_entry_time="22:00",
        max_trades_per_day=2,
        cooldown_bars_after_loss=6,
        daily_loss_limit_r=2.0,
        risk_pct=0.5,
    )
    
    res_base = run_backtest(df, initial_equity=10000.0, cfg=cfg_base, chart_output_path=None, strategy_fn=evaluate_trend_following)
    res_prop = run_backtest(df, initial_equity=10000.0, cfg=cfg_prop, chart_output_path=None, strategy_fn=evaluate_trend_following)
    
    def print_metrics(name: str, res: ORBBacktestResult):
        wins = [t for t in res.trade_log if t["r"] > 0]
        losses = [t for t in res.trade_log if t["r"] < 0]
        avg_win_r = (sum(t["r"] for t in wins) / len(wins)) if wins else 0.0
        avg_loss_r = (sum(t["r"] for t in losses) / len(losses)) if losses else 0.0
        avg_win_cash = (sum(t["pnl_cash"] for t in wins) / len(wins)) if wins else 0.0
        avg_loss_cash = (sum(t["pnl_cash"] for t in losses) / len(losses)) if losses else 0.0
        
        print(f"\n--- {name} ---")
        print(f"Total Days:                {res.days}")
        print(f"Total Trades:              {res.trades}")
        print(f"Winning Trades:            {len(wins)}")
        print(f"Losing Trades:             {len(losses)}")
        print(f"Win Rate:                  {res.win_rate_pct:.2f}%")
        print(f"Average R:                 {res.average_r:+.4f}R")
        print(f"Profit Factor:             {res.profit_factor:.2f}")
        print(f"Max Drawdown:              {res.max_drawdown_pct:.2f}%")
        print(f"Total Realized R:          {res.total_r:+.4f}R")
        print(f"Net P&L:                   ${res.net_pnl:+,.2f}")
        print(f"Avg Win:                   {avg_win_r:+.2f}R (${avg_win_cash:+,.2f})")
        print(f"Avg Loss:                  {avg_loss_r:+.2f}R (${avg_loss_cash:+,.2f})")
        print("Trade Log:")
        for idx, t in enumerate(res.trade_log, 1):
            print(f"  #{idx:02d} {t['date']} {t['direction']:5s} In: {t['entry_time']} @ {t['entry']:.2f} | Out: {t['exit_time']} @ {t['exit']:.2f} | {t['exit_reason']:11s} | {t['r']:+.2f}R | ${t['pnl_cash']:+.2f}")
        import sys
        sys.stdout.flush()

    print_metrics("BASELINE RULES (1 Trade/Day, No Open/Late Filter)", res_base)
    print_metrics("PROPOSED RULES (2 Trades/Day, 45m Open Filter, 22:00 Cutoff, Cooldown)", res_prop)
    return res_base, res_prop


if __name__ == "__main__":
    # 1. Oct 27-30
    run_comparison("data/xautusd_historical_5m.csv", "OCT 27-30 FOCUSED (Includes Oct 30 Crash)")
    # 2. Multi-Month
    run_comparison("data/xautusd_multi_month_5m.csv", "MULTI-MONTH AUG 04 - OCT 30 (25k Candles)")
