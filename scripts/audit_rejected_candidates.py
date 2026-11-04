"""Audit script to identify any candidate setups generated during the 60-minute test,
examine why they were rejected, and check subsequent price trajectory.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from data.delta_candles import fetch_5m_candles
from config.orb_settings import ORBSettings
from regime.market_regime import detect_market_regime
from strategy.orb_ema_volume import evaluate_orb
from strategy.trend_following import evaluate_trend_following
from strategy.vwap_ema import evaluate_vwap_ema
from strategy.bb_rsi_mean_reversion import evaluate_bb_rsi
from strategy.atr_compression_breakout import evaluate_atr_compression_breakout
from strategy.liquidity_sweep import evaluate_liquidity_sweep
from strategy.arbitrator import arbitrate_strategies

def main():
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    
    # Fetch candles covering the entire 60m test window plus subsequent candles up to present
    end_t = datetime.now(timezone.utc)
    df_all = fetch_5m_candles("XAUTUSD", 500, end_time=end_t)
    
    # Focus on the 12 candle timestamps completed during the 60m run
    timestamps_60m = [
        "2026-11-04 14:55:00+00:00",
        "2026-11-04 15:00:00+00:00",
        "2026-11-04 15:05:00+00:00",
        "2026-11-04 15:10:00+00:00",
        "2026-11-04 15:15:00+00:00",
        "2026-11-04 15:20:00+00:00",
        "2026-11-04 15:25:00+00:00",
        "2026-11-04 15:30:00+00:00",
        "2026-11-04 15:35:00+00:00",
        "2026-11-04 15:40:00+00:00",
        "2026-11-04 15:45:00+00:00",
        "2026-11-04 15:50:00+00:00",
    ]
    
    strategies = {
        "ORB": evaluate_orb,
        "TREND_FOLLOWING": evaluate_trend_following,
        "VWAP_EMA": evaluate_vwap_ema,
        "BB_RSI_MEAN_REVERSION": evaluate_bb_rsi,
        "ATR_COMPRESSION_BREAKOUT": evaluate_atr_compression_breakout,
        "LIQUIDITY_SWEEP": evaluate_liquidity_sweep,
    }
    
    results = []
    
    for ts_str in timestamps_60m:
        ts = pd.Timestamp(ts_str)
        if ts not in df_all.index:
            continue
        idx_pos = df_all.index.get_loc(ts)
        # Sliced frame up to this completed candle (no lookahead)
        frame_slice = df_all.iloc[: idx_pos + 1]
        
        regime_res = detect_market_regime(frame_slice, cfg)
        arb_res = arbitrate_strategies(frame_slice, cfg)
        
        # Test each strategy individually without arbitrator filter
        for s_name, s_fn in strategies.items():
            sig = s_fn(frame_slice, cfg)
            # If the strategy signaled LONG or SHORT, or reached deep checklist
            is_active_signal = sig.direction in ("LONG", "SHORT")
            
            # Check if it was considered a candidate
            results.append({
                "timestamp": ts_str,
                "strategy": s_name,
                "direction": sig.direction,
                "entry": sig.entry,
                "sl": sig.sl,
                "tp": sig.tp,
                "quality_score": getattr(sig, "quality_score", 0.0),
                "reason": sig.reason,
                "diagnostic_code": getattr(sig, "diagnostic_code", None),
                "regime": regime_res.regime.value,
                "regime_allowed": s_name in regime_res.allowed_strategies,
                "checklist": sig.checklist,
                "future_slice_length": len(df_all) - (idx_pos + 1),
                "idx_pos": idx_pos,
            })

    # Filter to cases where direction is LONG or SHORT, or where entry price was set
    active_candidates = [r for r in results if r["direction"] in ("LONG", "SHORT") or r["entry"] is not None]
    print(f"Total evaluations: {len(results)}, Active candidates found: {len(active_candidates)}")
    for c in active_candidates:
        print(f"[{c['timestamp']}] {c['strategy']} {c['direction']} Entry:${c['entry']} SL:${c['sl']} TP:${c['tp']} Regime:{c['regime']} Reason:{c['reason']}")

    # Also inspect why sub-strategies were rejected
    with open("data/audit_candidates_raw.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

if __name__ == "__main__":
    main()
