"""Phase 1: Deep Quantitative Audit of Multi-Strategy Trades.

Analyzes data/multi_strategy_all_trades.csv and crosses with data/xautusd_multi_month_5m.csv
to compute MFE, MAE, holding periods, exit diagnostics, fee impacts, and regime correlations.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime.market_regime import detect_market_regime
from config.orb_settings import ORBSettings

def main():
    trades_path = ROOT / "data" / "multi_strategy_all_trades.csv"
    candles_path = ROOT / "data" / "xautusd_multi_month_5m.csv"

    if not trades_path.exists() or not candles_path.exists():
        print("Required files not found!")
        return

    df_trades = pd.read_csv(trades_path)
    df_candles = pd.read_csv(candles_path, index_col=0, parse_dates=True)
    if df_candles.index.tz is None:
        df_candles.index = df_candles.index.tz_localize("Asia/Kolkata")
    else:
        df_candles.index = df_candles.index.tz_convert("Asia/Kolkata")
    df_candles = df_candles.sort_index()

    # Filter to Full Period runs for the 7 strategies
    fp_trades = df_trades[df_trades["split"] == "Full Period"].copy()
    fp_trades["entry_time"] = pd.to_datetime(fp_trades["entry_time"])
    fp_trades["exit_time"] = pd.to_datetime(fp_trades["exit_time"])
    fp_trades["duration_mins"] = (fp_trades["exit_time"] - fp_trades["entry_time"]).dt.total_seconds() / 60.0

    print("=" * 90)
    print("PHASE 1: EXISTING TRADES AUDIT (FULL PERIOD - 250 TRADES)")
    print("=" * 90)

    # 1. Long vs Short Breakdown
    print("\n--- 1. DIRECTIONAL PERFORMANCE (LONG vs SHORT) ---")
    for d in ["LONG", "SHORT"]:
        sub = fp_trades[fp_trades["direction"] == d]
        wins = sub[sub["r"] > 0]
        losses = sub[sub["r"] <= 0]
        gross_pnl = sub["gross_pnl"].sum()
        friction = sub["total_friction"].sum()
        net_pnl = sub["pnl_cash"].sum()
        total_r = sub["r"].sum()
        wr = (len(wins) / len(sub) * 100) if len(sub) > 0 else 0
        pf = (wins["pnl_cash"].sum() / abs(losses["pnl_cash"].sum())) if len(losses) > 0 and abs(losses["pnl_cash"].sum()) > 0 else 0
        avg_dur = sub["duration_mins"].mean()
        print(f"Direction: {d:<5} | Count: {len(sub):<3} | Win Rate: {wr:>5.1f}% | Gross: ${gross_pnl:>8.2f} | Friction: ${friction:>6.2f} | Net: ${net_pnl:>8.2f} | Tot R: {total_r:>6.2f} | PF: {pf:>4.2f} | Avg Dur: {avg_dur:>5.1f}m")

    # 2. Exit Reason Breakdown
    print("\n--- 2. EXIT REASON BREAKDOWN ---")
    exit_grp = fp_trades.groupby(["direction", "exit_reason"]).agg(
        count=("trade_id", "count"),
        net_pnl=("pnl_cash", "sum"),
        total_r=("r", "sum"),
        avg_duration=("duration_mins", "mean")
    ).reset_index()
    for _, row in exit_grp.iterrows():
        print(f"Dir: {row['direction']:<5} | Exit: {row['exit_reason']:<12} | Trades: {row['count']:<3} | Net P&L: ${row['net_pnl']:>8.2f} | Total R: {row['total_r']:>6.2f} | Avg Dur: {row['avg_duration']:>5.1f}m")

    # 3. Strategy by Strategy Breakdown
    print("\n--- 3. STRATEGY COMPARISON (GROSS vs FRICTION vs NET) ---")
    strat_grp = fp_trades.groupby("run_strategy").agg(
        count=("trade_id", "count"),
        gross_pnl=("gross_pnl", "sum"),
        friction=("total_friction", "sum"),
        net_pnl=("pnl_cash", "sum"),
        avg_r=("r", "mean"),
        win_rate=("r", lambda s: (s > 0).mean() * 100),
        avg_win=("pnl_cash", lambda s: s[s > 0].mean() if (s > 0).any() else 0),
        avg_loss=("pnl_cash", lambda s: s[s <= 0].mean() if (s <= 0).any() else 0),
    ).reset_index()
    for _, r in strat_grp.iterrows():
        print(f"{r['run_strategy']:<42} | Trades: {r['count']:<2} | Win: {r['win_rate']:>5.1f}% | Gross: ${r['gross_pnl']:>7.2f} | Fric: ${r['friction']:>6.2f} | Net: ${r['net_pnl']:>7.2f} | Avg R: {r['avg_r']:>7.4f}")

    # 4. Excursion Analysis (MFE / MAE)
    print("\n--- 4. EXCURSION ANALYSIS (MFE & MAE IN R-MULTIPLES) ---")
    mfes = []
    maes = []
    regimes_at_entry = []
    givebacks = [] # Trades that were in >= 1.0R profit but stopped out or closed at loss

    for _, row in fp_trades.iterrows():
        e_time = row["entry_time"]
        x_time = row["exit_time"]
        side = row["direction"]
        entry = row["entry"]
        sl = row["sl"]
        risk = abs(entry - sl)
        if risk == 0:
            risk = 1.0

        trade_bars = df_candles[(df_candles.index >= e_time) & (df_candles.index <= x_time)]
        if len(trade_bars) == 0:
            mfes.append(0.0)
            maes.append(0.0)
            regimes_at_entry.append("UNKNOWN")
            continue

        # Market regime at entry bar
        prefix = df_candles[df_candles.index <= e_time]
        reg_res = detect_market_regime(prefix)
        regimes_at_entry.append(reg_res.regime.value)

        if side == "LONG":
            max_p = trade_bars["High"].max()
            min_p = trade_bars["Low"].min()
            mfe_r = (max_p - entry) / risk
            mae_r = (entry - min_p) / risk
        else:
            max_p = trade_bars["High"].max()
            min_p = trade_bars["Low"].min()
            mfe_r = (entry - min_p) / risk
            mae_r = (max_p - entry) / risk

        mfes.append(mfe_r)
        maes.append(mae_r)

        if mfe_r >= 1.0 and row["r"] <= 0:
            givebacks.append(row["trade_id"])

    fp_trades["mfe_r"] = mfes
    fp_trades["mae_r"] = maes
    fp_trades["regime_at_entry"] = regimes_at_entry

    winners = fp_trades[fp_trades["r"] > 0]
    losers = fp_trades[fp_trades["r"] <= 0]

    print(f"Winners (n={len(winners)}): Avg MFE = {winners['mfe_r'].mean():.2f}R | Avg MAE = {winners['mae_r'].mean():.2f}R | Avg Duration = {winners['duration_mins'].mean():.1f}m")
    print(f"Losers  (n={len(losers)}): Avg MFE = {losers['mfe_r'].mean():.2f}R | Avg MAE = {losers['mae_r'].mean():.2f}R | Avg Duration = {losers['duration_mins'].mean():.1f}m")
    print(f"Severe Givebacks (reached >= +1.0R but exited at loss/scratch): {len(givebacks)} trades ({len(givebacks)/len(losers)*100:.1f}% of all losers)")

    # 5. Performance by Market Regime at Entry
    print("\n--- 5. PERFORMANCE BY MARKET REGIME AT ENTRY ---")
    reg_summary = fp_trades.groupby("regime_at_entry").agg(
        trades=("trade_id", "count"),
        win_rate=("r", lambda s: (s > 0).mean() * 100),
        net_pnl=("pnl_cash", "sum"),
        total_r=("r", "sum"),
        profit_factor=("pnl_cash", lambda s: (s[s > 0].sum() / abs(s[s <= 0].sum())) if (s <= 0).sum() < 0 else 999.0)
    ).reset_index()
    for _, row in reg_summary.iterrows():
        print(f"Regime: {row['regime_at_entry']:<18} | Trades: {row['trades']:<3} | Win Rate: {row['win_rate']:>5.1f}% | Net P&L: ${row['net_pnl']:>8.2f} | Total R: {row['total_r']:>6.2f} | PF: {row['profit_factor']:>4.2f}")

    # Save detailed audit tables
    fp_trades.to_csv(ROOT / "data" / "phase1_trades_audit_with_excursions.csv", index=False)
    print(f"\n[SUCCESS] Detailed trade excursion audit saved to: data/phase1_trades_audit_with_excursions.csv")

if __name__ == "__main__":
    main()

