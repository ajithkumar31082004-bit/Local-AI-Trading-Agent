"""Audit Remediation Verification: Multi-Period Backtest with 4-Stage Stop Ladder Parity & Full Friction.

Evaluates the 7-Strategy Ensemble:
1. ORB
2. TREND_FOLLOWING (Asymmetric ADX >= 26.0, Continuation Volume >= 1.0x)
3. VWAP_EMA
4. BB_RSI_MEAN_REVERSION
5. ATR_COMPRESSION_BREAKOUT
6. LIQUIDITY_SWEEP
7. SMC_MARKET_STRUCTURE (Sequential 3-state cycle: t_sweep < t_choch < t_bos)

Runs with:
- Strict 4-Stage Stop-Loss Ratchet Parity (0.8R BE, 1.0R -> +0.50R, 1.6R -> +1.10R, 2.2R -> +1.70R)
- Strict Intrabar Causality (SL_i-1 checked prior to excursion update for bar i+1)
- Micro-Lot sizing (1 micro-lot = 0.001 XAUT)
- Realistic Delta Exchange India friction (0.05% fee, 18% GST, 0.02% spread, 0.02% slippage)
- Splits: In-Sample (Aug 5 - Sep 15), Validation (Sep 16 - Oct 15), Out-of-Sample (Oct 16 - Oct 30)
- Full breakdown: Overall, LONG, and SHORT
"""
import sys
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.orb_settings import ORBSettings
from backtest.orb_backtest import run_backtest
from strategy.arbitrator import evaluate_arbitrated_portfolio, arbitrate_strategies
from strategy.orb_ema_volume import evaluate_orb
from strategy.trend_following import evaluate_trend_following
from strategy.smc_market_structure import evaluate_smc_structure


def compute_metrics(res, label: str) -> Dict[str, Any]:
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
            "Long Trades": 0,
            "Long Win %": 0.0,
            "Long P&L ($)": 0.0,
            "Short Trades": 0,
            "Short Win %": 0.0,
            "Short P&L ($)": 0.0,
        }

    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    win_rate = (len(wins) / count) * 100.0

    total_gross_gain = sum(t["pnl_cash"] for t in wins)
    total_gross_loss = abs(sum(t["pnl_cash"] for t in losses))
    pf = (total_gross_gain / total_gross_loss) if total_gross_loss > 0 else (999.0 if total_gross_gain > 0 else 0.0)

    total_r = sum(t["r"] for t in trades)
    exp_r = total_r / count

    longs = [t for t in trades if t.get("direction") == "LONG"]
    shorts = [t for t in trades if t.get("direction") == "SHORT"]
    long_wins = [t for t in longs if t["r"] > 0]
    short_wins = [t for t in shorts if t["r"] > 0]

    long_wr = (len(long_wins) / len(longs) * 100.0) if longs else 0.0
    short_wr = (len(short_wins) / len(shorts) * 100.0) if shorts else 0.0
    long_pnl = sum(t["pnl_cash"] for t in longs)
    short_pnl = sum(t["pnl_cash"] for t in shorts)

    return {
        "Strategy": label,
        "Trades": count,
        "Win Rate (%)": round(win_rate, 2),
        "Net P&L ($)": round(res.net_pnl, 2),
        "Profit Factor": round(pf, 2),
        "Max DD (%)": round(res.max_drawdown_pct, 2),
        "Expectancy (R)": round(exp_r, 4),
        "Total R": round(total_r, 2),
        "Long Trades": len(longs),
        "Long Win %": round(long_wr, 2),
        "Long P&L ($)": round(long_pnl, 2),
        "Short Trades": len(shorts),
        "Short Win %": round(short_wr, 2),
        "Short P&L ($)": round(short_pnl, 2),
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
        "fee_pct_per_side": 0.05,  # Taker friction
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
    }

    splits = {
        "3. Out-of-Sample / Test (Oct 16 - Oct 30)": (df.index >= "2026-10-16") & (df.index <= "2026-10-30 23:59:59+05:30"),
        "2. Validation (Sep 16 - Oct 15)": (df.index >= "2026-09-16") & (df.index <= "2026-10-15 23:59:59+05:30"),
        "1. In-Sample / Dev (Aug 5 - Sep 15)": (df.index >= "2026-08-05") & (df.index <= "2026-09-15 23:59:59+05:30"),
        "Full Period (Aug 5 - Oct 30)": (df.index >= "2026-08-05") & (df.index <= "2026-10-30 23:59:59+05:30"),
    }

    print("=" * 125, flush=True)
    print("AUDIT REMEDIATION EVIDENCE: OUT-OF-SAMPLE WALK-FORWARD BACKTEST", flush=True)
    print("Ensemble: 7 Strategies | Quality Score >= 0.75 | Asymmetric LONG Guard | 4-Stage Stop Ladder Parity", flush=True)
    print("=" * 125, flush=True)

    all_records = []

    for split_name, mask in splits.items():
        sub_df = df[mask].copy()
        days_count = len(dict.fromkeys(sub_df.index.date))
        print(f"\n>>> Split: {split_name} ({len(sub_df):,} bars, {days_count} trading days) <<<", flush=True)

        # Run 1: Baseline Fixed Exits (No Ratchet)
        print("-> Running Baseline (Fixed SL/TP)...", flush=True)
        cfg_baseline = ORBSettings(**base_cfg_kwargs, enable_multi_stage_exits=False, strategy_type="PORTFOLIO")
        res_baseline = run_backtest(sub_df, cfg=cfg_baseline, strategy_fn=evaluate_arbitrated_portfolio, initial_equity=10000.0)
        m_base = compute_metrics(res_baseline, "Portfolio (Baseline Exits)")
        m_base["Split"] = split_name
        all_records.append(m_base)

        # Run 2: Harmonized 4-Stage Stop Ratchet Ladder
        print("-> Running 4-Stage Stop Ratchet Ladder...", flush=True)
        cfg_ratchet = ORBSettings(**base_cfg_kwargs, enable_multi_stage_exits=True, strategy_type="PORTFOLIO")
        res_ratchet = run_backtest(sub_df, cfg=cfg_ratchet, strategy_fn=evaluate_arbitrated_portfolio, initial_equity=10000.0)
        m_ratchet = compute_metrics(res_ratchet, "Portfolio (4-Stage Stop Ladder)")
        m_ratchet["Split"] = split_name
        all_records.append(m_ratchet)

        print(f"{'Configuration':<34} | {'Trades':<6} | {'Win %':<7} | {'PF':<5} | {'Exp (R)':<8} | {'Net P&L ($)':<11} | {'LONG WR% (n)':<14} | {'SHORT WR% (n)':<14}", flush=True)
        print("-" * 125, flush=True)
        for m in (m_base, m_ratchet):
            long_info = f"{m['Long Win %']:.1f}% ({m['Long Trades']})"
            short_info = f"{m['Short Win %']:.1f}% ({m['Short Trades']})"
            print(f"{m['Strategy']:<34} | {m['Trades']:<6} | {m['Win Rate (%)']:>6.1f}% | {m['Profit Factor']:>5.2f} | {m['Expectancy (R)']:>8.4f} | ${m['Net P&L ($)']:>9.2f} | {long_info:<14} | {short_info:<14}", flush=True)

        # Incrementally save after each completed split
        out_csv = ROOT / "data" / "audit_remediation_backtest_summary.csv"
        pd.DataFrame(all_records).to_csv(out_csv, index=False)
        print(f"[PROGRESS] Updated {out_csv} with {len(all_records)} summary rows.\n", flush=True)

    out_csv = ROOT / "data" / "audit_remediation_backtest_summary.csv"
    pd.DataFrame(all_records).to_csv(out_csv, index=False)
    print(f"\n[SUCCESS] Audit backtest summary saved to: {out_csv}")


if __name__ == "__main__":
    main()
