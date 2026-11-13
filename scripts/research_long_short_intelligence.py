"""Phase 2: Long and Short Intelligence Research Script.

Analyzes indicator states at the exact bar of entry for all Long and Short trades:
- 15m EMA20 slope & EMA50 slope
- 5m EMA20 slope
- Price vs VWAP distance
- 15m ADX & 5m ADX
- 5m ATR
- Pullback depth from swing high/low
- Volume relative to 20-bar baseline
- Candle wick/body ratios
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from indicators.orb_indicators import ema, ema_slope, atr, adx, vwap, prior_volume_average, resample_to_15m

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

    # Precalculate indicators on full 5m dataset
    df_5m = df_candles.copy()
    df_5m["EMA20"] = ema(df_5m["Close"], 20)
    df_5m["EMA50"] = ema(df_5m["Close"], 50)
    df_5m["EMA20_Slope"] = ema_slope(df_5m["EMA20"], 2)
    df_5m["ATR14"] = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)
    df_5m["ADX14"] = adx(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)
    df_5m["VWAP"] = vwap(df_5m["High"], df_5m["Low"], df_5m["Close"], df_5m["Volume"])
    df_5m["Vol_Ratio"] = df_5m["Volume"] / prior_volume_average(df_5m["Volume"], 20).replace(0, np.nan)

    # 15m indicators
    df_15m = resample_to_15m(df_candles)
    df_15m["EMA20_15m"] = ema(df_15m["Close"], 20)
    df_15m["EMA50_15m"] = ema(df_15m["Close"], 50)
    df_15m["ADX_15m"] = adx(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)
    df_15m["Slope20_15m"] = ema_slope(df_15m["EMA20_15m"], 2)

    features = []
    for _, row in df_trades.iterrows():
        e_time = pd.to_datetime(row["entry_time"])
        if e_time not in df_5m.index:
            continue
        c_bar = df_5m.loc[e_time]

        # Latest closed 15m bar prior to or at entry
        closed_15m = df_15m[df_15m.index <= e_time]
        last_15m = closed_15m.iloc[-1] if not closed_15m.empty else None

        c_close = float(c_bar["Close"])
        c_open = float(c_bar["Open"])
        c_high = float(c_bar["High"])
        c_low = float(c_bar["Low"])
        body = abs(c_close - c_open)
        upper_wick = c_high - max(c_close, c_open)
        lower_wick = min(c_close, c_open) - c_low
        total_range = max(c_high - c_low, 1e-6)

        feat = {
            "trade_id": row["trade_id"],
            "direction": row["direction"],
            "strategy": row["run_strategy"],
            "r": row["r"],
            "win": 1 if row["r"] > 0 else 0,
            "mfe_r": row["mfe_r"],
            "pnl_cash": row["pnl_cash"],
            "ema20_slope_5m": float(c_bar["EMA20_Slope"]) if pd.notna(c_bar["EMA20_Slope"]) else 0.0,
            "adx_5m": float(c_bar["ADX14"]) if pd.notna(c_bar["ADX14"]) else 0.0,
            "atr_5m": float(c_bar["ATR14"]) if pd.notna(c_bar["ATR14"]) else 1.0,
            "dist_vwap_atr": (c_close - float(c_bar["VWAP"])) / max(float(c_bar["ATR14"]), 1e-6) if pd.notna(c_bar["VWAP"]) else 0.0,
            "vol_ratio": float(c_bar["Vol_Ratio"]) if pd.notna(c_bar["Vol_Ratio"]) else 1.0,
            "body_ratio": body / total_range,
            "lower_wick_ratio": lower_wick / total_range,
            "upper_wick_ratio": upper_wick / total_range,
            "adx_15m": float(last_15m["ADX_15m"]) if last_15m is not None and pd.notna(last_15m["ADX_15m"]) else 0.0,
            "slope20_15m": float(last_15m["Slope20_15m"]) if last_15m is not None and pd.notna(last_15m["Slope20_15m"]) else 0.0,
            "trend_15m_spread": (float(last_15m["EMA20_15m"]) - float(last_15m["EMA50_15m"])) / max(float(c_bar["ATR14"]), 1e-6) if last_15m is not None else 0.0,
        }
        features.append(feat)

    df_feat = pd.DataFrame(features)

    print("=" * 80)
    print("PHASE 2: LONG VS SHORT INTELLIGENCE DISCRIMINATION REPORT")
    print("=" * 80)

    for side in ["LONG", "SHORT"]:
        sub = df_feat[df_feat["direction"] == side]
        wins = sub[sub["win"] == 1]
        losses = sub[sub["win"] == 0]

        print(f"\n==================== {side} TRADES (N={len(sub)}, Wins={len(wins)}, Losses={len(losses)}) ====================")
        metrics = [
            ("15m ADX Trend Strength", "adx_15m"),
            ("15m EMA20 Slope", "slope20_15m"),
            ("15m EMA Spread / ATR", "trend_15m_spread"),
            ("5m EMA20 Slope", "ema20_slope_5m"),
            ("5m Price vs VWAP (in ATRs)", "dist_vwap_atr"),
            ("Volume vs 20-bar Avg", "vol_ratio"),
            ("Rejection Wick Ratio (Lower for Long, Upper for Short)", "lower_wick_ratio" if side == "LONG" else "upper_wick_ratio"),
            ("Candle Body Ratio", "body_ratio"),
            ("5m ATR", "atr_5m"),
        ]

        for label, col in metrics:
            w_mean = wins[col].mean() if len(wins) > 0 else 0.0
            l_mean = losses[col].mean() if len(losses) > 0 else 0.0
            diff = w_mean - l_mean
            print(f"{label:<48} | Winners: {w_mean:>7.3f} | Losers: {l_mean:>7.3f} | Diff: {diff:>+7.3f}")

    df_feat.to_csv(ROOT / "data" / "phase2_feature_discrimination.csv", index=False)
    print(f"\n[SUCCESS] Feature discrimination dataset saved to data/phase2_feature_discrimination.csv")

if __name__ == "__main__":
    main()

