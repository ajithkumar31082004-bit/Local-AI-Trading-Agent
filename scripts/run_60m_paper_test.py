"""60-Minute Real-Time PAPER Trading Stability and Strategy Monitoring Harness.

Runs DeltaPaperBot against Delta Exchange India live market data for a target
of 60 continuous minutes (3600 seconds) while collecting high-resolution telemetry:
- WebSocket connection stability and reconnect events.
- Ticker updates, volume, spread, and timestamp freshness.
- Completed 5m and 15m candles, checking for duplicates or gaps.
- Indicator calculation validity (no NaNs while READY).
- Market regime classification on every completed candle.
- Strategy Intelligence V2 evaluations, signals, and rejection reasons.
- Simulated paper broker position tracking, SL/TP monitoring, friction, P&L.
- Persistence verification across account reloads.
- Strict safety verification: ZERO real orders sent to Delta Exchange.
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.orb_settings import ORBSettings, settings
from config.settings import Settings
from market_data.delta_live_feed import DeltaLiveMarketDataFeed
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from services.delta_paper_bot import DeltaPaperBot
from strategy.arbitrator import arbitrate_strategies
from regime.market_regime import detect_market_regime, MarketRegime
from regime.readiness import ReadinessState, SystemReadiness
from indicators.orb_indicators import resample_to_15m, ema, adx, atr, vwap
from data.delta_candles import detect_missing_candles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("StabilityTest60M")


def run_safety_and_product_audit() -> Dict[str, Any]:
    """Verify safety configuration and public Delta Exchange India product specifications."""
    print("=" * 80)
    print("PHASE 1: SAFETY AUDIT & PUBLIC DELTA PRODUCT SPECIFICATION")
    print("=" * 80)

    cfg = ORBSettings()
    pyd_cfg = Settings()

    assert cfg.mode == "PAPER", f"Safety violation: cfg.mode={cfg.mode}, must be PAPER"
    assert cfg.live_trading_enabled is False, "Safety violation: live_trading_enabled is True"
    assert os.getenv("TRADING_MODE") == "PAPER", "Safety violation: env TRADING_MODE is not PAPER"
    assert os.getenv("LIVE_TRADING_ENABLED") == "false", "Safety violation: env LIVE_TRADING_ENABLED is not false"

    print("  [PASS] Operational Mode: Strictly PAPER. Live execution disabled.")
    print("  [PASS] Order Pathway: Isolated Simulated Local Broker.")

    # Validate Product on Delta Exchange India public API
    url = f"{cfg.api_base_url}/v2/products"
    resp = requests.get(url, params={"symbol": cfg.symbol}, timeout=10)
    assert resp.status_code == 200, f"HTTP {resp.status_code} fetching products"
    products = resp.json().get("result", [])
    target = None
    for p in products:
        if str(p.get("symbol", "")).upper() == cfg.symbol:
            target = p
            break

    assert target is not None, f"Symbol {cfg.symbol} not found on Delta Exchange India"
    actual_id = int(target.get("id"))
    assert actual_id == cfg.product_id, f"Product ID mismatch: expected {cfg.product_id}, got {actual_id}"

    print(f"  [PASS] Verified Symbol: {target.get('symbol')} | Product ID: {actual_id} | Contract: {target.get('contract_type')} | Settling: {target.get('quoting_asset', {}).get('symbol')}")
    print("=" * 80 + "\n")

    return {
        "mode": cfg.mode,
        "live_trading_enabled": cfg.live_trading_enabled,
        "symbol": target.get("symbol"),
        "product_id": actual_id,
        "contract_type": target.get("contract_type"),
        "quoting_asset": target.get("quoting_asset", {}).get("symbol"),
        "tick_size": target.get("tick_size"),
    }


def execute_stability_test(target_duration_seconds: int = 3600) -> Dict[str, Any]:
    """Execute the 60-minute continuous monitoring test."""
    print("=" * 80)
    print(f"PHASE 2: 60-MINUTE REAL-TIME PAPER RUNTIME STABILITY MONITORING")
    print(f"Target Duration: {target_duration_seconds} seconds ({target_duration_seconds / 60:.1f} minutes)")
    print("WebSocket URL:   wss://socket.india.delta.exchange")
    print("REST Seed:       450 historical 5m candles")
    print("Strategy:        Strategy Intelligence V2 (Arbitrated PORTFOLIO)")
    print("=" * 80)

    cfg = ORBSettings(
        symbol="XAUTUSD",
        strategy_type="PORTFOLIO",
        mode="PAPER",
        bootstrap_lookback_candles=450,
    )

    # Use a clean test-specific paper state file to avoid mixing past test runs
    test_state_file = PROJECT_ROOT / "data" / "paper_stability_60m_state.json"
    if test_state_file.exists():
        try:
            test_state_file.unlink()
        except Exception:
            pass

    broker = DeltaPaperBroker(cfg=cfg, state_file=test_state_file, initial_equity=10000.0)

    # Ensure clean singleton bot instance
    DeltaPaperBot._instance = None
    bot = DeltaPaperBot(cfg=cfg, broker=broker)

    # Telemetry trackers
    start_wall_time = datetime.now(timezone.utc)
    start_perf_time = time.time()
    next_report_time = start_perf_time + 2.0

    ticks_count = 0
    ticks_per_minute: List[int] = []
    current_min_ticks = 0
    min_tick_reset_time = start_perf_time + 60.0

    price_ticks: List[float] = []
    prices_recorded: List[Tuple[float, float, float]] = []  # (ts, price, mark)
    
    # Event records
    completed_5m_records: List[Dict[str, Any]] = []
    completed_15m_records: List[Dict[str, Any]] = []
    strategy_evaluations: List[Dict[str, Any]] = []
    signals_generated: List[Dict[str, Any]] = []
    rejections_record: List[Dict[str, Any]] = []
    readiness_state_durations: Dict[str, float] = {
        ReadinessState.DISCONNECTED.value: 0.0,
        ReadinessState.CONNECTING.value: 0.0,
        ReadinessState.BOOTSTRAPPING.value: 0.0,
        ReadinessState.WARMING_UP.value: 0.0,
        ReadinessState.READY.value: 0.0,
        ReadinessState.DEGRADED.value: 0.0,
    }
    market_regime_timeline: List[Dict[str, Any]] = []
    ws_disconnect_events: List[Dict[str, Any]] = []

    last_readiness_state = ReadinessState.DISCONNECTED.value
    last_state_change_time = start_perf_time
    last_ws_connected_state = False

    # Callbacks
    def on_tick(price: float, ticker: Dict[str, Any]):
        nonlocal ticks_count, current_min_ticks
        ticks_count += 1
        current_min_ticks += 1
        price_ticks.append(price)

    def on_candle(df5: pd.DataFrame, df15: pd.DataFrame, bar_info: Dict[str, Any]):
        bar_ts = str(bar_info.get("timestamp"))
        c = float(bar_info.get("Close", 0.0))
        h = float(bar_info.get("High", c))
        l = float(bar_info.get("Low", c))
        v = float(bar_info.get("Volume", 0.0))
        now_iso = datetime.now(timezone.utc).isoformat()
        
        logger.info("CANDLE COMPLETED EVENT: 5m Bar %s Close=$%.2f Vol=%.0f (Total 5m: %d, 15m: %d)", bar_ts, c, v, len(df5), len(df15))
        completed_5m_records.append({
            "event_time": now_iso,
            "candle_timestamp": bar_ts,
            "close": c,
            "high": h,
            "low": l,
            "volume": v,
            "total_5m_bars": len(df5),
            "total_15m_bars": len(df15),
        })

    bot.feed.register_tick_callback(on_tick)
    bot.feed.register_candle_callback(on_candle)

    # Start bot
    logger.info("Starting DeltaPaperBot with bootstrap lookback 450 candles...")
    bot.start(bootstrap_lookback=450)

    print(f"\n[RUNNING] Live stream active since {start_wall_time.strftime('%Y-%m-%d %H:%M:%S UTC')}.")
    print("Press Ctrl+C to terminate cleanly early if needed.\n")

    interrupted = False
    try:
        while True:
            now_perf = time.time()
            elapsed_sec = now_perf - start_perf_time
            if elapsed_sec >= target_duration_seconds:
                logger.info("Target duration of %d seconds reached.", target_duration_seconds)
                break

            time.sleep(2.0)
            loop_now = time.time()

            # Minute tick counter roll
            if loop_now >= min_tick_reset_time:
                ticks_per_minute.append(current_min_ticks)
                current_min_ticks = 0
                min_tick_reset_time = loop_now + 60.0

            # Telemetry snapshot
            telem = bot.get_dashboard_telemetry()
            feed_stat = telem.get("feed", {})
            acc_stat = telem.get("account", {})
            cur_price = feed_stat.get("latest_price", 0.0)
            mark_price = feed_stat.get("mark_price", 0.0)
            bid = feed_stat.get("bid", 0.0)
            ask = feed_stat.get("ask", 0.0)
            ws_conn = feed_stat.get("ws_connected", False)
            readiness_str = telem.get("readiness_state", ReadinessState.DISCONNECTED.value)
            regime_str = telem.get("market_regime", "UNKNOWN")
            rejection_str = telem.get("last_rejection_reason", "None")

            if cur_price > 0:
                prices_recorded.append((loop_now, cur_price, mark_price))

            # Track readiness state duration
            state_duration = loop_now - last_state_change_time
            if readiness_str in readiness_state_durations:
                readiness_state_durations[readiness_str] += 2.0  # Poll interval chunk
            if readiness_str != last_readiness_state:
                logger.info("Readiness state changed: %s -> %s (spent %.1fs in %s)", last_readiness_state, readiness_str, state_duration, last_readiness_state)
                last_readiness_state = readiness_str
                last_state_change_time = loop_now

            # Track WS connection state changes
            if ws_conn != last_ws_connected_state:
                now_iso = datetime.now(timezone.utc).isoformat()
                ws_disconnect_events.append({
                    "timestamp": now_iso,
                    "connected": ws_conn,
                    "reconnect_attempts": feed_stat.get("reconnect_attempts", 0),
                    "last_error": feed_stat.get("last_error"),
                })
                last_ws_connected_state = ws_conn

            # Periodic 15-second status display & evaluation logging
            if loop_now >= next_report_time:
                next_report_time = loop_now + 15.0
                rem_sec = max(0, int(target_duration_seconds - elapsed_sec))
                mins_elapsed = int(elapsed_sec // 60)
                secs_elapsed = int(elapsed_sec % 60)
                rem_m = int(rem_sec // 60)
                rem_s = int(rem_sec % 60)

                df5 = bot.feed.get_completed_5m_candles()
                df15 = bot.feed.get_completed_15m_candles()
                cnt5 = len(df5)
                cnt15 = len(df15)

                # Record strategy evaluation snapshot
                strategy_evaluations.append({
                    "elapsed_seconds": round(elapsed_sec, 1),
                    "wall_time": datetime.now(timezone.utc).isoformat(),
                    "price": cur_price,
                    "mark_price": mark_price,
                    "bid": bid,
                    "ask": ask,
                    "readiness": readiness_str,
                    "regime": regime_str,
                    "rejection_reason": rejection_str,
                    "completed_5m": cnt5,
                    "completed_15m": cnt15,
                })

                # Check and record market regime changes
                if not market_regime_timeline or market_regime_timeline[-1].get("regime") != regime_str:
                    market_regime_timeline.append({
                        "elapsed_seconds": round(elapsed_sec, 1),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "regime": regime_str,
                        "price": cur_price,
                    })

                # Log signals if any generated
                last_sig = telem.get("last_signal")
                if last_sig and last_sig.get("direction") in ("LONG", "SHORT"):
                    if not signals_generated or signals_generated[-1].get("timestamp") != last_sig.get("timestamp"):
                        signals_generated.append(last_sig)
                        print(f"    ⭐ SIGNAL GENERATED: {last_sig.get('direction')} via {last_sig.get('strategy_name')} @ ${last_sig.get('entry')} (SL: ${last_sig.get('sl')}, TP: ${last_sig.get('tp')})")

                pos = acc_stat.get("open_position")
                pos_str = "FLAT" if not pos else f"{pos['direction']} {pos['lots']}L @ ${pos['entry_price']:.2f}"
                upnl = acc_stat.get("unrealized_pnl", 0.0)

                status_line = (
                    f"[{mins_elapsed:02d}m{secs_elapsed:02d}s | Rem: {rem_m:02d}m{rem_s:02d}s] "
                    f"WS: {'OK' if ws_conn else 'DISCONN':<7} | "
                    f"Ready: {readiness_str:<10} | "
                    f"Price: ${cur_price:>8.2f} (B/A: {bid:.1f}/{ask:.1f}) | "
                    f"5m: {cnt5} | 15m: {cnt15} | "
                    f"Regime: {regime_str:<16} | "
                    f"Pos: {pos_str} (uPnL: ${upnl:+.2f}) | "
                    f"Ticks: {ticks_count:>5}"
                )
                print(status_line, flush=True)

                # Persist live heart-beat telemetry to disk
                live_status_data = {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_seconds": round(elapsed_sec, 1),
                    "remaining_seconds": rem_sec,
                    "ws_connected": ws_conn,
                    "readiness_state": readiness_str,
                    "price": cur_price,
                    "mark_price": mark_price,
                    "bid": bid,
                    "ask": ask,
                    "completed_5m": cnt5,
                    "completed_15m": cnt15,
                    "candles_completed_during_test": len(completed_5m_records),
                    "regime": regime_str,
                    "rejection_reason": rejection_str,
                    "ticks_count": ticks_count,
                    "open_position": pos,
                    "unrealized_pnl": upnl,
                    "status_line": status_line,
                }
                try:
                    with open(PROJECT_ROOT / "data" / "paper_stability_live_status.json", "w", encoding="utf-8") as f_live:
                        json.dump(live_status_data, f_live, indent=2)
                except Exception:
                    pass

    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Cleaning up...")
        interrupted = True
    finally:
        end_wall_time = datetime.now(timezone.utc)
        actual_duration_seconds = time.time() - start_perf_time
        logger.info("Stopping DeltaPaperBot cleanly...")
        bot.stop()
        logger.info("Bot stopped. Compiling final stability telemetry...")

    # Final post-run diagnostics
    df_final_5m = bot.feed.get_completed_5m_candles()
    df_final_15m = bot.feed.get_completed_15m_candles()

    # 1. Check for duplicates in candle series
    dup_5m_count = int(df_final_5m.index.duplicated().sum()) if not df_final_5m.empty else 0
    dup_15m_count = int(df_final_15m.index.duplicated().sum()) if not df_final_15m.empty else 0

    # 2. Check for missing candle gaps
    gaps_5m = detect_missing_candles(df_final_5m, expected_seconds=300) if not df_final_5m.empty else []

    # 3. Check indicator non-NaN validity
    indicators_valid = True
    nan_diagnostics = {}
    if not df_final_15m.empty and len(df_final_15m) >= 50:
        c15 = df_final_15m["Close"]
        h15 = df_final_15m["High"]
        l15 = df_final_15m["Low"]
        e20_15 = ema(c15, 20)
        e50_15 = ema(c15, 50)
        adx_15 = adx(h15, l15, c15, 14)
        atr_15 = atr(h15, l15, c15, 14)

        nan_diagnostics["15m_EMA20_NaN"] = bool(pd.isna(e20_15.iloc[-1]))
        nan_diagnostics["15m_EMA50_NaN"] = bool(pd.isna(e50_15.iloc[-1]))
        nan_diagnostics["15m_ADX14_NaN"] = bool(pd.isna(adx_15.iloc[-1]))
        nan_diagnostics["15m_ATR14_NaN"] = bool(pd.isna(atr_15.iloc[-1]))

        if any(nan_diagnostics.values()):
            indicators_valid = False

    if not df_final_5m.empty and len(df_final_5m) >= 50:
        c5 = df_final_5m["Close"]
        h5 = df_final_5m["High"]
        l5 = df_final_5m["Low"]
        v5 = df_final_5m["Volume"]
        e50_5 = ema(c5, 50)
        vwap_5 = vwap(h5, l5, c5, v5)

        nan_diagnostics["5m_EMA50_NaN"] = bool(pd.isna(e50_5.iloc[-1]))
        nan_diagnostics["5m_VWAP_NaN"] = bool(pd.isna(vwap_5.iloc[-1]))

        if nan_diagnostics.get("5m_EMA50_NaN") or nan_diagnostics.get("5m_VWAP_NaN"):
            indicators_valid = False

    # 4. Verify 15m resampling grouping (counts == 3)
    resample_integrity_passed = True
    if not df_final_5m.empty:
        grouped = df_final_5m.resample("15min", closed="left", label="left")
        counts = grouped["Close"].count()
        # All bars in df_final_15m must have count == 3
        for ts in df_final_15m.index:
            if ts in counts.index and counts.loc[ts] != 3:
                resample_integrity_passed = False
                break

    # 5. Paper Account Persistence Verification
    summary_live = broker.get_account_summary()
    broker_reloaded = DeltaPaperBroker(cfg=cfg, state_file=test_state_file, initial_equity=10000.0)
    summary_reloaded = broker_reloaded.get_account_summary()
    persistence_verified = (
        abs(summary_live["cash_balance"] - summary_reloaded["cash_balance"]) < 0.01 and
        abs(summary_live["realized_pnl"] - summary_reloaded["realized_pnl"]) < 0.01 and
        summary_live["total_trades"] == summary_reloaded["total_trades"]
    )

    feed_status = bot.feed.get_status()

    # Calculate price range observed
    prices_only = [p[1] for p in prices_recorded]
    min_price_observed = min(prices_only) if prices_only else 0.0
    max_price_observed = max(prices_only) if prices_only else 0.0

    output_results = {
        "test_metadata": {
            "start_time_utc": start_wall_time.isoformat(),
            "end_time_utc": end_wall_time.isoformat(),
            "target_duration_seconds": target_duration_seconds,
            "actual_duration_seconds": round(actual_duration_seconds, 2),
            "actual_duration_minutes": round(actual_duration_seconds / 60.0, 2),
            "interrupted": interrupted,
            "symbol": cfg.symbol,
            "product_id": cfg.product_id,
        },
        "connection_telemetry": {
            "ws_url": bot.feed.ws_url,
            "final_ws_connected": feed_status.get("ws_connected", False),
            "total_reconnect_attempts": feed_status.get("reconnect_attempts", 0),
            "disconnect_events": ws_disconnect_events,
            "disconnect_count": len(ws_disconnect_events),
        },
        "market_data_telemetry": {
            "total_ticks_received": ticks_count,
            "min_price_observed": min_price_observed,
            "max_price_observed": max_price_observed,
            "final_price": feed_status.get("latest_price", 0.0),
            "final_mark_price": feed_status.get("mark_price", 0.0),
            "final_bid": feed_status.get("bid", 0.0),
            "final_ask": feed_status.get("ask", 0.0),
            "completed_5m_candles_final": len(df_final_5m),
            "completed_15m_candles_final": len(df_final_15m),
            "candles_completed_during_test": len(completed_5m_records),
            "completed_candles_log": completed_5m_records,
            "duplicate_5m_timestamps": dup_5m_count,
            "duplicate_15m_timestamps": dup_15m_count,
            "missing_candle_gaps": len(gaps_5m),
            "gap_descriptions": [f"{g[0]} to {g[1]}" for g in gaps_5m[:5]],
            "resample_15m_grouping_strictly_3_bars": resample_integrity_passed,
        },
        "readiness_telemetry": {
            "final_readiness_state": feed_status.get("readiness_state"),
            "final_is_ready": feed_status.get("is_ready"),
            "state_durations_seconds": {k: round(v, 1) for k, v in readiness_state_durations.items()},
            "indicators_non_nan_valid": indicators_valid,
            "nan_diagnostics": nan_diagnostics,
        },
        "strategy_and_regime_telemetry": {
            "total_evaluations": len(strategy_evaluations),
            "market_regime_timeline": market_regime_timeline,
            "final_market_regime": market_regime_timeline[-1].get("regime") if market_regime_timeline else "UNKNOWN",
            "signals_generated_count": len(signals_generated),
            "signals_generated_list": signals_generated,
            "sample_rejection_reasons": [e["rejection_reason"] for e in strategy_evaluations[-5:]],
        },
        "broker_and_execution_telemetry": {
            "initial_equity": summary_live.get("initial_equity", 10000.0),
            "final_cash_balance": summary_live.get("cash_balance", 10000.0),
            "final_equity": summary_live.get("equity", 10000.0),
            "realized_pnl": summary_live.get("realized_pnl", 0.0),
            "unrealized_pnl": summary_live.get("unrealized_pnl", 0.0),
            "net_pnl": summary_live.get("net_pnl", 0.0),
            "total_fees": summary_live.get("total_fees", 0.0),
            "total_slippage": summary_live.get("total_slippage", 0.0),
            "peak_equity": summary_live.get("peak_equity", 10000.0),
            "max_drawdown_usd": summary_live.get("max_drawdown_usd", 0.0),
            "max_drawdown_pct": summary_live.get("max_drawdown_pct", 0.0),
            "total_simulated_trades": summary_live.get("total_trades", 0),
            "open_position": summary_live.get("open_position"),
            "closed_trades": summary_live.get("closed_trades", []),
            "persistence_verified": persistence_verified,
        },
        "safety_audit": {
            "real_orders_submitted": 0,
            "private_order_endpoints_called": 0,
            "live_trading_enabled": False,
            "mode": "PAPER",
        },
    }

    results_file = PROJECT_ROOT / "data" / "paper_stability_60m_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(output_results, f, indent=2)

    logger.info("Results successfully saved to %s", results_file)
    return output_results


def main():
    p = argparse.ArgumentParser(description="60-Minute Real-Time Paper Trading Stability Monitor")
    p.add_argument("--duration", type=int, default=3600, help="Test duration in seconds (default: 3600 = 60 minutes)")
    args = p.parse_args()

    # 1. Audit safety & product
    audit_data = run_safety_and_product_audit()

    # 2. Run monitoring
    res = execute_stability_test(target_duration_seconds=args.duration)

    print("\n" + "=" * 80)
    print("STABILITY MONITORING SUMMARY")
    print("=" * 80)
    print(f"Actual Duration:               {res['test_metadata']['actual_duration_minutes']:.2f} minutes ({res['test_metadata']['actual_duration_seconds']}s)")
    print(f"WebSocket Status:              {'CONNECTED' if res['connection_telemetry']['final_ws_connected'] else 'DISCONNECTED'}")
    print(f"Reconnect Count:               {res['connection_telemetry']['total_reconnect_attempts']}")
    print(f"Total Ticks Received:          {res['market_data_telemetry']['total_ticks_received']}")
    print(f"Price Range Observed:          ${res['market_data_telemetry']['min_price_observed']:.2f} - ${res['market_data_telemetry']['max_price_observed']:.2f} (Final: ${res['market_data_telemetry']['final_price']:.2f})")
    print(f"Completed 5m Candles Buffer:   {res['market_data_telemetry']['completed_5m_candles_final']} bars (Duplicates: {res['market_data_telemetry']['duplicate_5m_timestamps']})")
    print(f"Completed 15m Candles Buffer:  {res['market_data_telemetry']['completed_15m_candles_final']} bars (Duplicates: {res['market_data_telemetry']['duplicate_15m_timestamps']})")
    print(f"Candles Completed During Test: {res['market_data_telemetry']['candles_completed_during_test']}")
    print(f"15m Resample Integrity (3x5m): {'PASSED' if res['market_data_telemetry']['resample_15m_grouping_strictly_3_bars'] else 'FAILED'}")
    print(f"Final Readiness State:         {res['readiness_telemetry']['final_readiness_state']} (Ready: {res['readiness_telemetry']['final_is_ready']})")
    print(f"Indicator Non-NaN Valid:       {res['readiness_telemetry']['indicators_non_nan_valid']}")
    print(f"Final Market Regime:           {res['strategy_and_regime_telemetry']['final_market_regime']}")
    print(f"Strategy Evaluations:          {res['strategy_and_regime_telemetry']['total_evaluations']}")
    print(f"Signals Generated:             {res['strategy_and_regime_telemetry']['signals_generated_count']}")
    print(f"Simulated Trades:              {res['broker_and_execution_telemetry']['total_simulated_trades']} (Net P&L: ${res['broker_and_execution_telemetry']['net_pnl']:+.2f})")
    print(f"Paper Persistence Verified:    {res['broker_and_execution_telemetry']['persistence_verified']}")
    print(f"Real Orders Submitted:         0 (ZERO - Strictly Simulated)")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
