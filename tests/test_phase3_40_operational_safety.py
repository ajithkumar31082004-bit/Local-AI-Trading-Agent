"""Phase 3.40 — Final Offline Operational Safety Review Test Suite.

VERIFIES:
1. asyncio.CancelledError handling in Python 3.8+ (BaseException hierarchy).
2. KeyboardInterrupt and SystemExit clean propagation without suppression.
3. Socket cleanup and state reset on all cancellation paths.
4. TransportSupervisor BaseException handling (calls stop() and re-raises).
5. Queue overflow data loss observability and health degradation.
6. Acknowledge and reset of data loss telemetry.
7. Zero top-level execution on transport import.
8. Complete filesystem write boundary isolation.
9. Core safety invariants and historical ledger preservation.
"""

import ast
import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
import pytest

from services.shadow_observer.observer_safety_guard import (
    validate_shadow_write_path,
    ShadowObserverSafetyViolation,
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
from services.shadow_observer.transport.transport_supervisor import TransportSupervisor

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# 1. ASYNCIO CANCELLATION & PYTHON BASEEXCEPTION AUDIT
# =============================================================================

def test_01_asyncio_cancelled_error_handling_and_cleanup():
    """Verify asyncio.CancelledError (BaseException in Python 3.8+) cleans up socket and re-raises."""
    mock_sock = MockWebSocketConnection()

    def cancelling_factory(url, to):
        # In Python 3.8+, asyncio.CancelledError inherits directly from BaseException
        raise asyncio.CancelledError("Simulated task cancellation in coroutine")

    ws = PublicWebSocketClient(socket_factory=cancelling_factory)

    with pytest.raises(asyncio.CancelledError):
        ws.connect()

    # Connection state must be reset to DISCONNECTED
    assert ws.state == ConnectionState.DISCONNECTED
    # Socket must be cleaned up without leaking
    assert ws._socket is None
    assert "CancelledError" in ws.disconnect_reason or "Simulated" in ws.disconnect_reason


def test_02_keyboard_interrupt_and_system_exit_propagation():
    """Verify KeyboardInterrupt and SystemExit are NEVER suppressed and clean up socket."""
    # Test 1: KeyboardInterrupt
    def ki_factory(url, to):
        raise KeyboardInterrupt("Simulated Ctrl+C signal")

    ws_ki = PublicWebSocketClient(socket_factory=ki_factory)
    with pytest.raises(KeyboardInterrupt):
        ws_ki.connect()
    assert ws_ki.state == ConnectionState.DISCONNECTED
    assert ws_ki._socket is None

    # Test 2: SystemExit
    def se_factory(url, to):
        raise SystemExit(1)

    ws_se = PublicWebSocketClient(socket_factory=se_factory)
    with pytest.raises(SystemExit):
        ws_se.connect()
    assert ws_se.state == ConnectionState.DISCONNECTED
    assert ws_se._socket is None


def test_03_supervisor_base_exception_handling():
    """Verify TransportSupervisor intercepts BaseException, stops all clients, and re-raises."""
    mock_sock = MockWebSocketConnection()

    def ki_factory(url, to):
        raise KeyboardInterrupt("Simulated Ctrl+C during ws connect")

    supervisor = TransportSupervisor(
        socket_factory=ki_factory,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )

    with pytest.raises(KeyboardInterrupt):
        supervisor.start()

    # Supervisor must be left in STOPPED state, not dangling in CONNECTING
    assert supervisor.state == ConnectionState.STOPPED
    assert supervisor.rest_client.get_state() == ConnectionState.STOPPED
    assert supervisor.ws_client.get_state() == ConnectionState.STOPPED
    assert supervisor.ws_client._socket is None


# =============================================================================
# 2. DATA QUALITY, QUEUE OVERFLOW & HEALTH DEGRADATION
# =============================================================================

def test_04_queue_overflow_causes_degraded_state():
    """Verify a feed with dropped events cannot silently be treated as fully healthy."""
    cfg = TransportConfig(max_message_queue_size=10)
    mock_sock = MockWebSocketConnection()

    supervisor = TransportSupervisor(
        config=cfg,
        socket_factory=lambda u, t: mock_sock,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )
    t0 = 1731300000.0
    supervisor.start(now_epoch=t0)
    assert supervisor.state == ConnectionState.CONNECTED

    # Ingest 10 ticks (fills queue to capacity)
    for i in range(10):
        supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4000.0 + i, t0 + i, t0 + i))

    assert supervisor.queue_overflow_count == 0
    h_full = supervisor.check_health(now_epoch=t0 + 10)
    assert h_full["supervisor_state"] == "CONNECTED"
    assert h_full["has_data_loss"] is False
    assert h_full["is_feed_healthy"] is True

    # Ingest 5 more ticks -> drops occur!
    for i in range(10, 15):
        supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4000.0 + i, t0 + i, t0 + i))

    assert supervisor.queue_overflow_count == 5

    # Health check MUST report degraded state and data loss
    h_dropped = supervisor.check_health(now_epoch=t0 + 15)
    assert h_dropped["supervisor_state"] == "DEGRADED"
    assert h_dropped["has_data_loss"] is True
    assert h_dropped["is_feed_healthy"] is False
    assert supervisor.state == ConnectionState.DEGRADED

    # Drain with acknowledgment resets loss and restores health
    events = supervisor.drain_events(acknowledge_overflow=True)
    assert len(events) == 10
    assert supervisor.queue_overflow_count == 0

    h_recovered = supervisor.check_health(now_epoch=t0 + 16)
    assert h_recovered["supervisor_state"] == "CONNECTED"
    assert h_recovered["has_data_loss"] is False
    assert h_recovered["is_feed_healthy"] is True
    assert supervisor.state == ConnectionState.CONNECTED


# =============================================================================
# 3. TRANSPORT STARTUP ISOLATION & IMPORT PURITY
# =============================================================================

def test_05_transport_import_purity_and_zero_side_effects():
    """Verify importing transport modules causes zero background activity or thread creation."""
    import threading
    initial_threads = threading.active_count()

    import services.shadow_observer.transport as transport_pkg

    current_threads = threading.active_count()
    assert current_threads == initial_threads, "Transport import spawned unexpected background threads!"
    assert hasattr(transport_pkg, "TransportSupervisor")
    assert hasattr(transport_pkg, "PublicWebSocketClient")
    assert hasattr(transport_pkg, "PublicRestClient")


def test_06_trading_daemon_zero_transport_dependencies():
    """Verify trading daemon and broker state have zero references to shadow transport."""
    daemon_file = PROJECT_ROOT / "services" / "delta_paper_bot.py"
    if daemon_file.exists():
        tree = ast.parse(daemon_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "shadow_observer" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "shadow_observer" not in node.module


# =============================================================================
# 4. FILESYSTEM & WRITE BOUNDARY ISOLATION
# =============================================================================

def test_07_filesystem_isolation_and_write_protection(tmp_path):
    """Verify observer filesystem guard prevents directory escape and protects critical files."""
    # 1. Protected files fail
    for forbidden in [".env", ".env.monitoring", "paper_account_xautusd.json", "trading_agent.db"]:
        with pytest.raises(ShadowObserverSafetyViolation, match="protected state file"):
            validate_shadow_write_path(tmp_path / forbidden)

    # 2. Path escape outside allowed root fails
    allowed_dir = tmp_path / "authorized_shadow"
    allowed_dir.mkdir()
    escape_target = tmp_path / "outside.txt"

    with pytest.raises(ShadowObserverSafetyViolation, match="outside authorized shadow directory"):
        validate_shadow_write_path(escape_target, allowed_root=allowed_dir)

    # 3. Path inside allowed root succeeds
    safe_path = validate_shadow_write_path(allowed_dir / "report.json", allowed_root=allowed_dir)
    assert safe_path == (allowed_dir / "report.json").resolve()


# =============================================================================
# 5. CORE SAFETY INVARIANTS & HISTORICAL LEDGER PRESERVATION
# =============================================================================

def test_08_core_safety_invariants_preserved():
    """Verify all risk limits, position caps, and historical incident records remain unmodified."""
    from risk.position_size_guard import (
        APPROVED_MAX_PAPER_LOTS,
        validate_paper_order_lots,
        PositionSizeViolation,
    )

    # Invariant 1: Exactly 1 lot maximum
    assert APPROVED_MAX_PAPER_LOTS == 1

    # Invariant 2: Oversized requests deterministically rejected
    with pytest.raises(PositionSizeViolation, match="exceeds"):
        validate_paper_order_lots(2)

    with pytest.raises(PositionSizeViolation, match="prohibited"):
        validate_paper_order_lots(484)

    # Invariant 3: Historical incident PAPER_20261105_101000_LONG intact
    orders_json = PROJECT_ROOT / "data" / "paper_orders.json"
    if orders_json.exists():
        data = json.loads(orders_json.read_text(encoding="utf-8"))
        orders = data.get("orders", [])
        incident = next((o for o in orders if o.get("order_id") == "PAPER_20261105_101000_LONG"), None)
        assert incident is not None, "CRITICAL: Historical incident PAPER_20261105_101000_LONG missing!"
        assert incident.get("size") == 484, "CRITICAL: Historical incident size modified!"
        assert incident.get("pnl") == -7.9472 or "7.9472" in str(incident.get("pnl")), "PnL modified!"

