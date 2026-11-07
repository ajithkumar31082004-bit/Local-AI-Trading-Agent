"""Unit tests for Stage 1 Read-Only Public Market-Data Observer.

Verifies:
1. Strict read-only safety invariants and pre-flight abort conditions.
2. Private endpoint detection and rejection.
3. Write path isolation inside reports/stage1_readonly/.
4. Ticker and trade message processing, duplicate detection, and out-of-order detection.
5. Candle processing, duplicate detection, out-of-order detection, and gap analysis.
6. Feed health metrics, disconnect tracking, and stale feed intervals.
7. Output artifact generation and schema integrity.
8. Zero broker, strategy, or daemon interaction.
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from scripts.stage1_readonly_market_monitor import (
    Stage1MarketObserver,
    SafetyViolation,
    assert_runtime_safety_invariants,
    validate_write_path,
    OUTPUT_DIR,
)


@pytest.fixture
def isolated_observer(tmp_path):
    """Instantiate Stage1MarketObserver targeting an isolated test directory."""
    test_dir = tmp_path / "stage1_test_output"
    test_dir.mkdir(parents=True, exist_ok=True)
    obs = Stage1MarketObserver(
        symbol="XAUTUSD",
        product_id=131253,
        duration_seconds=60,
        output_dir=test_dir,
    )
    return obs, test_dir


# =============================================================================
# 1. Safety Assertions & Abort Conditions
# =============================================================================

def test_safety_invariants_pass_on_default_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_TRADING_ACKNOWLEDGED", "NO")
    # In shared pytest runner, previous test suites import trading modules.
    # Isolate sys.modules for clean observer preflight test:
    forbidden_modules = [
        "services.delta_paper_broker",
        "services.delta_paper_bot",
        "services.delta_live_executor",
        "services.risk_manager",
        "strategy.arbitrator",
    ]
    for mod in forbidden_modules:
        monkeypatch.delitem(sys.modules, mod, raising=False)

    res = assert_runtime_safety_invariants()
    assert res["trading_mode"] == "PAPER"
    assert res["live_trading_enabled"] is False
    assert res["zero_broker_calls"] is True
    assert res["zero_orders_submitted"] is True


def test_safety_invariants_abort_on_live_trading_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(SafetyViolation, match="Unsafe TRADING_MODE"):
        assert_runtime_safety_invariants()


def test_safety_invariants_abort_on_live_trading_enabled(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(SafetyViolation, match="Unsafe LIVE_TRADING_ENABLED"):
        assert_runtime_safety_invariants()


def test_safety_invariants_abort_on_live_trading_acknowledged(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_TRADING_ACKNOWLEDGED", "YES")
    with pytest.raises(SafetyViolation, match="Unsafe LIVE_TRADING_ACKNOWLEDGED"):
        assert_runtime_safety_invariants()


def test_validate_write_path_blocks_escape():
    safe_path = OUTPUT_DIR / "stage1_test.json"
    assert validate_write_path(safe_path) == safe_path

    unsafe_path = OUTPUT_DIR.parent / "escape.json"
    with pytest.raises(SafetyViolation, match="Security Breach"):
        validate_write_path(unsafe_path)


def test_private_endpoint_detection_raises_safety_violation():
    # 1. Private paths forbidden
    with pytest.raises(SafetyViolation, match="not in public allowlist"):
        Stage1MarketObserver(rest_url="https://api.india.delta.exchange/v2/orders")

    with pytest.raises(SafetyViolation, match="not in public allowlist"):
        Stage1MarketObserver(rest_url="https://api.india.delta.exchange/v2/wallet/balances")

    # 2. Untrusted host forbidden
    with pytest.raises(SafetyViolation, match="Host 'untrusted.socket.com' not in public allowlist"):
        Stage1MarketObserver(ws_url="wss://untrusted.socket.com")

    # 3. Invalid scheme forbidden
    with pytest.raises(SafetyViolation, match="Invalid URL scheme 'http'"):
        Stage1MarketObserver(rest_url="http://api.india.delta.exchange")

    with pytest.raises(SafetyViolation, match="Invalid URL scheme 'ws'"):
        Stage1MarketObserver(ws_url="ws://socket.india.delta.exchange")

    # 4. Non-standard port forbidden
    with pytest.raises(SafetyViolation, match="Non-standard port '8088' strictly forbidden"):
        Stage1MarketObserver(rest_url="https://api.india.delta.exchange:8088")

    # 5. Non-allowlisted path forbidden
    with pytest.raises(SafetyViolation, match="REST path '/v2/admin' not in public allowlist"):
        Stage1MarketObserver(rest_url="https://api.india.delta.exchange/v2/admin")


def test_dynamic_import_block_hook():
    """Verify sys.meta_path hook prevents dynamic importing of trading modules."""
    import importlib
    with pytest.raises(SafetyViolation, match="Dynamic import of trading/execution module strictly blocked"):
        importlib.import_module("services.delta_paper_broker")

    with pytest.raises(SafetyViolation, match="Dynamic import of trading/execution module strictly blocked"):
        importlib.import_module("strategy.arbitrator")


# =============================================================================
# 2. Ticker & Trade Message Processing
# =============================================================================

def test_ticker_processing_valid_and_duplicate(isolated_observer):
    obs, _ = isolated_observer
    
    # 1. Process valid ticker
    ticker_1 = {
        "type": "v2/ticker",
        "symbol": "XAUTUSD",
        "close": "4050.50",
        "mark_price": "4050.50",
        "timestamp": 1730912400000000,
    }
    obs.process_raw_message(json.dumps(ticker_1))
    assert obs.total_ticks == 1
    assert obs.valid_ticks == 1
    assert obs.duplicate_ticks == 0

    # 2. Process exact duplicate ticker
    obs.process_raw_message(json.dumps(ticker_1))
    assert obs.total_ticks == 2
    assert obs.valid_ticks == 2
    assert obs.duplicate_ticks == 1


def test_ticker_out_of_order_detection(isolated_observer):
    obs, _ = isolated_observer

    # 1. New tick at time T=100
    obs.process_ticker({
        "symbol": "XAUTUSD",
        "timestamp": 100000000,
        "close": "4050.0",
    })
    assert obs.out_of_order_ticks == 0

    # 2. Tick with older timestamp T=90
    obs.process_ticker({
        "symbol": "XAUTUSD",
        "timestamp": 90000000,
        "close": "4049.0",
    })
    assert obs.out_of_order_ticks == 1


def test_foreign_symbol_ignored(isolated_observer):
    obs, _ = isolated_observer
    obs.process_raw_message(json.dumps({
        "type": "v2/ticker",
        "symbol": "BTCUSD",
        "close": "70000.0",
        "timestamp": 1730912400000000,
    }))
    assert obs.total_ticks == 0
    assert obs.valid_ticks == 0


# =============================================================================
# 3. Candle Processing, Gaps & Integrity
# =============================================================================

def test_candle_finalization_and_duplicate_detection(isolated_observer):
    obs, _ = isolated_observer

    # Bar 1: Forming bar starting at T=0
    c1 = {
        "type": "candlestick_5m",
        "symbol": "XAUTUSD",
        "candle_start_time": 0,
        "open": 4000.0,
        "high": 4010.0,
        "low": 3995.0,
        "close": 4005.0,
        "volume": 10.0,
    }
    obs.process_raw_message(json.dumps(c1))
    assert obs.candle_count == 0  # Still forming

    # Bar 2: Advances to T=300 -> Bar 1 completed
    c2 = {
        "type": "candlestick_5m",
        "symbol": "XAUTUSD",
        "candle_start_time": 300_000_000,  # 300s in microseconds
        "open": 4005.0,
        "high": 4015.0,
        "low": 4002.0,
        "close": 4012.0,
        "volume": 15.0,
    }
    obs.process_raw_message(json.dumps(c2))
    assert obs.candle_count == 1
    assert obs.last_completed_candle_start_ts == 0
    assert len(obs.candle_records) == 1
    assert obs.candle_records[0]["close"] == 4005.0

    # Redundant bar 1 start received later
    c1_dup = {
        "type": "candlestick_5m",
        "symbol": "XAUTUSD",
        "candle_start_time": 0,
        "open": 4000.0,
        "high": 4010.0,
        "low": 3995.0,
        "close": 4005.0,
        "volume": 10.0,
    }
    obs.current_forming_start_ts = 0
    obs.current_forming_bar = {"start_ts": 0, "open": 4000, "high": 4010, "low": 3995, "close": 4005, "volume": 10}
    c3 = {
        "type": "candlestick_5m",
        "symbol": "XAUTUSD",
        "candle_start_time": 600_000_000,
        "open": 4012.0,
        "high": 4020.0,
        "low": 4010.0,
        "close": 4018.0,
        "volume": 20.0,
    }
    obs.process_raw_message(json.dumps(c3))
    assert obs.duplicate_candles == 1


def test_candle_gap_detection(isolated_observer):
    obs, _ = isolated_observer

    # Bar 1 at T=0
    obs.current_forming_start_ts = 0
    obs.current_forming_bar = {"start_ts": 0, "open": 4000, "high": 4010, "low": 3995, "close": 4005, "volume": 10}

    # Bar 2 at T=300 (normal continuous step)
    obs.process_candlestick({
        "symbol": "XAUTUSD",
        "candle_start_time": 300_000_000,
        "open": 4005, "high": 4015, "low": 4002, "close": 4012, "volume": 15,
    })
    assert obs.candle_count == 1
    assert obs.missing_candles == 0

    # Bar 3 at T=1200 (gap of 900s -> 2 missing 5m candles: T=600, T=900)
    obs.process_candlestick({
        "symbol": "XAUTUSD",
        "candle_start_time": 1200_000_000,
        "open": 4020, "high": 4030, "low": 4018, "close": 4025, "volume": 25,
    })
    assert obs.candle_count == 2
    assert obs.missing_candles == 2  # (1200 - 300 - 300) // 300 = 2 missing bars


# =============================================================================
# 4. Artifact Generation & Output Verification
# =============================================================================

def test_output_artifacts_generation(isolated_observer, monkeypatch):
    obs, test_dir = isolated_observer
    monkeypatch.setattr(obs, "output_dir", test_dir)
    obs.start_utc = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
    obs.start_ist = datetime(2026, 11, 6, 17, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    obs.end_utc = datetime(2026, 11, 6, 13, 0, 0, tzinfo=timezone.utc)
    obs.end_ist = datetime(2026, 11, 6, 18, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    obs.total_ticks = 1500
    obs.valid_ticks = 1500
    obs.candle_count = 12
    obs.safety_assertions = {
        "trading_mode": "PAPER",
        "live_trading_enabled": False,
        "zero_broker_calls": True,
        "zero_orders_submitted": True,
        "zero_strategy_evaluations": True,
        "zero_service_modifications": True,
    }

    # Generate artifacts
    obs._write_output_artifacts(cpu_pct=1.5, memory_mb=45.2)

    # 1. Verify stage1_summary.json
    summary_file = test_dir / "stage1_summary.json"
    assert summary_file.exists()
    with open(summary_file, "r", encoding="utf-8") as f:
        summary_data = json.load(f)
    assert summary_data["final_status"] == "PASS"
    assert summary_data["telemetry"]["total_ticks_received"] == 1500
    assert summary_data["telemetry"]["completed_candles"] == 12
    assert summary_data["resource_usage"]["cpu_utilization_pct"] == 1.5

    # 2. Verify stage1_candle_health.csv
    csv_file = test_dir / "stage1_candle_health.csv"
    assert csv_file.exists()

    # 3. Verify STAGE1_READONLY_REPORT.md
    report_file = test_dir / "STAGE1_READONLY_REPORT.md"
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "PASS — PUBLIC FEED STABLE" in content
    assert "THE TRADING DAEMON REMAINS STOPPED" in content
    assert "XAUTUSD" in content


# =============================================================================
# 5. Feed Reliability, Stale Intervals & Unsafe Module Detection
# =============================================================================

def test_safety_invariants_abort_on_forbidden_module_loaded(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_TRADING_ACKNOWLEDGED", "NO")
    # Temporarily inject forbidden module into sys.modules
    monkeypatch.setitem(sys.modules, "services.delta_paper_broker", object())
    with pytest.raises(SafetyViolation, match="Unsafe module loaded in interpreter: services.delta_paper_broker"):
        assert_runtime_safety_invariants()


def test_stale_feed_interval_tracking(isolated_observer):
    obs, _ = isolated_observer
    obs.start_utc = datetime.now(timezone.utc)
    # Set last feed update to 75 seconds ago
    obs.last_feed_update_ts = datetime.now(timezone.utc) - timedelta(seconds=75)
    obs._check_feed_staleness()
    assert obs.stale_feed_intervals == 1
    assert obs.longest_interruption_seconds >= 75.0


def test_rest_fallback_query_success(isolated_observer, monkeypatch):
    obs, _ = isolated_observer

    class MockResponse:
        status_code = 200
        def json(self):
            return {"result": [{"symbol": "XAUTUSD", "close": "4050.0"}]}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: MockResponse())
    obs._perform_rest_fallback_check()
    assert obs.rest_fallback_events == 1


def test_trade_processing_valid_and_duplicate(isolated_observer):
    obs, _ = isolated_observer
    trade_1 = {
        "type": "all_trades",
        "symbol": "XAUTUSD",
        "price": "4050.25",
        "size": 1,
        "timestamp": 1730912420000000,
        "id": "T12345",
    }
    obs.process_raw_message(json.dumps(trade_1))
    assert obs.total_ticks == 1
    assert obs.valid_ticks == 1
    assert obs.duplicate_ticks == 0

    # Repeat exact trade
    obs.process_raw_message(json.dumps(trade_1))
    assert obs.total_ticks == 2
    assert obs.valid_ticks == 2
    assert obs.duplicate_ticks == 1

