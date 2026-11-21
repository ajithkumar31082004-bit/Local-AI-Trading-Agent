"""Phase 4: Independent Exit Model Simulation & Comparison.

Replays completed trades candle-by-candle with identical entry points under:
- Model A: Baseline (Fixed SL 1.0R, Fixed TP 2.0R, Session Close)
- Model B: Breakeven Stop (Move SL to entry once candle reaches +1.0R MFE)
- Model C: Chandelier / ATR Trailing Stop (Trail behind swing extreme by 1.5 ATR after +1.0R)
- Model D: Partial Profit Taking (Scale out 50% at +1.0R, move stop to breakeven for remainder)
- Model E: Time-Decay Stale Exit (Exit after 18 bars / 90 mins if stagnant / negative)

Includes realistic exchange friction on all executions.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.orb_settings import ORBSettings
from risk.position_sizing import estimate_lot_risk

def simulate_trade_exits(trade_row, df_candles, cfg):
    e_time = pd.to_datetime(trade_row["entry_time"])
    side = trade_row["direction"]
    entry = float(trade_row["entry"])
    orig_sl = float(trade_row["sl"])
    orig_tp = float(trade_row["tp"])
    lots = int(trade_row["lots"])
    risk_r = abs(entry - orig_sl)
    if risk_r == 0:
        risk_r = 1.0

    # Candle slice for this day starting right after entry bar
    day_str = str(e_time.date())
    day_candles = df_candles[df_candles.index.strftime("%Y-%m-%d") == day_str]
    trade_candles = day_candles[day_candles.index > e_time]

    if trade_candles.empty:
        return {}

    # Pre-calculate unit friction
    _, fee_per_lot, spread_per_lot = estimate_lot_risk(entry, orig_sl, cfg)
    friction_per_lot = fee_per_lot + spread_per_lot
    total_friction = friction_per_lot * lots
    risk_budget = 10000.0 * (cfg.risk_pct / 100.0)

    results = {}

    for model_name in ["Model A: Baseline", "Model B: Breakeven at +1.0R", "Model C: ATR Trailing Stop", "Model D: Partial Scale (50% at 1R)", "Model E: Time-Decay 90m"]:
        sl = orig_sl
        tp = orig_tp
        active_lots = lots
        scale_pnl = 0.0
        scaled_out = False
        reached_1r = False
        exit_price = None
        exit_reason = None
        exit_ts = None

        for bar_idx, (ts, bar) in enumerate(trade_candles.iterrows(), start=1):
            h = float(bar["High"])
            l = float(bar["Low"])
            c = float(bar["Close"])
            atr_val = float(bar.get("ATR14", 5.0)) if "ATR14" in bar else 5.0

            # Check if +1.0R reached
            current_favorable = (h - entry) if side == "LONG" else (entry - l)
            if current_favorable >= risk_r:
                reached_1r = True

            # Model-specific SL/TP adjustments
            if model_name == "Model B: Breakeven at +1.0R":
                if reached_1r:
                    sl = entry # Move SL to breakeven

            elif model_name == "Model C: ATR Trailing Stop":
                if reached_1r:
                    trail_level = (h - 1.5 * atr_val) if side == "LONG" else (l + 1.5 * atr_val)
                    if side == "LONG":
                        sl = max(sl, trail_level)
                    else:
                        sl = min(sl, trail_level)

            elif model_name == "Model D: Partial Scale (50% at 1R)":
                if reached_1r and not scaled_out:
                    scale_lots = active_lots // 2
                    if scale_lots > 0:
                        scale_price = (entry + risk_r) if side == "LONG" else (entry - risk_r)
                        scale_delta = (scale_price - entry) if side == "LONG" else (entry - scale_price)
                        scale_gross = scale_lots * cfg.contract_size_xaut * scale_delta
                        scale_fric = friction_per_lot * scale_lots
                        scale_pnl = scale_gross - scale_fric
                        active_lots -= scale_lots
                        scaled_out = True
                        sl = entry # Move remainder to breakeven

            elif model_name == "Model E: Time-Decay 90m":
                if bar_idx >= 18 and not reached_1r:
                    # If after 90m (18 bars) trade has not hit +1R and is underwater, cut it
                    underwater = (c < entry) if side == "LONG" else (c > entry)
                    if underwater:
                        exit_price = c
                        exit_reason = "TIME_DECAY"
                        exit_ts = ts
                        break

            # Check SL / TP collisions
            hit_sl = (l <= sl) if side == "LONG" else (h >= sl)
            hit_tp = (h >= tp) if side == "LONG" else (l <= tp)

            if hit_sl or hit_tp:
                if hit_sl:
                    exit_price = sl
                    exit_reason = "SL"
                else:
                    exit_price = tp
                    exit_reason = "TP"
                exit_ts = ts
                break

            # Session End close (last bar of day)
            if bar_idx == len(trade_candles):
                exit_price = c
                exit_reason = "SESSION_END"
                exit_ts = ts
                break

        # Compute P&L for active lots
        delta = (exit_price - entry) if side == "LONG" else (entry - exit_price)
        rem_gross = active_lots * cfg.contract_size_xaut * delta
        rem_fric = friction_per_lot * active_lots
        final_pnl = rem_gross - rem_fric + scale_pnl
        net_r = final_pnl / risk_budget if risk_budget > 0 else 0.0

        results[model_name] = {
            "pnl": final_pnl,
            "r": net_r,
            "exit_reason": exit_reason,
            "exit_price": exit_price,
        }

    return results

def main():
    trades_path = ROOT / "data" / "phase1_trades_audit_with_excursions.csv"
    candles_path = ROOT / "data" / "xautusd_multi_month_5m.csv"

    df_trades = pd.read_csv(trades_path)
    df_candles = pd.read_csv(candles_path, index_col=0, parse_dates=True)
    if df_candles.index.tz is None:
        df_candles.index = df_candles.index.tz_localize("Asia/Kolkata")
    else:
        df_candles.index = df_candles.index.tz_convert("Asia/Kolkata")
    df_candles = df_candles.sort_index()

    from indicators.orb_indicators import atr
    df_candles["ATR14"] = atr(df_candles["High"], df_candles["Low"], df_candles["Close"], 14)

    cfg = ORBSettings()

    # Replay all 250 trades across all 5 models
    model_stats = {
        "Model A: Baseline": {"pnl": 0.0, "total_r": 0.0, "wins": 0, "losses": 0, "sl_count": 0, "tp_count": 0},
        "Model B: Breakeven at +1.0R": {"pnl": 0.0, "total_r": 0.0, "wins": 0, "losses": 0, "sl_count": 0, "tp_count": 0},
        "Model C: ATR Trailing Stop": {"pnl": 0.0, "total_r": 0.0, "wins": 0, "losses": 0, "sl_count": 0, "tp_count": 0},
        "Model D: Partial Scale (50% at 1R)": {"pnl": 0.0, "total_r": 0.0, "wins": 0, "losses": 0, "sl_count": 0, "tp_count": 0},
        "Model E: Time-Decay 90m": {"pnl": 0.0, "total_r": 0.0, "wins": 0, "losses": 0, "sl_count": 0, "tp_count": 0},
    }

    n_trades = len(df_trades)
    for _, row in df_trades.iterrows():
        res = simulate_trade_exits(row, df_candles, cfg)
        for m_name, m_res in res.items():
            model_stats[m_name]["pnl"] += m_res["pnl"]
            model_stats[m_name]["total_r"] += m_res["r"]
            if m_res["r"] > 0:
                model_stats[m_name]["wins"] += 1
            else:
                model_stats[m_name]["losses"] += 1
            if m_res["exit_reason"] == "SL":
                model_stats[m_name]["sl_count"] += 1
            elif m_res["exit_reason"] == "TP":
                model_stats[m_name]["tp_count"] += 1

    print("=" * 100)
    print("PHASE 4: EXIT MODEL INDEPENDENT SIMULATION (N=250 TRADES)")
    print("=" * 100)
    print(f"{'Exit Model':<36} | {'Win %':<6} | {'Net P&L ($)':<12} | {'Total R':<8} | {'Exp (R)':<8} | {'SL Hits':<7} | {'TP Hits':<7}")
    print("-" * 100)

    for m_name, s in model_stats.items():
        wr = (s["wins"] / n_trades) * 100.0
        exp_r = s["total_r"] / n_trades
        print(f"{m_name:<36} | {wr:>5.1f}% | ${s['pnl']:>10.2f} | {s['total_r']:>8.2f} | {exp_r:>8.4f} | {s['sl_count']:<7} | {s['tp_count']:<7}")

if __name__ == "__main__":
    main()

