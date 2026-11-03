"""Comprehensive Multi-Strategy Portfolio Backtest and Chronological Walk-Forward Validation.

Dataset: data/xautusd_multi_month_5m.csv (Aug 5 - Oct 30, 2026, 24,999 completed 5m candles).
Evaluates:
- Individual Strategies:
  1. ORB (Opening Range Breakout)
  2. Trend-Following (EMA20/EMA50 + Pullback)
  3. VWAP + EMA Confirmation
  4. Bollinger Bands + RSI Mean Reversion
  5. ATR Volatility Compression Breakout
  6. Liquidity Sweep Reversal
- Combined Portfolio (Arbitrator + Market Regime Detector)
- Existing 2-Strategy Baseline vs Upgraded Multi-Strategy Engine

Outputs comprehensive metrics, CSV reports, and regime breakdowns.
"""
import sys
from pathlib import Path
from typing import Dict, Any, List
import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.orb_settings import ORBSettings
from backtest.orb_backtest import run_backtest
from strategy.orb_ema_volume import evaluate_orb
from strategy.trend_following import evaluate_trend_following
from strategy.vwap_ema import evaluate_vwap_ema
from strategy.bb_rsi_mean_reversion import evaluate_bb_rsi
from strategy.atr_compression_breakout import evaluate_atr_compression_breakout
from strategy.liquidity_sweep import evaluate_liquidity_sweep
from strategy.arbitrator import evaluate_arbitrated_portfolio
from regime.market_regime import detect_market_regime


def compute_metrics(res, label: str, df_candles: pd.DataFrame) -> Dict[str, Any]:
    trades = res.trades if hasattr(res, "trades") and isinstance(res.trades, list) else res.trade_log
    count = len(trades)
    if count == 0:
        return {
            "Strategy": label,
            "Trades": 0,
            "Win Rate (%)": 0.0,
            "Net P&L ($)": 0.0,
            "Profit Factor": 0.0,
            "Max DD (%)": 0.0,
            "Expectancy (R)": 0.0,
            "Total R": 0.0,
            "Avg Win ($)": 0.0,
            "Avg Loss ($)": 0.0,
            "Sharpe Ratio": 0.0,
            "Long Trades": 0,
            "Long Win Rate (%)": 0.0,
            "Long P&L ($)": 0.0,
            "Short Trades": 0,
            "Short Win Rate (%)": 0.0,
            "Short P&L ($)": 0.0,
            "Regime Breakdown": {},
        }

    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    win_rate = (len(wins) / count) * 100.0

    total_gross_gain = sum(t["pnl_cash"] for t in wins)
    total_gross_loss = abs(sum(t["pnl_cash"] for t in losses))
    pf = (total_gross_gain / total_gross_loss) if total_gross_loss > 0 else (999.0 if total_gross_gain > 0 else 0.0)

    avg_win = (total_gross_gain / len(wins)) if wins else 0.0
    avg_loss = (sum(t["pnl_cash"] for t in losses) / len(losses)) if losses else 0.0
    total_r = sum(t["r"] for t in trades)
    exp_r = total_r / count

    # Long vs Short
    longs = [t for t in trades if t.get("direction") == "LONG"]
    shorts = [t for t in trades if t.get("direction") == "SHORT"]

    long_wins = [t for t in longs if t["r"] > 0]
    short_wins = [t for t in shorts if t["r"] > 0]

    long_wr = (len(long_wins) / len(longs) * 100.0) if longs else 0.0
    short_wr = (len(short_wins) / len(shorts) * 100.0) if shorts else 0.0

    long_pnl = sum(t["pnl_cash"] for t in longs)
    short_pnl = sum(t["pnl_cash"] for t in shorts)

    # Sharpe Ratio from daily returns
    daily_pnls: Dict[str, float] = {}
    for t in trades:
        d = t["date"]
        daily_pnls[d] = daily_pnls.get(d, 0.0) + t["pnl_cash"]
    
    daily_rets = pd.Series(list(daily_pnls.values()))
    if len(daily_rets) > 1 and daily_rets.std() > 0:
        sharpe = (daily_rets.mean() / daily_rets.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Regime breakdown (sample at entry times)
    regime_stats: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        entry_t = pd.to_datetime(t["entry_time"])
        prefix = df_candles[df_candles.index <= entry_t]
        reg_res = detect_market_regime(prefix)
        reg_name = reg_res.regime.value
        if reg_name not in regime_stats:
            regime_stats[reg_name] = {"trades": 0, "wins": 0, "pnl": 0.0}
        regime_stats[reg_name]["trades"] += 1
        if t["r"] > 0:
            regime_stats[reg_name]["wins"] += 1
        regime_stats[reg_name]["pnl"] += t["pnl_cash"]

    return {
        "Strategy": label,
        "Trades": count,
        "Win Rate (%)": round(win_rate, 2),
        "Net P&L ($)": round(res.net_pnl, 2),
        "Profit Factor": round(pf, 2),
        "Max DD (%)": round(res.max_drawdown_pct, 2),
        "Expectancy (R)": round(exp_r, 4),
        "Total R": round(total_r, 2),
        "Avg Win ($)": round(avg_win, 2),
        "Avg Loss ($)": round(avg_loss, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Long Trades": len(longs),
        "Long Win Rate (%)": round(long_wr, 2),
        "Long P&L ($)": round(long_pnl, 2),
        "Short Trades": len(shorts),
        "Short Win Rate (%)": round(short_wr, 2),
        "Short P&L ($)": round(short_pnl, 2),
        "Regime Breakdown": regime_stats,
    }


def main():
    csv_path = ROOT / "data" / "xautusd_multi_month_5m.csv"
    if not csv_path.exists():
        print(f"Data file not found: {csv_path}")
        return

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
        "max_margin_pct_equity": 10.0,
        "leverage": 2.0,
        "fee_pct_per_side": 0.01,
        "spread_pct_round_trip": 0.02,
        "slippage_pct_round_trip": 0.02,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
    }

    strategies = [
        ("1. ORB Baseline", evaluate_orb, "ORB"),
        ("2. Trend-Following Baseline", evaluate_trend_following, "TREND_FOLLOWING"),
        ("3. VWAP + EMA Confirmation", evaluate_vwap_ema, "VWAP_EMA"),
        ("4. Bollinger + RSI Mean Reversion", evaluate_bb_rsi, "BB_RSI"),
        ("5. ATR Compression Breakout", evaluate_atr_compression_breakout, "ATR_BREAKOUT"),
        ("6. Liquidity Sweep Reversal", evaluate_liquidity_sweep, "LIQUIDITY_SWEEP"),
        ("7. Upgraded Multi-Strategy Arbitrated Portfolio", evaluate_arbitrated_portfolio, "PORTFOLIO"),
    ]

    splits = {
        "Full Period (Aug 5 - Oct 30)": (df.index >= "2026-08-05") & (df.index <= "2026-10-30 23:59:59+05:30"),
        "1. In-Sample / Dev (Aug 5 - Sep 15)": (df.index >= "2026-08-05") & (df.index <= "2026-09-15 23:59:59+05:30"),
        "2. Validation (Sep 16 - Oct 15)": (df.index >= "2026-09-16") & (df.index <= "2026-10-15 23:59:59+05:30"),
        "3. Out-of-Sample / Test (Oct 16 - Oct 30)": (df.index >= "2026-10-16") & (df.index <= "2026-10-30 23:59:59+05:30"),
    }

    print("=" * 110)
    print("XAUTUSD MULTI-STRATEGY INTELLIGENCE ENGINE: COMPREHENSIVE BACKTEST REPORT")
    print("=" * 110)

    summary_records = []
    all_trade_records = []

    # Run Full Period comparison first
    full_mask = splits["Full Period (Aug 5 - Oct 30)"]
    full_df = df[full_mask].copy()

    print(f"\n--- FULL DATASET REPLAY (24,999 bars, ~87 trading days) ---")
    print(f"{'Strategy':<38} | {'Trades':<6} | {'Win %':<6} | {'PF':<5} | {'Exp (R)':<8} | {'Net P&L ($)':<12} | {'Max DD':<7} | {'Sharpe':<6}")
    print("-" * 110)

    for strat_label, strat_fn, strat_type in strategies:
        print(f"-> Running {strat_label}...", flush=True)
        cfg = ORBSettings(**base_cfg_kwargs, strategy_type=strat_type)
        res = run_backtest(full_df, cfg=cfg, strategy_fn=strat_fn, initial_equity=10000.0)
        m = compute_metrics(res, strat_label, full_df)
        m["Split"] = "Full Period"
        summary_records.append(m)

        trades_list = res.trades if hasattr(res, "trades") and isinstance(res.trades, list) else res.trade_log
        for idx_t, t in enumerate(trades_list, start=1):
            rec = dict(t)
            rec["trade_id"] = f"{strat_type}_FP_{idx_t:03d}"
            rec["split"] = "Full Period"
            rec["run_strategy"] = strat_label
            all_trade_records.append(rec)

        print(f"   {m['Strategy']:<38} | {m['Trades']:<6} | {m['Win Rate (%)']:>5.1f}% | {m['Profit Factor']:>5.2f} | {m['Expectancy (R)']:>8.4f} | ${m['Net P&L ($)']:>10.2f} | {m['Max DD (%)']:>6.2f}% | {m['Sharpe Ratio']:>6.2f}\n", flush=True)

    # Run Walk-Forward Splits for the Arbitrated Portfolio vs Baseline
    print("\n" + "=" * 110)
    print("CHRONOLOGICAL WALK-FORWARD ANALYSIS: ARBITRATED PORTFOLIO VS BASELINES")
    print("=" * 110)

    wf_strategies = [
        ("Existing ORB Baseline", evaluate_orb, "ORB"),
        ("Existing Trend-Following Baseline", evaluate_trend_following, "TREND_FOLLOWING"),
        ("Upgraded Multi-Strategy Engine", evaluate_arbitrated_portfolio, "PORTFOLIO"),
    ]

    for split_name, mask in splits.items():
        if split_name == "Full Period (Aug 5 - Oct 30)":
            continue
        sub_df = df[mask].copy()
        days_count = len(dict.fromkeys(sub_df.index.date))
        print(f"\n--- Split: {split_name} ({len(sub_df):,} candles, {days_count} days) ---")
        print(f"{'Strategy':<36} | {'Trades':<6} | {'Win %':<6} | {'PF':<5} | {'Exp (R)':<8} | {'Net P&L ($)':<12} | {'Max DD':<7}")
        print("-" * 105)

        for s_label, s_fn, s_type in wf_strategies:
            print(f"-> Running {s_label}...", flush=True)
            cfg = ORBSettings(**base_cfg_kwargs, strategy_type=s_type)
            res = run_backtest(sub_df, cfg=cfg, strategy_fn=s_fn, initial_equity=10000.0)
            m = compute_metrics(res, s_label, sub_df)
            m["Split"] = split_name
            summary_records.append(m)

            trades_list = res.trades if hasattr(res, "trades") and isinstance(res.trades, list) else res.trade_log
            split_tag = split_name.split(".")[0].strip()
            for idx_t, t in enumerate(trades_list, start=1):
                rec = dict(t)
                rec["trade_id"] = f"{s_type}_S{split_tag}_{idx_t:03d}"
                rec["split"] = split_name
                rec["run_strategy"] = s_label
                all_trade_records.append(rec)

            print(f"   {m['Strategy']:<36} | {m['Trades']:<6} | {m['Win Rate (%)']:>5.1f}% | {m['Profit Factor']:>5.2f} | {m['Expectancy (R)']:>8.4f} | ${m['Net P&L ($)']:>10.2f} | {m['Max DD (%)']:>6.2f}%\n", flush=True)

    # Save summary dataframe
    out_df = pd.DataFrame(summary_records)
    out_csv = ROOT / "data" / "multi_strategy_backtest_summary.csv"
    out_df.drop(columns=["Regime Breakdown"], errors="ignore").to_csv(out_csv, index=False)
    print(f"\n[SUCCESS] Multi-Strategy Backtest Report saved to: {out_csv}")

    # Save trade-by-trade CSV
    if all_trade_records:
        trades_df = pd.DataFrame(all_trade_records)
        trades_csv = ROOT / "data" / "multi_strategy_all_trades.csv"
        trades_df.to_csv(trades_csv, index=False)
        print(f"[SUCCESS] Trade-by-trade audit ({len(trades_df)} trades) saved to: {trades_csv}")


if __name__ == "__main__":
    main()

