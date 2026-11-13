"""Evaluate trailing 24-hour completed market data from Delta Exchange India.

Evaluates 288 completed 5m candles (96 15m bars) chronologically across the
exact Strategy Intelligence V2 arbitrage engine, recording:
- Market regime classification and timeline
- Strategy evaluations and rejection reasons
- Simulated paper executions, friction, and P&L
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.orb_settings import ORBSettings
from services.delta_paper_broker import DeltaPaperBroker
from strategy.arbitrator import arbitrate_strategies
from regime.market_regime import detect_market_regime
from indicators.orb_indicators import resample_to_15m
from data.delta_candles import detect_missing_candles


def run_trailing_24h_evaluation():
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    url = f"{cfg.api_base_url}/v2/history/candles"
    
    # 450 bootstrap + 288 24h test = 738 bars
    end_epoch = int(datetime.now(timezone.utc).timestamp())
    start_epoch = end_epoch - ((450 + 288 + 10) * 300)
    
    resp = requests.get(url, params={"symbol": cfg.symbol, "resolution": "5m", "start": start_epoch, "end": end_epoch}, timeout=15)
    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    raw_bars = resp.json().get("result", [])
    
    # Clean & sort chronologically
    df_raw = pd.DataFrame(raw_bars)
    df_raw = df_raw.rename(columns={
        "time": "Timestamp", "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"
    })
    df_raw["Datetime"] = pd.to_datetime(df_raw["Timestamp"], unit="s", utc=True).dt.tz_convert(cfg.timezone)
    df_raw = df_raw.drop_duplicates(subset=["Datetime"]).sort_values("Datetime").set_index("Datetime")
    
    # Drop current forming bar if present
    now_ts = datetime.now(timezone.utc).timestamp()
    if now_ts - df_raw["Timestamp"].iloc[-1] < 300:
        df_raw = df_raw.iloc[:-1]
        
    print(f"Total historical bars available: {len(df_raw)}")
    assert len(df_raw) >= 738, f"Expected at least 738 bars, got {len(df_raw)}"
    
    # Slice exactly 450 bootstrap + 288 24-hour test bars
    df_study = df_raw.iloc[-(450 + 288):]
    bootstrap_df = df_study.iloc[:450]
    test_bars_df = df_study.iloc[450:]
    
    test_start = test_bars_df.index[0]
    test_end = test_bars_df.index[-1]
    
    print(f"24-Hour Forward Study Window: {test_start} to {test_end} ({len(test_bars_df)} 5m bars)")
    
    # Setup paper broker
    state_file = PROJECT_ROOT / "data" / "paper_forward_24h_history_state.json"
    if state_file.exists():
        state_file.unlink()
        
    broker = DeltaPaperBroker(cfg=cfg, state_file=state_file, initial_equity=10000.0)
    
    regime_timeline = []
    evaluations = []
    signals = []
    trades = []
    rejection_counts = {}
    
    # Step through the 288 forward bars causally
    for i in range(len(test_bars_df)):
        cur_bar = test_bars_df.iloc[i]
        bar_time = test_bars_df.index[i]
        # Trailing history up to current bar (no future lookahead)
        window = df_study.iloc[:450 + i + 1]
        
        # 1. Update open position SL/TP with current high/low/close
        if broker.open_position:
            h = float(cur_bar["High"])
            l = float(cur_bar["Low"])
            c = float(cur_bar["Close"])
            closed = broker.evaluate_open_position_sl_tp(c, high=h, low=l, timestamp=str(bar_time))
            if closed:
                trades.append(closed)
                    
        # 2. Evaluate market regime
        regime_res = detect_market_regime(window, cfg)
        regime_str = regime_res.regime.value
        
        if not regime_timeline or regime_timeline[-1]["regime"] != regime_str:
            regime_timeline.append({
                "bar_index": i,
                "timestamp": str(bar_time),
                "regime": regime_str,
                "confidence": regime_res.confidence,
                "price": float(cur_bar["Close"]),
            })
            
        # 3. Strategy Arbitrator evaluation
        arb_res = arbitrate_strategies(window, cfg)
        winning_sig = arb_res.winning_signal
        
        rejection_reason = "No signal"
        if not arb_res.eligible_signals and arb_res.rejected_strategies:
            rejection_reason = f"{arb_res.rejected_strategies[0][0]}: {arb_res.rejected_strategies[0][1]}"
            
        rejection_counts[rejection_reason] = rejection_counts.get(rejection_reason, 0) + 1
        
        evaluations.append({
            "bar": i + 1,
            "timestamp": str(bar_time),
            "close": float(cur_bar["Close"]),
            "regime": regime_str,
            "eligible_strategies": [s.strategy_name for s in arb_res.eligible_signals],
            "rejected_strategies": arb_res.rejected_strategies,
            "has_winning_signal": winning_sig is not None,
        })
        
        if winning_sig and winning_sig.direction in ("LONG", "SHORT"):
            signals.append({
                "bar": i + 1,
                "timestamp": str(bar_time),
                "strategy": winning_sig.strategy_name,
                "direction": winning_sig.direction,
                "entry": winning_sig.entry,
                "sl": winning_sig.sl,
                "tp": winning_sig.tp,
                "r_target": winning_sig.r_multiple,
                "regime": regime_str,
                "reason": winning_sig.reason,
            })
            
            # If broker has no open position and daily limits permit, open trade
            if not broker.open_position and len(broker.closed_trades) < cfg.max_trades_per_day:
                ok, msg, p_open = broker.open_simulated_trade(
                    symbol=cfg.symbol,
                    direction=winning_sig.direction,
                    entry_price=winning_sig.entry,
                    stop_loss=winning_sig.sl,
                    take_profit=winning_sig.tp,
                    lots=1,
                    strategy_name=winning_sig.strategy_name,
                    signal_reason=winning_sig.reason,
                )
                if ok:
                    print(f"  [TRADE OPENED] Bar {i+1} ({bar_time}): {winning_sig.direction} via {winning_sig.strategy_name} @ ${winning_sig.entry:.2f}")

    # Check for missing gaps or duplicate timestamps
    dup_5m = int(test_bars_df.index.duplicated().sum())
    gaps = detect_missing_candles(test_bars_df, expected_seconds=300)
    
    summary = broker.get_account_summary()
    
    output = {
        "start_ist": str(test_start),
        "end_ist": str(test_end),
        "total_5m_bars": len(test_bars_df),
        "total_15m_bars": len(resample_to_15m(test_bars_df)),
        "duplicates": dup_5m,
        "gaps_count": len(gaps),
        "regime_transitions_count": len(regime_timeline),
        "regime_timeline": regime_timeline,
        "evaluations_count": len(evaluations),
        "signals_count": len(signals),
        "signals": signals,
        "trades_count": len(trades),
        "trades": trades,
        "top_rejections": sorted(rejection_counts.items(), key=lambda x: x[1], reverse=True)[:8],
        "broker_summary": summary,
    }
    
    out_file = PROJECT_ROOT / "data" / "trailing_24h_audit_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
        
    print("\n" + "=" * 80)
    print("TRAILING 24-HOUR AUDIT SUMMARY")
    print("=" * 80)
    print(f"Period:               {test_start} to {test_end}")
    print(f"Bars Evaluated:       {len(test_bars_df)} 5m bars | {len(resample_to_15m(test_bars_df))} 15m bars")
    print(f"Duplicates / Gaps:    {dup_5m} duplicates | {len(gaps)} gaps")
    print(f"Regime Changes:       {len(regime_timeline)}")
    print(f"Strategy Signals:     {len(signals)}")
    print(f"Executed Trades:      {len(trades)}")
    print(f"Net Realized P&L:     ${summary.get('realized_pnl', 0.0):+.2f}")
    print(f"Final Equity:         ${summary.get('equity', 10000.0):.2f}")
    print("Top Rejections:")
    for r, count in output["top_rejections"][:5]:
        print(f"  - ({count}x) {r}")
    print("=" * 80)
    return output


if __name__ == "__main__":
    run_trailing_24h_evaluation()
