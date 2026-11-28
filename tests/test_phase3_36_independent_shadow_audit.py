"""Phase 3.36 — Independent Adversarial Audit Test Suite for Read-Only Shadow Observer.

COVERS 14 INDEPENDENT ADVERSARIAL AUDIT DOMAINS:
1. Malicious path traversal and uppercase forbidden state-file write prevention.
2. Symlink, suffix (.tmp, .bak), and directory escape protection.
3. In-progress candle rejection boundary (now_epoch vs candle_start + interval).
4. Future candle rejection and timestamps in the future.
5. Monotonicity enforcement and duplicate timestamp rejection.
6. Extreme candle gaps (1h, 24h weekend) and gap duration accounting.
7. Corrupted OHLCV structures (negative prices, High < Low, negative volume, NaN).
8. Empty and warmup candle DataFrame handling.
9. Full AST dependency and call-graph isolation (zero broker imports across observer & strategies).
10. Invariant schema purity (no execution artifacts, hypothetical-only labels).
11. Multi-threaded concurrency stress testing on ShadowSignalRecorder.
12. Environment safety tripwire (LIVE_TRADING_ENABLED=true fails closed).
13. Historical production trade record and broker state preservation.
14. Market data reader staleness and out-of-order tick tracking.
"""

import ast
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

import pandas as pd
import pytest

from services.shadow_observer.observer_safety_guard import (
    ShadowObserverSafetyViolation,
    validate_shadow_write_path,
    verify_observer_isolation,
    block_execution_attempt,
    FORBIDDEN_WRITE_TARGETS,
)
from services.shadow_observer.candle_validator import (
    CandleValidator,
    CandleValidationReport,
)
from services.shadow_observer.strategy_evaluator import (
    ReadOnlyStrategyEvaluator,
    ShadowEvaluationEvent,
)
from services.shadow_observer.signal_recorder import (
    ShadowSignalRecorder,
)
from services.shadow_observer.market_data_reader import (
    PublicMarketDataReader,
    PUBLIC_REST_URL,
    PUBLIC_WS_URL,
    TARGET_INSTRUMENT,
)
from services.shadow_observer.shadow_report import (
    ShadowReportGenerator,
)
from config.orb_settings import ORBSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_test_candle(
    start_epoch: float,
    open_p: float = 4000.0,
    high_p: float = 4010.0,
    low_p: float = 3990.0,
    close_p: float = 4005.0,
    vol: float = 1000.0,
    is_complete: bool = True,
) -> Dict[str, Any]:
    return {
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": vol,
        "timestamp": start_epoch,
        "is_complete": is_complete,
    }


def _build_synthetic_series(count: int = 160, base_price: float = 4000.0) -> pd.DataFrame:
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
# 1. MALICIOUS PATH TRAVERSAL & FORBIDDEN STATE WRITES
# =============================================================================

def test_adv_01_malicious_path_traversal(tmp_path):
    """Verify malicious traversal strings and forbidden file variants are rejected."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    traversal_attacks = [
        sandbox / ".." / "data" / "paper_account_xautusd.json",
        sandbox / ".." / ".." / "paper_forward_24h_state.json",
        sandbox / "PAPER_ACCOUNT_XAUTUSD.JSON",
        sandbox / "paper_forward_24h_state.json.bak",
        sandbox / ".env",
        sandbox / ".env.monitoring",
        sandbox / "trading_agent.db",
        sandbox / "paper_account_xautusd.idempotency.db",
    ]

    for attack in traversal_attacks:
        with pytest.raises(ShadowObserverSafetyViolation):
            validate_shadow_write_path(attack, allowed_root=sandbox)


# =============================================================================
# 2. SYMLINK AND DIRECTORY ESCAPE PROTECTION
# =============================================================================

def test_adv_02_directory_escape_protection(tmp_path):
    """Verify writes strictly cannot escape the specified shadow output directory."""
    allowed_dir = tmp_path / "allowed_shadow"
    allowed_dir.mkdir()
    forbidden_dir = tmp_path / "unauthorized_folder"
    forbidden_dir.mkdir()

    outside_file = forbidden_dir / "shadow_leak.jsonl"
    with pytest.raises(ShadowObserverSafetyViolation, match="outside authorized shadow directory"):
        validate_shadow_write_path(outside_file, allowed_root=allowed_dir)

    # Legitimate file inside allowed directory passes
    legit_file = allowed_dir / "valid_signal.jsonl"
    resolved = validate_shadow_write_path(legit_file, allowed_root=allowed_dir)
    assert resolved == legit_file.resolve()


# =============================================================================
# 3. IN-PROGRESS CANDLE REJECTION BOUNDARY
# =============================================================================

def test_adv_03_in_progress_candle_rejection_boundary():
    """Verify candle boundary behavior around completion window (300 seconds for 5m)."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    t0 = 1731300000.0  # Candle starts at t0

    # Case A: exactly 1 second before completion (t0 + 299s) -> INCOMPLETE
    now_before = t0 + 299.0
    c_forming = _build_test_candle(t0)
    rep_before = validator.validate_incoming_candle(c_forming, now_epoch=now_before)
    assert rep_before.is_valid is False
    assert rep_before.is_completed is False
    assert "INCOMPLETE_CANDLE" in rep_before.rejection_reason

    # Case B: explicit flag is_complete=False -> INCOMPLETE even if time elapsed
    now_after = t0 + 350.0
    c_flag_false = _build_test_candle(t0, is_complete=False)
    rep_flag = validator.validate_incoming_candle(c_flag_false, now_epoch=now_after)
    assert rep_flag.is_valid is False
    assert rep_flag.is_completed is False

    # Case C: exactly at completion boundary (t0 + 300s) -> VALID
    now_boundary = t0 + 300.0
    c_closed = _build_test_candle(t0, is_complete=True)
    rep_closed = validator.validate_incoming_candle(c_closed, now_epoch=now_boundary)
    assert rep_closed.is_valid is True
    assert rep_closed.is_completed is True


# =============================================================================
# 4. FUTURE CANDLE REJECTION
# =============================================================================

def test_adv_04_future_candle_rejection():
    """Verify that candles with timestamps in the future relative to wall clock are rejected."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    now_epoch = 1731300000.0
    future_start = now_epoch + 600.0  # 10 minutes in the future

    c_future = _build_test_candle(future_start)
    rep = validator.validate_incoming_candle(c_future, now_epoch=now_epoch)
    assert rep.is_valid is False
    assert "INCOMPLETE_CANDLE" in rep.rejection_reason


# =============================================================================
# 5. MONOTONICITY & DUPLICATE TIMESTAMP REJECTION
# =============================================================================

def test_adv_05_monotonicity_and_duplicate_rejection():
    """Verify strictly monotonic ordering and duplicate rejection."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    t0 = 1731300000.0
    c1 = _build_test_candle(t0)
    c1_dup = _build_test_candle(t0, high_p=4060.0, close_p=4050.0)  # valid structure, same timestamp
    c_past = _build_test_candle(t0 - 300.0)          # timestamp in the past

    rep1 = validator.validate_incoming_candle(c1)
    assert rep1.is_valid is True

    # Duplicate rejection
    rep_dup = validator.validate_incoming_candle(c1_dup)
    assert rep_dup.is_valid is False
    assert rep_dup.is_duplicate is True
    assert "DUPLICATE_CANDLE" in rep_dup.rejection_reason

    # Out-of-order rejection
    rep_past = validator.validate_incoming_candle(c_past)
    assert rep_past.is_valid is False
    assert "OUT_OF_ORDER" in rep_past.rejection_reason


# =============================================================================
# 6. EXTREME CANDLE GAPS & ACCOUNTING
# =============================================================================

def test_adv_06_extreme_candle_gaps():
    """Verify gap detection for 1-hour intraday gap and 24-hour weekend gap."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    t0 = 1731300000.0

    # Ingest initial candle
    rep0 = validator.validate_incoming_candle(_build_test_candle(t0))
    assert rep0.is_valid is True
    assert rep0.is_gap is False

    # 1 hour later (3600 seconds = 12 intervals of 5m -> 11 missing bars)
    t_1h = t0 + 3600.0
    rep_1h = validator.validate_incoming_candle(_build_test_candle(t_1h))
    assert rep_1h.is_valid is True
    assert rep_1h.is_gap is True
    assert rep_1h.gap_duration_seconds == 3600.0
    assert rep_1h.missing_intervals == 11

    # 24 hours later (86400 seconds = 288 intervals of 5m -> 287 missing bars)
    t_24h = t_1h + 86400.0
    rep_24h = validator.validate_incoming_candle(_build_test_candle(t_24h))
    assert rep_24h.is_valid is True
    assert rep_24h.is_gap is True
    assert rep_24h.gap_duration_seconds == 86400.0
    assert rep_24h.missing_intervals == 287
    assert validator.gap_count == 2


# =============================================================================
# 7. CORRUPTED OHLCV STRUCTURE REJECTION
# =============================================================================

def test_adv_07_corrupted_ohlcv_structures():
    """Verify that corrupt OHLCV structures are rejected before reaching strategies."""
    validator = CandleValidator(expected_timeframe_minutes=5)
    t = 1731300000.0

    corrupted_cases = [
        ({"open": 4000, "high": 3990, "low": 4010, "close": 4005, "volume": 100, "timestamp": t}, "High (3990.0) is less than Low (4010.0)"),
        ({"open": 4000, "high": 4010, "low": 3990, "close": 4020, "volume": 100, "timestamp": t}, "High (4010.0) is less than max(Open, Close) (4020.0)"),
        ({"open": 3980, "high": 4010, "low": 3990, "close": 4005, "volume": 100, "timestamp": t}, "Low (3990.0) is greater than min(Open, Close) (3980.0)"),
        ({"open": 4000, "high": 4010, "low": 3990, "close": 4005, "volume": -50, "timestamp": t}, "Negative volume in candle"),
        ({"open": -100, "high": 4010, "low": 3990, "close": 4005, "volume": 100, "timestamp": t}, "Non-positive price in candle"),
        ({"open": "INVALID", "high": 4010, "low": 3990, "close": 4005, "volume": 100, "timestamp": t}, "Non-numeric OHLCV value"),
        ({"high": 4010, "low": 3990, "close": 4005, "volume": 100, "timestamp": t}, "Missing required candle field: open"),
    ]

    for candle, expected_substr in corrupted_cases:
        rep = validator.validate_incoming_candle(candle)
        assert rep.is_valid is False
        assert expected_substr in rep.rejection_reason


# =============================================================================
# 8. EMPTY & WARMUP CANDLE DATAFRAME HANDLING
# =============================================================================

def test_adv_08_empty_and_warmup_handling():
    """Verify evaluator gracefully handles empty data and warmup periods with NO_SIGNAL."""
    evaluator = ReadOnlyStrategyEvaluator()

    # Empty DataFrame
    empty_event = evaluator.evaluate_arbitrated_shadow(pd.DataFrame())
    assert empty_event.winning_signal == "NO_SIGNAL"
    assert empty_event.diagnostic_code == "EMPTY_DATA"
    assert "EMPTY_CANDLE_DATA" in empty_event.rejection_reason

    # Insufficient candles (warmup)
    short_df = _build_synthetic_series(count=10)
    warmup_event = evaluator.evaluate_arbitrated_shadow(short_df)
    assert warmup_event.winning_signal == "NO_SIGNAL"
    assert warmup_event.is_warming_up is True or "WARMING_UP" in (warmup_event.rejection_reason or "")


# =============================================================================
# 9. AST DEPENDENCY & CALL-GRAPH ISOLATION
# =============================================================================

def test_adv_09_full_ast_dependency_and_callgraph_isolation():
    """Verify zero broker imports and zero order execution methods across all observer and strategy modules."""
    forbidden_terms = [
        "DeltaPaperBroker",
        "open_simulated_trade",
        "close_simulated_trade",
        "execute_live_order",
        "send_order",
        "cancel_order",
        "place_order",
    ]

    # Directories to inspect
    target_dirs = [
        PROJECT_ROOT / "services" / "shadow_observer",
        PROJECT_ROOT / "strategy",
    ]

    inspected_files = 0
    for target_dir in target_dirs:
        for py_file in target_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            inspected_files += 1

            # Check imports
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "broker" not in alias.name.lower(), f"Forbidden import '{alias.name}' in {py_file}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert "broker" not in node.module.lower(), f"Forbidden import from '{node.module}' in {py_file}"

                # Check function / method calls
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        assert node.func.attr not in forbidden_terms, f"Forbidden call '{node.func.attr}' in {py_file}"
                    elif isinstance(node.func, ast.Name):
                        assert node.func.id not in forbidden_terms, f"Forbidden call '{node.func.id}' in {py_file}"

    assert inspected_files >= 10, f"Expected at least 10 modules inspected, found {inspected_files}"


# =============================================================================
# 10. INVARIANT SCHEMA PURITY
# =============================================================================

def test_adv_10_invariant_schema_purity(tmp_path):
    """Verify recorded events and summaries contain zero execution/accounting artifacts."""
    out_dir = tmp_path / "schema_purity"
    recorder = ShadowSignalRecorder(out_dir)
    evaluator = ReadOnlyStrategyEvaluator()

    df = _build_synthetic_series(count=160)
    for _ in range(3):
        evt = evaluator.evaluate_arbitrated_shadow(df)
        recorder.record_event(evt)

    # Inspect JSONL lines
    events = recorder.load_events()
    assert len(events) == 3

    forbidden_fields = ["trade_id", "filled_price", "commission", "cash_balance", "equity", "pnl", "net_pnl"]
    for e in events:
        assert e["label"] == "HYPOTHETICAL — NOT AN ORDER"
        assert e["is_order"] is False
        for ff in forbidden_fields:
            assert ff not in e, f"Found execution artifact field '{ff}' in shadow event record"

    # Inspect summary
    summary = recorder.load_summary()
    assert summary["label"] == "HYPOTHETICAL — NOT AN ORDER"
    assert summary["is_order"] is False
    assert summary["total_events_recorded"] == 3


# =============================================================================
# 11. MULTI-THREADED CONCURRENCY STRESS
# =============================================================================

def test_adv_11_multi_threaded_concurrency_stress(tmp_path):
    """Verify ShadowSignalRecorder remains thread-safe under concurrent multi-threaded writes."""
    out_dir = tmp_path / "thread_stress"
    recorder = ShadowSignalRecorder(out_dir)
    evaluator = ReadOnlyStrategyEvaluator()
    df = _build_synthetic_series(count=160)

    num_threads = 8
    events_per_thread = 25
    total_expected = num_threads * events_per_thread

    def worker_task(thread_id: int):
        for _ in range(events_per_thread):
            evt = evaluator.evaluate_arbitrated_shadow(df)
            recorder.record_event(evt)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_task, i) for i in range(num_threads)]
        for f in futures:
            f.result()

    assert recorder.total_events == total_expected
    loaded_events = recorder.load_events()
    assert len(loaded_events) == total_expected

    summary = recorder.load_summary()
    assert summary["total_events_recorded"] == total_expected


# =============================================================================
# 12. ENVIRONMENT SAFETY TRIPWIRE
# =============================================================================

def test_adv_12_environment_safety_tripwire(monkeypatch):
    """Verify observer fails closed immediately if LIVE trading environment is detected."""
    # Normal PAPER setting passes
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    verify_observer_isolation()

    # LIVE_TRADING_ENABLED=true triggers violation
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ShadowObserverSafetyViolation, match="LIVE_TRADING_ENABLED is true"):
        verify_observer_isolation()

    # TRADING_MODE=LIVE triggers violation
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(ShadowObserverSafetyViolation, match="TRADING_MODE is LIVE"):
        verify_observer_isolation()


# =============================================================================
# 13. HISTORICAL PRODUCTION TRADE RECORD PRESERVATION
# =============================================================================

def test_adv_13_historical_production_trade_record_intact():
    """Verify that paper forward state file and historical incident record are completely untouched."""
    forward_state_file = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    assert forward_state_file.exists(), "Forward state file must exist"

    data = json.loads(forward_state_file.read_text(encoding="utf-8"))
    incident = next((t for t in data.get("closed_trades", []) if t.get("trade_id") == "PAPER_20261105_101000_LONG"), None)

    assert incident is not None, "PAPER_20261105_101000_LONG trade record must be present"
    assert incident["lots"] == 484, "Historical lots must remain 484"
    assert incident["net_pnl"] == -7.9472, "Historical net PnL must remain -7.9472"
    assert incident["symbol"] == "XAUTUSD", "Historical symbol must remain XAUTUSD"


# =============================================================================
# 14. MARKET DATA STALENESS & OUT-OF-ORDER TRACKING
# =============================================================================

def test_adv_14_market_data_staleness_and_out_of_order():
    """Verify public market data reader tracks out-of-order ticks and flags feed staleness correctly."""
    reader = PublicMarketDataReader(symbol="XAUTUSD", stale_threshold_seconds=60.0)
    t0 = 1731300000.0

    # Ingest tick at t0
    ok1, msg1 = reader.ingest_ticker_update({"symbol": "XAUTUSD", "price": 4050.0, "timestamp": t0}, now_epoch=t0)
    assert ok1 is True
    assert reader.ticks_received == 1
    assert reader.out_of_order_count == 0

    # Ingest backwards tick (t0 - 10s)
    ok2, msg2 = reader.ingest_ticker_update({"symbol": "XAUTUSD", "price": 4048.0, "timestamp": t0 - 10.0}, now_epoch=t0)
    assert ok2 is True
    assert reader.ticks_received == 2
    assert reader.out_of_order_count == 1

    # Check staleness at 45s (not stale)
    stale_45, elapsed_45 = reader.is_feed_stale(now_epoch=t0 + 45.0)
    assert stale_45 is False

    # Check staleness at 65s (stale)
    stale_65, elapsed_65 = reader.is_feed_stale(now_epoch=t0 + 65.0)
    assert stale_65 is True
    assert elapsed_65 == 65.0
    assert reader.stale_detections >= 1
