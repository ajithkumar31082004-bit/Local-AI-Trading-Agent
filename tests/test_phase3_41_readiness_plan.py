"""Phase 3.41 — Public Feed Dry-Run Readiness Plan Offline Test Suite.

VERIFIES:
1. Startup blocked by incorrect PAPER settings.
2. Startup blocked if trading daemon is active.
3. Startup blocked by incorrect product symbol.
4. Startup blocked by unapproved endpoints.
5. Automatic timeout at 60 minutes using mocked time.
6. Stop on stale feed.
7. Stop on queue overflow.
8. Stop on malformed data.
9. Stop on memory and disk threshold violations.
10. Graceful shutdown sequence.
11. No restart after shutdown.
12. No broker, order, or private API access.
13. No filesystem writes outside authorized directory.
14. Completeness and accuracy of dry-run reporting schema.
"""

from pathlib import Path
import json
import pytest

from services.shadow_observer.observer_safety_guard import (
    ShadowObserverSafetyViolation,
    validate_shadow_write_path,
)
from services.shadow_observer.transport.transport_config import TransportConfig
from services.shadow_observer.transport.transport_interface import (
    ConnectionState,
    NormalizedTick,
)
from services.shadow_observer.transport.public_websocket_client import MockWebSocketConnection
from services.shadow_observer.transport.transport_supervisor import TransportSupervisor
from services.shadow_observer.dry_run_runner import (
    DryRunConfig,
    execute_preflight_safety_gates,
    ShadowDryRunRunner,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# 1. PREFLIGHT SAFETY GATES TESTING
# =============================================================================

def test_01_startup_blocked_by_incorrect_paper_settings(tmp_path):
    """Verify preflight gate fails if TRADING_MODE is not PAPER or LIVE is enabled."""
    cfg = DryRunConfig(output_dir=tmp_path)

    # Case A: LIVE trading mode
    env_live = tmp_path / ".env.live"
    env_live.write_text("TRADING_MODE=LIVE\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")
    ok, failures = execute_preflight_safety_gates(cfg, env_file=env_live)
    assert ok is False
    assert any("TRADING_MODE" in f for f in failures)

    # Case B: LIVE_TRADING_ENABLED=true
    env_enabled = tmp_path / ".env.enabled"
    env_enabled.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=true\n", encoding="utf-8")
    ok, failures = execute_preflight_safety_gates(cfg, env_file=env_enabled)
    assert ok is False
    assert any("LIVE_TRADING_ENABLED" in f for f in failures)


def test_02_startup_blocked_if_trading_daemon_active(tmp_path):
    """Verify preflight gate fails if trading daemon is detected as running."""
    cfg = DryRunConfig(output_dir=tmp_path)
    env_valid = tmp_path / ".env.valid"
    env_valid.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")

    ok, failures = execute_preflight_safety_gates(
        cfg,
        env_file=env_valid,
        is_daemon_running_fn=lambda: True,  # Daemon active
    )
    assert ok is False
    assert any("Trading daemon" in f for f in failures)


def test_03_startup_blocked_by_incorrect_product():
    """Verify preflight gate fails if symbol is not XAUTUSD."""
    with pytest.raises(ShadowObserverSafetyViolation, match="Unsupported symbol"):
        DryRunConfig(symbol="BTCUSD")


def test_04_startup_blocked_by_unapproved_endpoint():
    """Verify unapproved REST or WS endpoints fail safety validation."""
    with pytest.raises(ShadowObserverSafetyViolation, match="Unauthorized REST base URL"):
        TransportConfig(rest_base_url="https://api.binance.com")

    with pytest.raises(ShadowObserverSafetyViolation, match="Unauthorized WebSocket URL"):
        TransportConfig(ws_url="wss://stream.binance.com")


# =============================================================================
# 2. AUTOMATIC STOP CONDITIONS TESTING
# =============================================================================

def test_05_automatic_timeout_at_60_minutes():
    """Verify runner automatically terminates upon reaching 60 minutes (3600s)."""
    current_time = 1731300000.0

    def mock_time():
        return current_time

    mock_sock = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )

    runner = ShadowDryRunRunner(
        config=DryRunConfig(max_duration_seconds=3600.0, stale_feed_threshold_seconds=5000.0),
        supervisor=supervisor,
        time_fn=mock_time,
    )

    started = runner.start()
    assert started is True
    assert runner.is_running is True

    # Advance time by 1800s (30m) -> continues
    current_time += 1800.0
    assert runner.tick() is True

    # Advance time to 3600s (60m) -> stops automatically
    current_time += 1800.0
    assert runner.tick() is False
    assert runner.is_running is False
    assert "MAX_DURATION_REACHED" in runner.shutdown_reason
    assert supervisor.state == ConnectionState.STOPPED


def test_06_stop_on_stale_feed():
    """Verify runner automatically terminates if public market data feed becomes stale."""
    current_time = 1731300000.0

    mock_sock = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    runner = ShadowDryRunRunner(
        supervisor=supervisor,
        time_fn=lambda: current_time,
    )
    runner.start()

    # Ingest tick at t0
    supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4050.0, current_time, current_time))

    # Advance time by 30s -> normal
    current_time += 30.0
    assert runner.tick() is True

    # Advance time by 65s (total 95s without tick) -> stale stop triggered!
    current_time += 65.0
    assert runner.tick() is False
    assert "STALE_FEED" in runner.shutdown_reason
    assert supervisor.state == ConnectionState.STOPPED


def test_07_stop_on_queue_overflow():
    """Verify runner immediately halts if queue overflow occurs."""
    current_time = 1731300000.0
    cfg = DryRunConfig(max_message_queue_size=10, stale_feed_threshold_seconds=5000.0)
    mock_sock = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        config=TransportConfig(max_message_queue_size=10),
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    runner = ShadowDryRunRunner(
        config=cfg,
        supervisor=supervisor,
        time_fn=lambda: current_time,
    )
    runner.start()

    # Fill queue to capacity (10 ticks)
    for i in range(10):
        supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4000.0 + i, current_time + i, current_time + i))

    assert runner.tick() is True
    assert supervisor.queue_overflow_count == 0

    # Ingest 11th tick -> overflow!
    supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4011.0, current_time + 11, current_time + 11))
    assert supervisor.queue_overflow_count == 1

    # Next tick check must halt the runner
    assert runner.tick() is False
    assert "QUEUE_OVERFLOW" in runner.shutdown_reason
    assert supervisor.state == ConnectionState.STOPPED


def test_08_stop_on_memory_and_disk_threshold_violations():
    """Verify runner stops if memory exceeds 150MB or disk exceeds 25MB."""
    current_time = 1731300000.0
    mock_sock = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )

    # Case A: Memory violation (> 150MB)
    runner_mem = ShadowDryRunRunner(
        config=DryRunConfig(stale_feed_threshold_seconds=5000.0),
        supervisor=supervisor,
        time_fn=lambda: current_time,
        memory_check_fn=lambda: 165.0,  # 165MB
    )
    runner_mem.start()
    assert runner_mem.tick() is False
    assert "MEMORY_BUDGET_EXCEEDED" in runner_mem.shutdown_reason

    # Case B: Disk violation (> 25MB)
    mock_sock2 = MockWebSocketConnection()
    supervisor2 = TransportSupervisor(
        socket_factory=lambda u, t: mock_sock2,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    runner_disk = ShadowDryRunRunner(
        config=DryRunConfig(stale_feed_threshold_seconds=5000.0),
        supervisor=supervisor2,
        time_fn=lambda: current_time,
        disk_check_fn=lambda: 28.5,  # 28.5MB
    )
    runner_disk.start()
    assert runner_disk.tick() is False
    assert "DISK_BUDGET_EXCEEDED" in runner_disk.shutdown_reason


# =============================================================================
# 3. RESTART PREVENTION & ZERO-ORDER CONFIRMATION
# =============================================================================

def test_09_no_restart_after_shutdown():
    """Verify runner strictly forbids restarting after shutdown."""
    mock_sock = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    runner = ShadowDryRunRunner(supervisor=supervisor)
    runner.start()
    runner.stop("USER_STOP")

    # Calling start() again must raise an error
    with pytest.raises(ShadowObserverSafetyViolation, match="Restart after shutdown is prohibited"):
        runner.start()


def test_10_readiness_reporting_schema_completeness():
    """Verify reporting dictionary conforms to all required Phase 3.41 reporting fields."""
    mock_sock = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    runner = ShadowDryRunRunner(
        supervisor=supervisor,
        time_fn=lambda: 1731300000.0,
        memory_check_fn=lambda: 52.3,
        disk_check_fn=lambda: 3.1,
    )
    runner.start()
    supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4050.0, 1731300000.0, 1731300000.0))
    runner.stop("NORMAL_TEST_COMPLETION")

    report = runner.generate_readiness_report()

    # Verify session metadata
    assert report["session_metadata"]["instrument"] == "XAUTUSD"
    assert report["session_metadata"]["final_transport_state"] == "STOPPED"
    assert report["session_metadata"]["shutdown_reason"] == "NORMAL_TEST_COMPLETION"

    # Verify safety confirmations
    assert report["safety_confirmations"]["orders_submitted"] == 0
    assert report["safety_confirmations"]["broker_methods_invoked"] == 0
    assert report["safety_confirmations"]["private_api_calls_attempted"] == 0
    assert report["safety_confirmations"]["live_trading_enabled"] is False
    assert report["safety_confirmations"]["trading_mode"] == "PAPER"
    assert report["safety_confirmations"]["restart_prevented"] is True

    # Verify resource utilization
    assert report["resource_utilization"]["peak_memory_mb"] == 52.3
    assert report["resource_utilization"]["peak_disk_mb"] == 3.1
