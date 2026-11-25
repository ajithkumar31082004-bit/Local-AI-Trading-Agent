"""Phase 3.35 — Offline Tests for Read-Only Shadow Observer.

COVERS 14 INDEPENDENT OFFLINE TEST DOMAINS:
1. Strategy registry discovery across all 6 strategies.
2. Completed-candle validation and incomplete candle rejection.
3. Duplicate candle detection and rejection.
4. Out-of-order timestamp detection.
5. Stale ticker detection (>60s elapsed).
6. Missing candle / gap detection.
7. Signal generation across strategies with synthetic fixtures.
8. Rejection reason recording.
9. Observer recorder restart & persistence.
10. Output-file integrity & schema verification ('HYPOTHETICAL — NOT AN ORDER').
11. Broker import isolation (observer modules do not import broker execution).
12. Zero broker state mutation (cash, equity, position, historical ledger unchanged).
13. No order endpoint access.
14. No private credential access & safety guard path enforcement.
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest

from services.shadow_observer.observer_safety_guard import (
    ShadowObserverSafetyViolation,
    validate_shadow_write_path,
    verify_observer_isolation,
    block_execution_attempt,
)
from services.shadow_observer.candle_validator import CandleValidator
from services.shadow_observer.strategy_evaluator import ReadOnlyStrategyEvaluator
from services.shadow_observer.signal_recorder import ShadowSignalRecorder
from services.shadow_observer.market_data_reader import PublicMarketDataReader
from services.shadow_observer.shadow_report import ShadowReportGenerator
from config.orb_settings import ORBSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_synthetic_5m_candles(count: int = 160, base_price: float = 4000.0) -> pd.DataFrame:
    """Generate a clean synthetic 5m OHLCV DataFrame for testing."""
    idx = pd.date_range("2026-11-10 09:00", periods=count, freq="5min", tz="Asia/Kolkata")
    opens, highs, lows, closes, vols = [], [], [], [], []
    for i in range(count):
        c = base_price + (i * 0.5)
        opens.append(c - 0.2)
        highs.append(c + 1.5)
        lows.append(c - 1.5)
        closes.append(c)
        vols.append(1000.0 + (i * 10))

    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


# =============================================================================
# 1. STRATEGY REGISTRY DISCOVERY
# =============================================================================

def test_1_strategy_registry_discovery():
    """Verify that all 6 core strategies are registered and discoverable in the evaluator."""
    evaluator = ReadOnlyStrategyEvaluator()
    registered = evaluator.registered_strategies
    expected = {
        "ORB",
        "TREND_FOLLOWING",
        "VWAP_EMA",
        "BB_RSI_MEAN_REVERSION",
        "ATR_COMPRESSION_BREAKOUT",
        "LIQUIDITY_SWEEP",
    }
    assert expected.issubset(set(registered.keys())), f"Missing strategies: {expected - set(registered.keys())}"
    assert len(registered) == 6


# =============================================================================
# 2. COMPLETED-CANDLE VALIDATION & INCOMPLETE REJECTION
# =============================================================================

def test_2_completed_candle_validation_and_incomplete_rejection():
    """Verify that completed candles pass and incomplete candles are rejected."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    now_epoch = 1731300000.0

    # 1. Valid completed candle
    valid_candle = {
        "open": 4000.0,
        "high": 4010.0,
        "low": 3995.0,
        "close": 4005.0,
        "volume": 500.0,
        "timestamp": now_epoch - 600,
        "is_complete": True,
    }
    rep = validator.validate_incoming_candle(valid_candle, now_epoch=now_epoch)
    assert rep.is_valid is True
    assert rep.is_completed is True

    # 2. Incomplete candle via flag
    incomplete_candle = dict(valid_candle, is_complete=False, timestamp=now_epoch - 300)
    rep_inc = validator.validate_incoming_candle(incomplete_candle, now_epoch=now_epoch)
    assert rep_inc.is_valid is False
    assert rep_inc.is_completed is False
    assert "INCOMPLETE_CANDLE" in rep_inc.rejection_reason

    # 3. Incomplete candle via timestamp in future / forming
    forming_candle = dict(valid_candle, timestamp=now_epoch - 60)
    rep_form = validator.validate_incoming_candle(forming_candle, now_epoch=now_epoch)
    assert rep_form.is_valid is False
    assert "INCOMPLETE_CANDLE" in rep_form.rejection_reason


# =============================================================================
# 3. DUPLICATE CANDLE REJECTION
# =============================================================================

def test_3_duplicate_candle_rejection():
    """Verify duplicate candles with identical timestamps are rejected."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    candle = {
        "open": 4000.0,
        "high": 4005.0,
        "low": 3995.0,
        "close": 4002.0,
        "volume": 100.0,
        "timestamp": 1731300000,
    }
    r1 = validator.validate_incoming_candle(candle)
    assert r1.is_valid is True

    # Ingest same candle again
    r2 = validator.validate_incoming_candle(candle)
    assert r2.is_valid is False
    assert r2.is_duplicate is True
    assert "DUPLICATE_CANDLE" in r2.rejection_reason
    assert validator.duplicate_count == 1


# =============================================================================
# 4. OUT-OF-ORDER TIMESTAMP DETECTION
# =============================================================================

def test_4_out_of_order_timestamp_detection():
    """Verify that candles arriving backwards in time are rejected."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    c1 = {"open": 4000.0, "high": 4005.0, "low": 3995.0, "close": 4002.0, "volume": 100.0, "timestamp": 1731300300}
    c2 = {"open": 4002.0, "high": 4006.0, "low": 3998.0, "close": 4004.0, "volume": 110.0, "timestamp": 1731300000}

    r1 = validator.validate_incoming_candle(c1)
    assert r1.is_valid is True

    r2 = validator.validate_incoming_candle(c2)
    assert r2.is_valid is False
    assert "OUT_OF_ORDER" in r2.rejection_reason


# =============================================================================
# 5. STALE TICKER DETECTION
# =============================================================================

def test_5_stale_ticker_detection():
    """Verify stale ticker condition is flagged when elapsed time > 60s."""
    reader = PublicMarketDataReader(symbol="XAUTUSD", stale_threshold_seconds=60.0)
    base_time = 1731300000.0

    # Ingest tick at base_time
    ok, _ = reader.ingest_ticker_update({"symbol": "XAUTUSD", "price": 4005.0}, now_epoch=base_time)
    assert ok is True

    # Check 30 seconds later -> not stale
    stale, elapsed = reader.is_feed_stale(now_epoch=base_time + 30.0)
    assert stale is False
    assert elapsed == 30.0

    # Check 75 seconds later -> stale
    stale_75, elapsed_75 = reader.is_feed_stale(now_epoch=base_time + 75.0)
    assert stale_75 is True
    assert elapsed_75 == 75.0
    assert reader.stale_detections >= 1


# =============================================================================
# 6. MISSING CANDLE / GAP DETECTION
# =============================================================================

def test_6_missing_candle_gap_detection():
    """Verify candle gaps (>300s between 5m bars) are flagged with missing intervals."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    t0 = 1731300000
    c1 = {"open": 4000.0, "high": 4005.0, "low": 3995.0, "close": 4002.0, "volume": 100.0, "timestamp": t0}
    # Skip two 5m candles: next is t0 + 900s (missing 1 bar in between)
    c2 = {"open": 4002.0, "high": 4008.0, "low": 4000.0, "close": 4006.0, "volume": 150.0, "timestamp": t0 + 900}

    r1 = validator.validate_incoming_candle(c1)
    assert r1.is_valid is True
    assert r1.is_gap is False

    r2 = validator.validate_incoming_candle(c2)
    assert r2.is_valid is True
    assert r2.is_gap is True
    assert r2.gap_duration_seconds == 900.0
    assert r2.missing_intervals == 2
    assert validator.gap_count == 1


# =============================================================================
# 7. SIGNAL GENERATION WITH SYNTHETIC DATA
# =============================================================================

def test_7_signal_generation_synthetic_data():
    """Verify evaluator processes synthetic candles and produces structured ShadowEvaluationEvent."""
    df = _build_synthetic_5m_candles(count=160, base_price=4050.0)
    evaluator = ReadOnlyStrategyEvaluator()

    event = evaluator.evaluate_arbitrated_shadow(df)
    assert event.label == "HYPOTHETICAL — NOT AN ORDER"
    assert event.is_order is False
    assert event.instrument == "XAUTUSD"
    assert event.winning_signal in ("BUY", "SELL", "NO_SIGNAL")
    assert event.market_regime != "UNKNOWN"
    assert len(event.sub_strategy_signals) == 6


# =============================================================================
# 8. REJECTION REASON RECORDING
# =============================================================================

def test_8_rejection_reason_recording():
    """Verify that NO_SIGNAL outcomes clearly articulate rejection reasons."""
    # Build insufficient candles to force warming up / rejection
    short_df = _build_synthetic_5m_candles(count=5)
    evaluator = ReadOnlyStrategyEvaluator()

    event = evaluator.evaluate_arbitrated_shadow(short_df)
    assert event.winning_signal == "NO_SIGNAL"
    assert event.rejection_reason is not None
    assert event.is_warming_up is True or "WARMING_UP" in event.rejection_reason or "UNCERTAIN" in event.market_regime


# =============================================================================
# 9. RECORDER RESTART & PERSISTENCE
# =============================================================================

def test_9_recorder_restart_and_persistence(tmp_path):
    """Verify recorder appends cleanly, persists summary, and reloads across restarts."""
    out_dir = tmp_path / "shadow_test_out"
    recorder = ShadowSignalRecorder(out_dir)

    df = _build_synthetic_5m_candles(count=160)
    evaluator = ReadOnlyStrategyEvaluator()
    evt1 = evaluator.evaluate_arbitrated_shadow(df)
    evt2 = evaluator.evaluate_arbitrated_shadow(df)

    recorder.record_event(evt1)
    recorder.record_event(evt2)
    assert recorder.total_events == 2

    # Simulate process restart
    restarted_recorder = ShadowSignalRecorder(out_dir)
    events = restarted_recorder.load_events()
    assert len(events) == 2
    summary = restarted_recorder.load_summary()
    assert summary["total_events_recorded"] == 2
    assert summary["label"] == "HYPOTHETICAL — NOT AN ORDER"
    assert summary["is_order"] is False


# =============================================================================
# 10. OUTPUT FILE INTEGRITY & SCHEMA VERIFICATION
# =============================================================================

def test_10_output_file_integrity_and_schema(tmp_path):
    """Verify output JSONL files conform to the strict non-order schema."""
    out_dir = tmp_path / "schema_check"
    recorder = ShadowSignalRecorder(out_dir)
    df = _build_synthetic_5m_candles(count=160)
    evaluator = ReadOnlyStrategyEvaluator()

    evt = evaluator.evaluate_arbitrated_shadow(df)
    recorder.record_event(evt)

    events = recorder.load_events()
    assert len(events) == 1
    rec = events[0]

    # Required schema fields
    required_fields = [
        "label", "is_order", "event_id", "timestamp_utc", "timestamp_ist",
        "instrument", "candle_timeframe", "market_regime", "regime_confidence",
        "winning_strategy", "winning_signal", "risk_reward_ratio",
        "signal_quality_score", "sub_strategy_signals"
    ]
    for rf in required_fields:
        assert rf in rec, f"Missing schema field: {rf}"

    assert rec["label"] == "HYPOTHETICAL — NOT AN ORDER"
    assert rec["is_order"] is False


# =============================================================================
# 11. BROKER IMPORT ISOLATION
# =============================================================================

def test_11_broker_import_isolation():
    """Verify that shadow_observer modules do NOT import DeltaPaperBroker execution methods."""
    import inspect
    import services.shadow_observer.strategy_evaluator as strat_eval
    import services.shadow_observer.signal_recorder as sig_rec
    import services.shadow_observer.market_data_reader as mkt_rdr
    import services.shadow_observer.candle_validator as cdl_val
    import services.shadow_observer.observer_safety_guard as sft_grd

    modules_to_inspect = [strat_eval, sig_rec, mkt_rdr, cdl_val, sft_grd]
    for mod in modules_to_inspect:
        source = inspect.getsource(mod)
        assert "open_simulated_trade" not in source, f"Found open_simulated_trade in {mod.__name__}"
        assert "close_simulated_trade" not in source, f"Found close_simulated_trade in {mod.__name__}"
        assert "execute_live_order" not in source, f"Found execute_live_order in {mod.__name__}"
        assert "DeltaPaperBroker" not in source, f"Found DeltaPaperBroker in {mod.__name__}"


# =============================================================================
# 12. ZERO BROKER STATE MUTATION
# =============================================================================

def test_12_zero_broker_state_mutation(tmp_path):
    """Verify that evaluating shadow strategies leaves existing broker state, cash, and incident records unchanged."""
    from services.delta_paper_broker import DeltaPaperBroker

    # Create dummy broker state
    state_file = tmp_path / "broker_untouched.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="ISOLATION_KEY")
    initial_cash = broker.cash_balance
    initial_pos = dict(broker.open_position)
    initial_closed = list(broker.closed_trades)
    initial_records_count = broker._idempotency_registry.count_records()

    # Now execute shadow observer evaluations
    df = _build_synthetic_5m_candles(count=160)
    evaluator = ReadOnlyStrategyEvaluator()
    out_dir = tmp_path / "shadow_isolated"
    recorder = ShadowSignalRecorder(out_dir)

    for _ in range(5):
        event = evaluator.evaluate_arbitrated_shadow(df)
        recorder.record_event(event)

    # Re-inspect broker state
    assert broker.cash_balance == initial_cash, "Cash balance must not change"
    assert broker.open_position == initial_pos, "Open position must not change"
    assert broker.closed_trades == initial_closed, "Closed trades must not change"
    assert broker._idempotency_registry.count_records() == initial_records_count, "SQLite records must not change"

    # Verify official historical forward state file is unchanged
    hist_file = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    assert hist_file.exists()
    hist_data = json.loads(hist_file.read_text(encoding="utf-8"))
    incident = next((t for t in hist_data["closed_trades"] if t.get("trade_id") == "PAPER_20261105_101000_LONG"), None)
    assert incident is not None, "PAPER_20261105_101000_LONG must remain intact"
    assert incident["lots"] == 484
    assert incident["net_pnl"] == -7.9472


# =============================================================================
# 13. NO ORDER ENDPOINT ACCESS
# =============================================================================

def test_13_no_order_endpoint_access():
    """Verify that public market data reader only points to public URLs without order endpoints."""
    reader = PublicMarketDataReader()
    telemetry = reader.get_feed_telemetry()

    assert "api.india.delta.exchange" in telemetry["public_rest_url"]
    assert "socket.india.delta.exchange" in telemetry["public_ws_url"]
    assert "orders" not in telemetry["public_rest_url"]
    assert "v2/orders" not in telemetry["public_rest_url"]


# =============================================================================
# 14. NO PRIVATE CREDENTIAL ACCESS & WRITE PATH ENFORCEMENT
# =============================================================================

def test_14_no_private_credential_access_and_path_enforcement(tmp_path):
    """Verify safety guard rejects forbidden write targets and path escapes."""
    # 1. Attempting to write to protected state files must raise ShadowObserverSafetyViolation
    for forbidden in ["paper_account_xautusd.json", "paper_forward_24h_state.json", ".env"]:
        with pytest.raises(ShadowObserverSafetyViolation, match="SECURITY VIOLATION"):
            validate_shadow_write_path(tmp_path / forbidden)

    # 2. Attempting to write outside allowed shadow directory must raise ShadowObserverSafetyViolation
    allowed_dir = tmp_path / "allowed_shadow"
    allowed_dir.mkdir()
    escape_target = tmp_path / "unauthorized_dir" / "rogue.json"
    with pytest.raises(ShadowObserverSafetyViolation, match="SECURITY VIOLATION"):
        validate_shadow_write_path(escape_target, allowed_root=allowed_dir)

    # 3. Block execution attempt raises
    with pytest.raises(ShadowObserverSafetyViolation, match="CRITICAL SECURITY VIOLATION"):
        block_execution_attempt()

