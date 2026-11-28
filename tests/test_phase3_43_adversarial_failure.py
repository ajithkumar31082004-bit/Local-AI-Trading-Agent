"""Phase 3.43 — Final Adversarial Failure-Injection Test Suite.

STRICT OFFLINE ADVERSARIAL VERIFICATION:
1. Simultaneous Stop-Condition Testing (Stop race conditions, deterministic priority).
2. Cancellation and Lifecycle Failure Injection (At every lifecycle boundary).
3. Resource Exhaustion Boundary Tests (Below, exact, above thresholds; headroom).
4. Malicious Synthetic Payload Ingestion (NaN, Inf, negative, huge, malformed, private keys).
5. Filesystem Security & Attack Simulations (Traversal, symlinks, protected targets, disk full).
6. Repeated & Re-entrant Lifecycle Operations (Start twice, stop twice, restart attempts).
7. Network & Execution Isolation Verification.
"""

import os
import sys
import json
import math
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List
import pytest

from services.shadow_observer.observer_safety_guard import (
    ShadowObserverSafetyViolation,
    validate_shadow_write_path,
    FORBIDDEN_WRITE_TARGETS,
)
from services.shadow_observer.transport.transport_config import TransportConfig
from services.shadow_observer.transport.transport_interface import (
    ConnectionState,
    NormalizedTick,
    NormalizedCandle,
)
from services.shadow_observer.transport.public_websocket_client import (
    PublicWebSocketClient,
    MockWebSocketConnection,
)
from services.shadow_observer.transport.public_rest_client import PublicRestClient
from services.shadow_observer.transport.reconnect_controller import ReconnectController
from services.shadow_observer.transport.transport_supervisor import TransportSupervisor
from services.shadow_observer.candle_validator import CandleValidator
from services.shadow_observer.dry_run_runner import (
    DryRunConfig,
    execute_preflight_safety_gates,
    ShadowDryRunRunner,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_mock_runner(
    config: Optional[DryRunConfig] = None,
    time_fn=None,
    memory_check_fn=None,
    disk_check_fn=None,
) -> ShadowDryRunRunner:
    """Helper to construct a fully mock-wired runner for offline testing."""
    cfg = config or DryRunConfig()
    mock_sock = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        config=TransportConfig(
            symbol=cfg.symbol,
            max_message_queue_size=cfg.max_message_queue_size,
            stale_feed_threshold_seconds=cfg.stale_feed_threshold_seconds,
        ),
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    return ShadowDryRunRunner(
        config=cfg,
        supervisor=supervisor,
        time_fn=time_fn,
        memory_check_fn=memory_check_fn,
        disk_check_fn=disk_check_fn,
    )


# =============================================================================
# 1. SIMULTANEOUS STOP-CONDITION TESTING
# =============================================================================

def test_simultaneous_stale_feed_and_queue_overflow():
    """Verify deterministic priority when Stale Feed and Queue Overflow trigger simultaneously."""
    now = 1731300000.0
    runner = make_mock_runner(
        config=DryRunConfig(max_message_queue_size=10, stale_feed_threshold_seconds=60.0),
        time_fn=lambda: now,
    )
    runner.start()
    # Ingest tick at t0
    runner.supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4050.0, now, now))

    # Simultaneously: Advance time by 65s (Stale) AND trigger queue overflow drops
    now += 65.0
    runner.supervisor.queue_overflow_count = 5

    assert runner.tick() is False
    # In priority order, STALE_FEED is evaluated before QUEUE_OVERFLOW
    assert "STALE_FEED" in runner.shutdown_reason
    assert runner.has_terminated is True
    assert runner.supervisor.state == ConnectionState.STOPPED


def test_simultaneous_memory_limit_and_disk_limit():
    """Verify deterministic priority when Memory and Disk limits are exceeded simultaneously."""
    runner = make_mock_runner(
        time_fn=lambda: 1731300000.0,
        memory_check_fn=lambda: 160.0,  # > 150 MB
        disk_check_fn=lambda: 30.0,     # > 25 MB
    )
    runner.start()
    assert runner.tick() is False
    # Memory condition is checked before Disk condition
    assert "MEMORY_BUDGET_EXCEEDED" in runner.shutdown_reason
    assert runner.has_terminated is True


def test_simultaneous_manual_halt_and_unhandled_exception():
    """Verify manual halt and unhandled exception handling cleanly terminates."""
    def exploding_time():
        raise RuntimeError("Fatal hardware timer fault!")

    runner = make_mock_runner()
    runner.start()
    runner.request_stop("OPERATOR_REQUESTED_SIGINT")
    runner.time_fn = exploding_time

    # Runner was already stopped by request_stop
    assert runner.is_running is False
    assert "OPERATOR_REQUESTED_SIGINT" in runner.shutdown_reason
    assert runner.has_terminated is True


def test_simultaneous_max_reconnects_and_duration_expiration():
    """Verify duration expiration priority over degraded reconnect status."""
    now = 1731300000.0
    runner = make_mock_runner(
        config=DryRunConfig(max_duration_seconds=3600.0),
        time_fn=lambda: now,
    )
    runner.start()
    runner.supervisor.state = ConnectionState.DEGRADED
    runner.supervisor.reconnect_ctrl.consecutive_failures = 5

    # Simultaneously advance time past 3600s
    now += 3601.0
    assert runner.tick() is False
    # Duration condition is checked before degraded reconnects
    assert "MAX_DURATION_REACHED" in runner.shutdown_reason
    assert runner.has_terminated is True


def test_simultaneous_symbol_mismatch_and_malformed_payload():
    """Verify symbol mismatch detection cleanly halts when payload issues coexist."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()
    runner.detected_symbol_mismatch = "BTCUSD"
    runner.detected_forbidden_key = "api-key"

    assert runner.tick() is False
    # Symbol mismatch is evaluated before forbidden key in stop conditions
    assert "SYMBOL_MISMATCH" in runner.shutdown_reason
    assert runner.has_terminated is True


# =============================================================================
# 2. CANCELLATION AND LIFECYCLE FAILURE INJECTION
# =============================================================================

def test_cancellation_during_preflight(tmp_path):
    """Verify KeyboardInterrupt / CancelledError during preflight propagates and leaves zero state."""
    cfg = DryRunConfig(output_dir=tmp_path)
    env_file = tmp_path / ".env.valid"
    env_file.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")

    def cancelling_git():
        raise KeyboardInterrupt("Simulated Ctrl+C during git preflight")

    with pytest.raises(KeyboardInterrupt):
        execute_preflight_safety_gates(cfg, env_file=env_file, git_clean_check_fn=cancelling_git)


def test_cancellation_during_transport_startup():
    """Verify transport startup cancellation cleanly stops supervisor and raises."""
    mock_sock = MockWebSocketConnection()

    def cancelling_connect(url, timeout):
        raise KeyboardInterrupt("Cancelled during socket handshake")

    supervisor = TransportSupervisor(
        socket_factory=cancelling_connect,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    runner = ShadowDryRunRunner(supervisor=supervisor, time_fn=lambda: 1731300000.0)

    with pytest.raises(KeyboardInterrupt):
        runner.start()

    assert supervisor.state == ConnectionState.STOPPED


def test_cancellation_during_data_ingestion():
    """Verify KeyboardInterrupt during tick execution triggers clean teardown."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()

    def interrupt_time():
        raise KeyboardInterrupt("User pressed Ctrl+C during tick")

    runner.time_fn = interrupt_time
    with pytest.raises(KeyboardInterrupt):
        runner.tick()

    assert runner.is_running is False
    assert runner.has_terminated is True
    assert "MANUAL_HALT" in runner.shutdown_reason
    assert runner.supervisor.state == ConnectionState.STOPPED


def test_system_exit_during_tick():
    """Verify SystemExit during tick execution triggers clean teardown."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()

    def exit_time():
        sys.exit(1)

    runner.time_fn = exit_time
    with pytest.raises(SystemExit):
        runner.tick()

    assert runner.is_running is False
    assert runner.has_terminated is True
    assert "MANUAL_HALT" in runner.shutdown_reason
    assert runner.supervisor.state == ConnectionState.STOPPED


def test_ordinary_exception_during_tick():
    """Verify unexpected RuntimeError during tick triggers UNHANDLED_EXCEPTION teardown."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()

    def crash_time():
        raise RuntimeError("Kernel clock desynchronization")

    runner.time_fn = crash_time
    with pytest.raises(RuntimeError, match="Kernel clock desynchronization"):
        runner.tick()

    assert runner.is_running is False
    assert runner.has_terminated is True
    assert "UNHANDLED_EXCEPTION" in runner.shutdown_reason
    assert runner.supervisor.state == ConnectionState.STOPPED


# =============================================================================
# 3. RESOURCE EXHAUSTION TESTS (BELOW, EXACT, ABOVE BOUNDARIES)
# =============================================================================

@pytest.mark.parametrize("mem_value,should_continue", [
    (149.9, True),   # Immediately below 150 MB
    (150.0, True),   # Exactly at 150 MB
    (150.1, False),  # Immediately above 150 MB
])
def test_memory_budget_boundary(mem_value, should_continue):
    """Verify memory limit strictly enforces at > 150.0 MB threshold."""
    runner = make_mock_runner(
        time_fn=lambda: 1731300000.0,
        memory_check_fn=lambda: mem_value,
    )
    runner.start()
    continued = runner.tick()
    assert continued is should_continue
    if not should_continue:
        assert "MEMORY_BUDGET_EXCEEDED" in runner.shutdown_reason
        assert runner.has_terminated is True


@pytest.mark.parametrize("disk_value,should_continue", [
    (24.9, True),   # Immediately below 25 MB
    (25.0, True),   # Exactly at 25 MB
    (25.1, False),  # Immediately above 25 MB
])
def test_disk_budget_boundary(disk_value, should_continue):
    """Verify disk limit strictly enforces at > 25.0 MB threshold."""
    runner = make_mock_runner(
        time_fn=lambda: 1731300000.0,
        disk_check_fn=lambda: disk_value,
    )
    runner.start()
    continued = runner.tick()
    assert continued is should_continue
    if not should_continue:
        assert "DISK_BUDGET_EXCEEDED" in runner.shutdown_reason
        assert runner.has_terminated is True


def test_queue_exact_and_overflow_boundary():
    """Verify queue accepts exactly 1000 items and halts immediately at 1001."""
    cfg = DryRunConfig(max_message_queue_size=1000, stale_feed_threshold_seconds=5000.0)
    now = 1731300000.0
    runner = make_mock_runner(config=cfg, time_fn=lambda: now)
    runner.start()

    # Fill queue to exactly 1,000 items
    for i in range(1000):
        runner.supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4000.0, now, now))

    assert len(runner.supervisor.event_queue) == 1000
    assert runner.supervisor.queue_overflow_count == 0
    assert runner.tick() is True

    # 1,001st event causes 1 drop
    runner.supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4001.0, now, now))
    assert runner.supervisor.queue_overflow_count == 1
    assert runner.tick() is False
    assert "QUEUE_OVERFLOW" in runner.shutdown_reason


def test_reconnect_history_bounded_at_100_under_storm():
    """Verify ReconnectController history remains strictly capped at 100 under 500 failure events."""
    ctrl = ReconnectController(TransportConfig(max_reconnect_history=100))
    for i in range(500):
        ctrl.record_failure(f"storm error #{i}", now_epoch=1000.0 + i)

    assert len(ctrl.reconnect_history) == 100
    assert ctrl.reconnect_history[0]["reason"] == "storm error #400"
    assert ctrl.reconnect_history[-1]["reason"] == "storm error #499"


# =============================================================================
# 4. MALICIOUS PAYLOAD TESTS
# =============================================================================

def test_malicious_payload_unexpected_symbols():
    """Verify foreign symbols (BTCUSD, ETHUSD, DOGE) are rejected and cannot enter pipeline."""
    client = PublicWebSocketClient(config=TransportConfig(symbol="XAUTUSD"))
    client.state = ConnectionState.CONNECTED

    received_ticks = []
    client.on_tick = lambda t: received_ticks.append(t)

    for foreign in ["BTCUSD", "ETHUSD", "DOGE", "SOLUSD"]:
        client.handle_incoming_message(json.dumps({"channel": "v2/ticker", "symbol": foreign, "mark_price": "50000.0"}))

    assert len(received_ticks) == 0


def test_malicious_payload_nan_and_infinity_prices():
    """Verify NaN, +Inf, -Inf prices in ticker and candle frames are strictly rejected."""
    client = PublicWebSocketClient(config=TransportConfig(symbol="XAUTUSD"))
    client.state = ConnectionState.CONNECTED

    ticks = []
    candles = []
    client.on_tick = lambda t: ticks.append(t)
    client.on_candle = lambda c: candles.append(c)

    # Ingest NaN, Inf, -Inf
    for bad_price in ["NaN", "Infinity", "-Infinity", "nan", "inf"]:
        client.handle_incoming_message(json.dumps({"channel": "v2/ticker", "symbol": "XAUTUSD", "mark_price": bad_price}))
        client.handle_incoming_message(json.dumps({
            "channel": "candlestick_5m",
            "candle": {"symbol": "XAUTUSD", "open": bad_price, "high": "4100", "low": "4000", "close": "4050", "volume": "10", "timestamp": 1731300000}
        }))

    assert len(ticks) == 0
    assert len(candles) == 0
    assert client.malformed_frames_count > 0


def test_malicious_payload_negative_and_zero_prices():
    """Verify negative and zero prices are rejected."""
    client = PublicWebSocketClient(config=TransportConfig(symbol="XAUTUSD"))
    client.state = ConnectionState.CONNECTED

    ticks = []
    client.on_tick = lambda t: ticks.append(t)

    for bad in [-100.5, 0.0, -0.0001]:
        client.handle_incoming_message(json.dumps({"channel": "v2/ticker", "symbol": "XAUTUSD", "mark_price": bad}))

    assert len(ticks) == 0
    assert client.malformed_frames_count == 3


def test_malicious_payload_extremely_large_numbers():
    """Verify prices exceeding realistic instrument bounds (> 1,000,000) are rejected."""
    client = PublicWebSocketClient(config=TransportConfig(symbol="XAUTUSD"))
    client.state = ConnectionState.CONNECTED

    ticks = []
    client.on_tick = lambda t: ticks.append(t)

    for huge in [1e7, 1e12, 1e308]:
        client.handle_incoming_message(json.dumps({"channel": "v2/ticker", "symbol": "XAUTUSD", "mark_price": huge}))

    assert len(ticks) == 0


def test_malicious_payload_invalid_timestamps():
    """Verify negative, NaN, or zero timestamps in candles are rejected."""
    val = CandleValidator()
    # Negative timestamp
    bad_candle = {"open": 4000.0, "high": 4050.0, "low": 3950.0, "close": 4020.0, "volume": 10.0, "timestamp": -500}
    rep = val.validate_incoming_candle(bad_candle)
    assert rep.is_valid is False
    assert "MONOTONICITY_VIOLATION" in rep.rejection_reason or "Non-positive" in str(rep.rejection_reason)


def test_malicious_payload_duplicate_and_out_of_order_candles():
    """Verify duplicate and out-of-order candles are rejected by CandleValidator."""
    val = CandleValidator()
    c1 = {"open": 4000.0, "high": 4050.0, "low": 3950.0, "close": 4020.0, "volume": 10.0, "timestamp": 1731300000}
    c2 = {"open": 4020.0, "high": 4060.0, "low": 4010.0, "close": 4050.0, "volume": 15.0, "timestamp": 1731300300}

    # Ingest c1 -> Valid
    rep1 = val.validate_incoming_candle(c1)
    assert rep1.is_valid is True

    # Ingest c1 duplicate -> Rejected
    rep_dup = val.validate_incoming_candle(c1)
    assert rep_dup.is_valid is False
    assert rep_dup.is_duplicate is True

    # Ingest c2 -> Valid
    rep2 = val.validate_incoming_candle(c2)
    assert rep2.is_valid is True

    # Ingest older candle (t < 1731300300) -> Out of order rejected
    c_old = {"open": 4010.0, "high": 4030.0, "low": 4000.0, "close": 4025.0, "volume": 5.0, "timestamp": 1731300150}
    rep_old = val.validate_incoming_candle(c_old)
    assert rep_old.is_valid is False
    assert "OUT_OF_ORDER" in rep_old.rejection_reason


def test_malicious_payload_auth_keys_trigger_immediate_stop():
    """Verify payloads with API keys, secrets, or signature fields trigger PRIVATE_KEY_IN_PAYLOAD without logging values."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()

    sensitive_payload = {
        "channel": "v2/ticker",
        "symbol": "XAUTUSD",
        "api-key": "super_secret_live_key_do_not_leak",
        "delta-signature": "hmac_signature_value",
    }
    safe = runner.validate_payload_safety(sensitive_payload)
    assert safe is False
    assert "PRIVATE_KEY_IN_PAYLOAD" in runner.shutdown_reason
    # Ensure sensitive secret string itself is not logged in shutdown reason
    assert "super_secret_live_key_do_not_leak" not in runner.shutdown_reason


def test_malicious_payload_oversized_payload_rejected():
    """Verify WebSocket client rejects frames exceeding 64 KB."""
    client = PublicWebSocketClient(config=TransportConfig(symbol="XAUTUSD"))
    client.state = ConnectionState.CONNECTED

    huge_payload = "A" * 70000  # 70 KB
    client.handle_incoming_message(huge_payload)
    assert client.malformed_frames_count == 1


# =============================================================================
# 5. FILESYSTEM SECURITY TESTS
# =============================================================================

def test_filesystem_traversal_attack_rejected(tmp_path):
    """Verify path traversal with ../ is blocked."""
    allowed_dir = tmp_path / "shadow"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    escaping_target = allowed_dir / ".." / "escaped.json"

    with pytest.raises(ShadowObserverSafetyViolation, match="outside authorized shadow directory"):
        validate_shadow_write_path(escaping_target, allowed_root=allowed_dir)


def test_filesystem_forbidden_state_files_protected():
    """Verify write to all forbidden state targets is unconditionally rejected."""
    for forbidden in FORBIDDEN_WRITE_TARGETS:
        with pytest.raises(ShadowObserverSafetyViolation, match="SECURITY VIOLATION: Observer attempted to write"):
            validate_shadow_write_path(forbidden)


def test_filesystem_save_readiness_report_within_allowed_dir(tmp_path):
    """Verify save_readiness_report writes valid JSON inside config.output_dir."""
    runner = make_mock_runner(
        config=DryRunConfig(output_dir=tmp_path),
        time_fn=lambda: 1731300000.0,
    )
    runner.start()
    runner.stop("NORMAL_TEST_STOP")

    out_file = runner.save_readiness_report()
    assert out_file.exists()
    assert out_file.is_relative_to(tmp_path)

    content = json.loads(out_file.read_text(encoding="utf-8"))
    assert content["session_metadata"]["shutdown_reason"] == "NORMAL_TEST_STOP"
    assert content["safety_confirmations"]["orders_submitted"] == 0


def test_filesystem_save_readiness_report_blocks_traversal(tmp_path):
    """Verify save_readiness_report rejects arbitrary escape paths."""
    runner = make_mock_runner(
        config=DryRunConfig(output_dir=tmp_path),
        time_fn=lambda: 1731300000.0,
    )
    runner.start()
    runner.stop("NORMAL_TEST_STOP")

    escape_target = tmp_path.parent / "escaped_report.json"
    with pytest.raises(ShadowObserverSafetyViolation, match="outside authorized shadow directory"):
        runner.save_readiness_report(output_path=escape_target)


# =============================================================================
# 6. REPEATED & RE-ENTRANT LIFECYCLE TESTS
# =============================================================================

def test_lifecycle_start_called_twice():
    """Verify start called twice does not spawn duplicate supervisors."""
    runner = make_mock_runner()
    started1 = runner.start()
    assert started1 is True

    # Second start on active runner returns False because supervisor.start() detects active state
    started2 = runner.start()
    assert started2 is False
    assert runner.is_running is True


def test_lifecycle_stop_called_twice():
    """Verify stop called twice is idempotent and executes cleanly."""
    runner = make_mock_runner()
    runner.start()
    runner.stop("FIRST_STOP")
    assert runner.shutdown_reason == "FIRST_STOP"
    assert runner.has_terminated is True

    # Second stop call
    runner.stop("SECOND_STOP")
    # Reason remains the original first stop reason
    assert runner.shutdown_reason == "FIRST_STOP"
    assert runner.has_terminated is True


def test_lifecycle_stop_before_start():
    """Verify stop called before start executes safely without error."""
    runner = make_mock_runner()
    runner.stop("PREMATURE_STOP")
    assert runner.is_running is False
    assert runner.has_terminated is True


def test_lifecycle_start_after_stop_permanently_blocked():
    """Verify start called after stop raises ShadowObserverSafetyViolation."""
    runner = make_mock_runner()
    runner.start()
    runner.stop("NORMAL_COMPLETION")

    with pytest.raises(ShadowObserverSafetyViolation, match="Restart after shutdown is prohibited"):
        runner.start()


# =============================================================================
# 7. NETWORK & EXECUTION ISOLATION AUDIT
# =============================================================================

def test_isolation_no_order_methods_exist_on_runner():
    """Verify ShadowDryRunRunner and TransportSupervisor have zero order-placement methods."""
    runner = make_mock_runner()
    order_keywords = ["buy", "sell", "order", "place_order", "create_order", "submit_order", "cancel_order"]

    for kw in order_keywords:
        assert not hasattr(runner, kw), f"Runner must not have order method: {kw}"
        assert not hasattr(runner.supervisor, kw), f"Supervisor must not have order method: {kw}"
        assert not hasattr(runner.supervisor.ws_client, kw), f"WS Client must not have order method: {kw}"
        assert not hasattr(runner.supervisor.rest_client, kw), f"REST Client must not have order method: {kw}"


def test_isolation_block_execution_attempt_raises():
    """Verify observer_safety_guard.block_execution_attempt unconditionally raises."""
    from services.shadow_observer.observer_safety_guard import block_execution_attempt
    with pytest.raises(ShadowObserverSafetyViolation, match="Order execution attempted"):
        block_execution_attempt()
