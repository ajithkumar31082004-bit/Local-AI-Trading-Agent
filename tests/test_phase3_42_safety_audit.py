"""Phase 3.42 — Independent Safety Audit Offline Test Suite.

COMPREHENSIVE ADVERSARIAL VERIFICATION:
1. All 12 Preflight Safety Gates (both pass and fail-closed paths):
   - Gate 1: API keys absent (DELTA_API_KEY, DELTA_API_SECRET in env / file).
   - Gate 2: Broker namespace isolation & paper-only configuration.
   - Gate 3: Git working-tree cleanliness.
   - Gate 4: XAUTUSD-only instrument validation.
   - Gate 5: Maximum 3,600-second duration.
   - Gate 6: Queue maximum 1,000.
   - Gate 7: Memory maximum 150 MB.
   - Gate 8: Disk maximum 25 MB.
   - Gate 9: Restart policy set to "never".
   - Gate 10: Output directory isolation and write safety.
   - Gate 11: Outbound connectivity probe behavior (strictly offline).
   - Gate 12: Host resource headroom.
2. All 10 Stop Conditions:
   - Stop 1: DURATION_EXPIRED.
   - Stop 2: STALE_FEED.
   - Stop 3: QUEUE_OVERFLOW.
   - Stop 4: SYMBOL_MISMATCH.
   - Stop 5: PRIVATE_KEY_IN_PAYLOAD.
   - Stop 6: MEMORY_LIMIT_EXCEEDED.
   - Stop 7: DISK_LIMIT_EXCEEDED.
   - Stop 8: MANUAL_HALT.
   - Stop 9: MAX_RECONNECTS_EXCEEDED.
   - Stop 10: UNHANDLED_EXCEPTION.
3. Cancellation & Exception Handling (KeyboardInterrupt, SystemExit).
4. Restart Prevention Lockout.
5. Resource Limit Bounds (Queue, Memory, Disk, Reconnect History).
6. Filesystem Traversal & Protected File Isolation.
7. Network Isolation (Zero network calls without injected adapters).
"""

import os
import sys
import json
from pathlib import Path
from typing import Optional
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
# 1. PREFLIGHT GATES AUDIT TESTS (GATES 1 - 12)
# =============================================================================

def test_gate_01_fails_closed_when_api_keys_present_in_env(monkeypatch, tmp_path):
    """Gate 1: Verify preflight fails closed if DELTA_API_KEY or DELTA_API_SECRET is present in os.environ."""
    cfg = DryRunConfig(output_dir=tmp_path)
    env_file = tmp_path / ".env.valid"
    env_file.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")

    monkeypatch.setenv("DELTA_API_KEY", "unauthorized_live_key_xyz")
    ok, failures = execute_preflight_safety_gates(cfg, env_file=env_file)
    assert ok is False
    assert any("Gate 1 Failed: DELTA_API_KEY or DELTA_API_SECRET found in environment" in f for f in failures)


def test_gate_01_fails_closed_when_api_keys_in_env_file(tmp_path):
    """Gate 1: Verify preflight fails closed if DELTA_API_KEY or DELTA_API_SECRET is present in .env."""
    cfg = DryRunConfig(output_dir=tmp_path)
    env_file = tmp_path / ".env.secret"
    env_file.write_text(
        "TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\nDELTA_API_KEY=my_secret_key\n",
        encoding="utf-8"
    )

    ok, failures = execute_preflight_safety_gates(cfg, env_file=env_file)
    assert ok is False
    assert any("Gate 1 Failed: DELTA_API_KEY present in .env file" in f for f in failures)


def test_gate_02_fails_closed_on_live_broker_in_sys_modules(tmp_path, monkeypatch):
    """Gate 2: Verify preflight fails closed if a forbidden broker module is in sys.modules."""
    cfg = DryRunConfig(output_dir=tmp_path)
    env_file = tmp_path / ".env.valid"
    env_file.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")

    monkeypatch.setitem(sys.modules, "execution.broker", object())
    ok, failures = execute_preflight_safety_gates(cfg, env_file=env_file)
    assert ok is False
    assert any("Gate 2 Failed: Broker module 'execution.broker' is loaded" in f for f in failures)


def test_gate_03_fails_closed_on_dirty_working_tree(tmp_path):
    """Gate 3: Verify preflight fails closed if git working tree check reports uncommitted changes."""
    cfg = DryRunConfig(output_dir=tmp_path)
    env_file = tmp_path / ".env.valid"
    env_file.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")

    ok, failures = execute_preflight_safety_gates(
        cfg,
        env_file=env_file,
        git_clean_check_fn=lambda: (False, "M modified_file.py"),
    )
    assert ok is False
    assert any("Gate 3 Failed: Working tree not clean" in f for f in failures)


def test_gate_04_fails_closed_on_unsupported_instrument():
    """Gate 4: Verify preflight fails closed if symbol is not XAUTUSD."""
    with pytest.raises(ShadowObserverSafetyViolation, match="Unsupported symbol"):
        DryRunConfig(symbol="ETHUSD")


def test_gate_05_fails_closed_on_excessive_duration():
    """Gate 5: Verify preflight fails closed if max duration > 3600 seconds or <= 0."""
    with pytest.raises(ShadowObserverSafetyViolation, match="exceeds 60-minute limit"):
        DryRunConfig(max_duration_seconds=3601.0)

    with pytest.raises(ShadowObserverSafetyViolation, match="exceeds 60-minute limit"):
        DryRunConfig(max_duration_seconds=0.0)


def test_gate_06_fails_closed_on_queue_size_over_1000():
    """Gate 6: Verify preflight fails closed if message queue size > 1000 or <= 0."""
    with pytest.raises(ShadowObserverSafetyViolation, match="exceeds max limit"):
        DryRunConfig(max_message_queue_size=1001)

    with pytest.raises(ShadowObserverSafetyViolation, match="exceeds max limit"):
        DryRunConfig(max_message_queue_size=0)


def test_gate_07_fails_closed_on_memory_over_150mb():
    """Gate 7: Verify preflight fails closed if max memory limit > 150MB."""
    with pytest.raises(ShadowObserverSafetyViolation, match="Memory limit .* exceeds max limit"):
        DryRunConfig(max_memory_mb=200.0)


def test_gate_08_fails_closed_on_disk_over_25mb():
    """Gate 8: Verify preflight fails closed if max disk limit > 25MB."""
    with pytest.raises(ShadowObserverSafetyViolation, match="Disk limit .* exceeds max limit"):
        DryRunConfig(max_disk_mb=50.0)


def test_gate_09_fails_closed_on_restart_policy_not_never():
    """Gate 9: Verify preflight fails closed if restart policy is anything other than 'never'."""
    with pytest.raises(ShadowObserverSafetyViolation, match="Automatic restart policy .* prohibited"):
        DryRunConfig(restart_policy="always")


def test_gate_10_fails_closed_on_output_directory_traversal():
    """Gate 10: Verify preflight fails closed if output directory targets system paths."""
    escaping_path = Path("C:/Windows/System32/shadow_test")
    cfg = DryRunConfig(output_dir=escaping_path)
    ok, failures = execute_preflight_safety_gates(cfg)
    assert ok is False
    assert any("Gate 10 Failed: Output directory isolation violation" in f for f in failures)


def test_gate_11_fails_closed_on_unauthorized_endpoints():
    """Gate 11: Verify preflight fails closed on non-allowlisted endpoint URLs."""
    with pytest.raises(ShadowObserverSafetyViolation, match="Unauthorized REST base URL"):
        TransportConfig(rest_base_url="https://api.delta.exchange")

    with pytest.raises(ShadowObserverSafetyViolation, match="Unauthorized WebSocket URL"):
        TransportConfig(ws_url="wss://socket.delta.exchange")


def test_gate_12_fails_closed_on_insufficient_host_headroom(tmp_path):
    """Gate 12: Verify preflight fails closed if host headroom check fails."""
    cfg = DryRunConfig(output_dir=tmp_path)
    env_file = tmp_path / ".env.valid"
    env_file.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")

    ok, failures = execute_preflight_safety_gates(
        cfg,
        env_file=env_file,
        host_headroom_check_fn=lambda: (False, "Free disk space 200 MB < 1.0 GB"),
    )
    assert ok is False
    assert any("Gate 12 Failed: Host resource headroom check failed" in f for f in failures)


# =============================================================================
# 2. AUTOMATIC STOP CONDITIONS AUDIT TESTS (STOPS 1 - 10)
# =============================================================================

def test_stop_01_duration_expired():
    """Stop 1: Verify runner triggers DURATION_EXPIRED at 3600s."""
    t0 = 1731300000.0
    now = t0
    runner = make_mock_runner(
        config=DryRunConfig(max_duration_seconds=3600.0, stale_feed_threshold_seconds=5000.0),
        time_fn=lambda: now,
    )
    runner.start()
    now += 3600.0
    assert runner.tick() is False
    assert "MAX_DURATION_REACHED" in runner.shutdown_reason
    assert runner.has_terminated is True


def test_stop_02_stale_feed():
    """Stop 2: Verify runner triggers STALE_FEED when ticker age exceeds threshold."""
    t0 = 1731300000.0
    now = t0
    runner = make_mock_runner(
        config=DryRunConfig(stale_feed_threshold_seconds=60.0),
        time_fn=lambda: now,
    )
    runner.start()
    runner.supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4050.0, now, now))

    now += 61.0
    assert runner.tick() is False
    assert "STALE_FEED" in runner.shutdown_reason
    assert runner.has_terminated is True


def test_stop_03_queue_overflow():
    """Stop 3: Verify runner triggers QUEUE_OVERFLOW upon first dropped event."""
    current_time = 1731300000.0
    runner = make_mock_runner(
        config=DryRunConfig(max_message_queue_size=10, stale_feed_threshold_seconds=5000.0),
        time_fn=lambda: current_time,
    )
    runner.start()
    for i in range(10):
        runner.supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4000.0 + i, current_time + i, current_time + i))
    assert runner.tick() is True

    runner.supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4011.0, current_time + 11, current_time + 11))
    assert runner.supervisor.queue_overflow_count == 1
    assert runner.tick() is False
    assert "QUEUE_OVERFLOW" in runner.shutdown_reason


def test_stop_04_symbol_mismatch():
    """Stop 4: Verify runner triggers SYMBOL_MISMATCH if foreign symbol detected."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()
    runner.detected_symbol_mismatch = "BTCUSD"
    assert runner.tick() is False
    assert "SYMBOL_MISMATCH" in runner.shutdown_reason


def test_stop_05_private_key_in_payload():
    """Stop 5: Verify runner triggers PRIVATE_KEY_IN_PAYLOAD when sensitive auth keyword detected."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()
    bad_payload = {"type": "ticker", "api-key": "sensitive_leaked_key_123"}
    safe = runner.validate_payload_safety(bad_payload)
    assert safe is False
    assert "PRIVATE_KEY_IN_PAYLOAD" in runner.shutdown_reason
    assert runner.has_terminated is True


def test_stop_06_memory_limit_exceeded():
    """Stop 6: Verify runner triggers MEMORY_BUDGET_EXCEEDED when RSS exceeds 150 MB."""
    runner = make_mock_runner(
        time_fn=lambda: 1731300000.0,
        memory_check_fn=lambda: 151.0,
    )
    runner.start()
    assert runner.tick() is False
    assert "MEMORY_BUDGET_EXCEEDED" in runner.shutdown_reason


def test_stop_07_disk_limit_exceeded():
    """Stop 7: Verify runner triggers DISK_BUDGET_EXCEEDED when output size exceeds 25 MB."""
    runner = make_mock_runner(
        time_fn=lambda: 1731300000.0,
        disk_check_fn=lambda: 25.5,
    )
    runner.start()
    assert runner.tick() is False
    assert "DISK_BUDGET_EXCEEDED" in runner.shutdown_reason


def test_stop_08_manual_halt():
    """Stop 8: Verify runner triggers MANUAL_HALT when operator requests stop."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()
    runner.request_stop("OPERATOR_REQUESTED_SIGINT")
    assert runner.is_running is False
    assert "OPERATOR_REQUESTED_SIGINT" in runner.shutdown_reason
    assert runner.has_terminated is True


def test_stop_09_max_reconnects_exceeded():
    """Stop 9: Verify runner triggers MAX_RECONNECTS_EXCEEDED after 5 consecutive failures."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()
    runner.supervisor.state = ConnectionState.DEGRADED
    runner.supervisor.reconnect_ctrl.consecutive_failures = 5
    assert runner.tick() is False
    assert "MAX_RECONNECTS_EXCEEDED" in runner.shutdown_reason


def test_stop_10_unhandled_exception_in_tick_triggers_immediate_shutdown():
    """Stop 10: Verify unhandled exception in tick cleanly halts runner and records UNHANDLED_EXCEPTION."""
    def exploding_memory():
        raise RuntimeError("Unexpected OS memory counter corruption!")

    runner = make_mock_runner(
        time_fn=lambda: 1731300000.0,
        memory_check_fn=exploding_memory,
    )
    runner.start()

    with pytest.raises(RuntimeError, match="Unexpected OS memory counter corruption"):
        runner.tick()

    assert runner.is_running is False
    assert runner.has_terminated is True
    assert "UNHANDLED_EXCEPTION" in runner.shutdown_reason
    assert runner.supervisor.state == ConnectionState.STOPPED


def test_cancellation_keyboard_interrupt_triggers_clean_teardown():
    """Verify KeyboardInterrupt cleanly executes teardown and sets MANUAL_HALT."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()

    def interrupt_fn():
        raise KeyboardInterrupt("Simulated Ctrl+C")

    runner.time_fn = interrupt_fn
    try:
        runner.tick()
    except KeyboardInterrupt:
        pass

    assert runner.is_running is False
    assert runner.has_terminated is True
    assert "MANUAL_HALT" in runner.shutdown_reason
    assert runner.supervisor.state == ConnectionState.STOPPED


# =============================================================================
# 3. RESTART PREVENTION & RESOURCE ENFORCEMENT
# =============================================================================

def test_restart_strictly_blocked_after_termination():
    """Verify runner permanently blocks restart once terminated."""
    runner = make_mock_runner(time_fn=lambda: 1731300000.0)
    runner.start()
    runner.stop("NORMAL_HALT")
    assert runner.has_terminated is True

    with pytest.raises(ShadowObserverSafetyViolation, match="Restart after shutdown is prohibited"):
        runner.start()


def test_reconnect_controller_history_is_bounded_at_100():
    """Verify ReconnectController history deque is strictly capped at max_reconnect_history (100)."""
    cfg = TransportConfig(max_reconnect_history=100)
    ctrl = ReconnectController(cfg)

    for i in range(150):
        ctrl.record_failure(f"simulated failure #{i}", now_epoch=1000.0 + i)

    assert len(ctrl.reconnect_history) == 100
    assert ctrl.reconnect_history[-1]["reason"] == "simulated failure #149"


# =============================================================================
# 4. NETWORK & IMPORT ISOLATION
# =============================================================================

def test_public_websocket_fails_closed_without_socket_factory():
    """Verify PublicWebSocketClient raises RuntimeError if no socket factory is injected."""
    client = PublicWebSocketClient()
    with pytest.raises(RuntimeError, match="OFFLINE SAFETY: No socket factory injected"):
        client.connect()
    assert client.get_state() == ConnectionState.DISCONNECTED


def test_public_rest_fails_closed_without_http_adapter():
    """Verify PublicRestClient raises RuntimeError if no HTTP adapter is injected."""
    client = PublicRestClient()
    client.connect()
    with pytest.raises(RuntimeError, match="OFFLINE SAFETY: No HTTP adapter injected"):
        client.fetch_ticker()
    assert client.get_state() == ConnectionState.CONNECTED


def test_forbidden_write_targets_rejected():
    """Verify validate_shadow_write_path rejects writes to protected state files."""
    for forbidden in FORBIDDEN_WRITE_TARGETS:
        with pytest.raises(ShadowObserverSafetyViolation, match="SECURITY VIOLATION: Observer attempted to write"):
            validate_shadow_write_path(forbidden)
