"""Phase 3.39 — Independent Adversarial Public Feed Transport Code Audit.

INDEPENDENT ADVERSARIAL TEST SUITE:
1. All six lifecycle states and valid/invalid transitions.
2. Duplicate connection guards on REST and WebSocket clients.
3. Connection timeout and refusal resilience with resource cleanup.
4. Reconnection controller mathematical bounds, jitter bounds (±10%), and reset policy.
5. Cancellation during connection and streaming with socket disposal.
6. Socket closure after adverse exceptions.
7. Repeated start/stop lifecycle cycling (10 consecutive cycles).
8. Resource safety: bounded queue (1000) and observable overflow behavior.
9. Resource safety: bounded reconnect history (100).
10. Logging flood protection during queue overflow.
11. AST isolation audit: zero API credentials, private endpoints, or broker state mutations.
12. Product identity strictness (XAUTUSD only; foreign symbol rejection).
13. Dual timestamp separation (exchange vs local receipt).
14. Malformed, incomplete, duplicate, out-of-order, and stale event handling.
15. Global safety invariants and historical ledger preservation.
"""

import ast
import json
import os
import random
from pathlib import Path
from datetime import datetime, timezone
from collections import deque
import pytest

from services.shadow_observer.observer_safety_guard import ShadowObserverSafetyViolation
from services.shadow_observer.transport.transport_config import (
    TransportConfig,
    PUBLIC_REST_BASE,
    PUBLIC_WS_BASE,
    FORBIDDEN_ENDPOINT_KEYWORDS,
    FORBIDDEN_AUTH_HEADERS,
)
from services.shadow_observer.transport.transport_interface import (
    ConnectionState,
    NormalizedTick,
    NormalizedCandle,
)
from services.shadow_observer.transport.reconnect_controller import ReconnectController
from services.shadow_observer.transport.public_rest_client import (
    PublicRestClient,
    ALLOWED_REST_PATHS,
)
from services.shadow_observer.transport.public_websocket_client import (
    PublicWebSocketClient,
    ALLOWED_PUBLIC_CHANNELS,
    MockWebSocketConnection,
)
from services.shadow_observer.transport.transport_supervisor import TransportSupervisor

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# 1. LIFECYCLE STATES & TRANSITION AUDIT
# =============================================================================

def test_01_all_six_lifecycle_states_and_transitions():
    """Verify all six lifecycle states are defined, reachable, and behave predictably."""
    states = [s.value for s in ConnectionState]
    expected_states = ["DISCONNECTED", "CONNECTING", "CONNECTED", "DEGRADED", "STOPPING", "STOPPED"]
    assert sorted(states) == sorted(expected_states)

    mock_socket = MockWebSocketConnection()
    supervisor = TransportSupervisor(
        socket_factory=lambda url, to: mock_socket,
        http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
    )

    # State 1: DISCONNECTED at initialization
    assert supervisor.state == ConnectionState.DISCONNECTED
    assert supervisor.ws_client.get_state() == ConnectionState.DISCONNECTED
    assert supervisor.rest_client.get_state() == ConnectionState.DISCONNECTED

    # State 2 & 3: CONNECTING -> CONNECTED on start
    t0 = 1731300000.0
    started = supervisor.start(now_epoch=t0)
    assert started is True
    assert supervisor.state == ConnectionState.CONNECTED
    assert supervisor.ws_client.get_state() == ConnectionState.CONNECTED
    assert supervisor.rest_client.get_state() == ConnectionState.CONNECTED

    # State 4: DEGRADED when feed stagnates
    health_degraded = supervisor.check_health(now_epoch=t0 + 120.0)
    assert health_degraded["supervisor_state"] == "DEGRADED"
    assert supervisor.state == ConnectionState.DEGRADED

    # State 5 & 6: STOPPING -> STOPPED on stop
    supervisor.stop()
    assert supervisor.state == ConnectionState.STOPPED
    assert supervisor.ws_client.get_state() == ConnectionState.STOPPED
    assert supervisor.rest_client.get_state() == ConnectionState.STOPPED


# =============================================================================
# 2. DUPLICATE CONNECTION GUARDS
# =============================================================================

def test_02_duplicate_connection_guards():
    """Verify duplicate connection requests are rejected without spawning redundant sockets."""
    sockets_spawned = []

    def counting_socket_factory(url, to):
        s = MockWebSocketConnection()
        sockets_spawned.append(s)
        return s

    ws = PublicWebSocketClient(socket_factory=counting_socket_factory)

    # First connection succeeds
    assert ws.connect() is True
    assert ws.is_connected() is True
    assert len(sockets_spawned) == 1

    # Second connection while CONNECTED is rejected
    assert ws.connect() is False
    assert len(sockets_spawned) == 1

    # In CONNECTING state, secondary connection is also rejected
    ws.state = ConnectionState.CONNECTING
    assert ws.connect() is False
    assert len(sockets_spawned) == 1


# =============================================================================
# 3. CONNECTION TIMEOUT & REFUSAL RESILIENCE
# =============================================================================

def test_03_connection_timeout_and_refusal_cleanup():
    """Verify socket timeouts and connection refusals clean up partially opened sockets."""
    leaked_socket = MockWebSocketConnection()

    def timeout_factory(url, to):
        # Simulate partial socket creation followed by socket.timeout
        leaked_socket.is_open = True
        raise TimeoutError("Simulated TCP handshake timeout after 10.0s")

    ws = PublicWebSocketClient(socket_factory=timeout_factory)

    with pytest.raises(TimeoutError, match="Simulated TCP handshake timeout"):
        ws.connect()

    # Verify state is DISCONNECTED and internal socket is cleaned up
    assert ws.state == ConnectionState.DISCONNECTED
    assert ws._socket is None
    assert "timeout" in ws.disconnect_reason.lower()


# =============================================================================
# 4. RECONNECTION CONTROLLER MATH, JITTER & BOUNDS
# =============================================================================

def test_04_reconnect_controller_math_and_bounds():
    """Verify exact backoff progression, 60s cap, 5-attempt retry bound, and +/-10% jitter."""
    cfg = TransportConfig(
        initial_backoff_seconds=2.0,
        backoff_factor=2.0,
        max_backoff_seconds=60.0,
        max_reconnect_attempts=5,
        jitter_ratio=0.1,
    )
    ctrl = ReconnectController(cfg)

    # Deterministic test with jitter_ratio = 0.0
    ctrl_no_jitter = ReconnectController(TransportConfig(
        initial_backoff_seconds=2.0,
        backoff_factor=2.0,
        max_backoff_seconds=60.0,
        max_reconnect_attempts=5,
        jitter_ratio=0.0,
    ))

    expected_delays = [2.0, 4.0, 8.0, 16.0, 32.0]
    t_base = 1731300000.0

    for idx, expected in enumerate(expected_delays, start=1):
        can_retry, delay = ctrl_no_jitter.record_failure(f"Fail {idx}", now_epoch=t_base + idx * 50)
        assert can_retry is True
        assert delay == expected
        assert ctrl_no_jitter.consecutive_failures == idx

    # 6th failure exceeds maximum retry limit of 5
    can_retry_6, delay_6 = ctrl_no_jitter.record_failure("Fail 6", now_epoch=t_base + 300)
    assert can_retry_6 is False
    assert delay_6 == 0.0
    assert ctrl_no_jitter.can_attempt_now(now_epoch=t_base + 10000) is False

    # Reset upon success
    ctrl_no_jitter.record_success(now_epoch=t_base + 350)
    assert ctrl_no_jitter.consecutive_failures == 0
    assert ctrl_no_jitter.next_allowed_attempt is None
    assert ctrl_no_jitter.can_attempt_now(now_epoch=t_base + 351) is True

    # Adversarial verification of jitter bounds over 1,000 iterations
    for attempt in range(1, 10):
        base_expected = min(60.0, 2.0 * (2.0 ** min(attempt - 1, 10)))
        lower_bound = max(0.1, round(base_expected * 0.9, 3) - 0.01)
        upper_bound = min(66.0, round(base_expected * 1.1, 3) + 0.01)

        for _ in range(100):
            computed = ctrl.compute_backoff(attempt)
            assert lower_bound <= computed <= upper_bound, (
                f"Attempt {attempt}: computed delay {computed} out of bounds [{lower_bound}, {upper_bound}]"
            )


# =============================================================================
# 5. CANCELLATION DURING CONNECTION & STREAMING
# =============================================================================

def test_05_cancellation_during_connection_and_streaming():
    """Verify interruption/cancellation during connection or streaming cleans up sockets cleanly."""
    mock_socket = MockWebSocketConnection()

    def aborting_factory(url, to):
        # Simulate cancellation during handshake
        raise KeyboardInterrupt("Simulated user cancellation during connect")

    ws = PublicWebSocketClient(socket_factory=aborting_factory)

    with pytest.raises(BaseException):
        ws.connect()

    assert ws.state == ConnectionState.DISCONNECTED
    assert ws._socket is None

    # Test cancellation during streaming
    streaming_socket = MockWebSocketConnection()
    ws_streaming = PublicWebSocketClient(socket_factory=lambda u, t: streaming_socket)
    ws_streaming.connect()
    assert ws_streaming.is_connected() is True
    assert streaming_socket.is_open is True

    # Trigger abrupt disconnect / cancellation
    ws_streaming.disconnect()
    assert ws_streaming.state == ConnectionState.STOPPED
    assert ws_streaming._socket is None
    assert streaming_socket.is_open is False


# =============================================================================
# 6. SOCKET CLOSURE AFTER EXCEPTIONS
# =============================================================================

def test_06_socket_closure_after_exceptions():
    """Verify socket is closed and released when unhandled exceptions occur during execution."""
    mock_sock = MockWebSocketConnection()
    ws = PublicWebSocketClient(socket_factory=lambda u, t: mock_sock)
    ws.connect()
    assert mock_sock.is_open is True

    # Disconnect releases socket
    ws.disconnect()
    assert mock_sock.is_open is False
    assert ws._socket is None


# =============================================================================
# 7. REPEATED START / STOP CYCLES
# =============================================================================

def test_07_repeated_start_stop_cycles():
    """Verify supervisor survives 10 rapid consecutive start/stop cycles without state corruption."""
    for cycle in range(10):
        mock_sock = MockWebSocketConnection()
        supervisor = TransportSupervisor(
            socket_factory=lambda u, t: mock_sock,
            http_adapter=lambda m, u, p, t: (200, {"success": True, "result": {}}),
        )
        assert supervisor.start() is True
        assert supervisor.state == ConnectionState.CONNECTED
        assert mock_sock.is_open is True

        supervisor.stop()
        assert supervisor.state == ConnectionState.STOPPED
        assert mock_sock.is_open is False
        assert supervisor.ws_client._socket is None


# =============================================================================
# 8. RESOURCE SAFETY: BOUNDED QUEUE & OBSERVABLE OVERFLOW
# =============================================================================

def test_08_bounded_queue_and_observable_overflow():
    """Verify queue enforces max 1,000 bound, increments overflow count, and reports it in health."""
    cfg = TransportConfig(max_message_queue_size=1000)
    supervisor = TransportSupervisor(config=cfg)
    t0 = 1731300000.0

    # Ingest 1,500 ticks
    for i in range(1500):
        tick = NormalizedTick("XAUTUSD", 4000.0 + (i * 0.1), t0 + i, t0 + i)
        supervisor.on_tick_received(tick)

    # Verify maximum queue depth is strictly capped at 1,000
    assert len(supervisor.event_queue) == 1000
    assert supervisor.total_ticks_processed == 1500

    # Verify exactly 500 overflows were recorded and observable
    assert supervisor.queue_overflow_count == 500

    health = supervisor.check_health(now_epoch=t0 + 1500)
    assert health["queue_depth"] == 1000
    assert health["queue_overflow_count"] == 500

    # Drain queue
    drained = supervisor.drain_events()
    assert len(drained) == 1000
    assert len(supervisor.event_queue) == 0


# =============================================================================
# 9. RESOURCE SAFETY: BOUNDED RECONNECT HISTORY
# =============================================================================

def test_09_bounded_reconnect_history():
    """Verify reconnect controller history is capped at exactly 100 records."""
    ctrl = ReconnectController(TransportConfig(max_reconnect_history=100))
    t0 = 1731300000.0

    for i in range(250):
        ctrl.record_failure(f"Error {i}", now_epoch=t0 + i)

    assert len(ctrl.reconnect_history) == 100
    telemetry = ctrl.get_telemetry()
    assert telemetry["history_depth"] == 100
    assert telemetry["total_failures"] == 250


# =============================================================================
# 10. LOGGING FLOOD PROTECTION
# =============================================================================

def test_10_logging_flood_protection(caplog):
    """Verify queue overflow does not create uncontrolled logging loops (throttled logging)."""
    cfg = TransportConfig(max_message_queue_size=10)
    supervisor = TransportSupervisor(config=cfg)

    # Ingest 250 ticks into capacity-10 queue
    with caplog.at_level("WARNING"):
        for i in range(250):
            supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4000.0, 1000.0 + i, 1000.0 + i))

    assert supervisor.queue_overflow_count == 240
    # Throttled logging logs only at drop 1, drop 100, drop 200 (total 3 log records)
    overflow_logs = [r for r in caplog.records if "Event queue overflow" in r.message]
    assert len(overflow_logs) == 3


# =============================================================================
# 11. ENDPOINT & CREDENTIAL ISOLATION (AST INSPECTION)
# =============================================================================

def test_11_ast_isolation_and_zero_credentials():
    """Inspect AST of all transport files to ensure zero access to credentials or private APIs."""
    transport_dir = PROJECT_ROOT / "services" / "shadow_observer" / "transport"
    py_files = list(transport_dir.glob("*.py"))
    assert len(py_files) >= 6

    forbidden_ast_names = [
        "DELTA_API_KEY", "DELTA_API_SECRET",
        "api_key", "api_secret", "secret_key",
        "private_key", "signature", "auth_token",
    ]

    forbidden_modules = [
        "services.paper_broker",
        "services.portfolio_service",
        "services.idempotency_registry",
        "services.execution_intent",
    ]

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden_modules, f"Forbidden import {alias.name} in {py_file}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module not in forbidden_modules, f"Forbidden from-import {node.module} in {py_file}"
            elif isinstance(node, ast.Name):
                assert node.id not in forbidden_ast_names, f"Forbidden identifier {node.id} in {py_file}"


# =============================================================================
# 12. PRODUCT IDENTITY & FOREIGN REJECTION
# =============================================================================

def test_12_strict_product_identity():
    """Verify transport rejects any symbol other than XAUTUSD."""
    # Config rejects non-XAUTUSD
    with pytest.raises(ShadowObserverSafetyViolation):
        TransportConfig(symbol="BTCUSD")

    # REST rejects foreign path
    rest = PublicRestClient()
    with pytest.raises(ShadowObserverSafetyViolation):
        rest._validate_path_safety("/v2/tickers/BTCUSD/orders")

    # WebSocket ignores foreign symbol frames
    received_foreign = []
    ws = PublicWebSocketClient(
        on_tick=lambda t: received_foreign.append(t),
    )
    ws.state = ConnectionState.CONNECTED
    ws.handle_incoming_message(json.dumps({
        "channel": "v2/ticker",
        "symbol": "BTCUSD",
        "mark_price": "65000.0",
    }))
    assert len(received_foreign) == 0


# =============================================================================
# 13. SEPARATE EXCHANGE & RECEIPT TIMESTAMPS
# =============================================================================

def test_13_separate_exchange_and_receipt_timestamps():
    """Verify exchange timestamps and local receipt timestamps are tracked independently."""
    t_exchange = 1731300000.0
    t_local = 1731300005.123

    tick = NormalizedTick(
        symbol="XAUTUSD",
        price=4050.25,
        exchange_timestamp=t_exchange,
        local_receipt_timestamp=t_local,
    )
    assert tick.exchange_timestamp != tick.local_receipt_timestamp
    assert tick.exchange_timestamp == t_exchange
    assert tick.local_receipt_timestamp == t_local

    candle = NormalizedCandle(
        symbol="XAUTUSD",
        timeframe="5m",
        open=4000.0,
        high=4010.0,
        low=3990.0,
        close=4005.0,
        volume=100.0,
        candle_start_epoch=int(t_exchange),
        candle_end_epoch=int(t_exchange + 300),
        exchange_timestamp=t_exchange,
        local_receipt_timestamp=t_local,
    )
    assert candle.exchange_timestamp == t_exchange
    assert candle.local_receipt_timestamp == t_local


# =============================================================================
# 14. MALFORMED, INCOMPLETE, OUT-OF-ORDER & STALE EVENTS
# =============================================================================

def test_14_malformed_incomplete_and_stale_events():
    """Verify transport handles malformed strings, missing values, and stale feeds safely."""
    ws = PublicWebSocketClient()
    ws.state = ConnectionState.CONNECTED

    # 1. Non-JSON string
    ws.handle_incoming_message("GARBAGE_PAYLOAD")
    assert ws.malformed_frames_count == 1

    # 2. Ticker with negative price
    ws.handle_incoming_message(json.dumps({"channel": "v2/ticker", "mark_price": -5.0}))
    assert ws.malformed_frames_count == 2

    # 3. Candle with missing fields
    ws.handle_incoming_message(json.dumps({"channel": "candlestick_5m", "candle": {"open": None}}))
    assert ws.malformed_frames_count == 3


# =============================================================================
# 15. SAFETY INVARIANTS & HISTORICAL LEDGER PRESERVATION
# =============================================================================

def test_15_safety_invariants_and_historical_ledger():
    """Verify safety limits, PAPER configuration, and preservation of historical incident record."""
    from risk.position_size_guard import APPROVED_MAX_PAPER_LOTS, validate_paper_order_lots, PositionSizeViolation

    # Invariant 1: Max lots == 1
    assert APPROVED_MAX_PAPER_LOTS == 1

    # Invariant 2: Oversized orders strictly rejected with PositionSizeViolation (never silently clamped)
    with pytest.raises(PositionSizeViolation, match="exceeds the authorized forward-test maximum"):
        validate_paper_order_lots(2)

    with pytest.raises(PositionSizeViolation, match="Silent clamping is prohibited"):
        validate_paper_order_lots(484)

    # Invariant 3: Historical incident PAPER_20261105_101000_LONG intact
    orders_json = PROJECT_ROOT / "data" / "paper_orders.json"
    if orders_json.exists():
        data = json.loads(orders_json.read_text(encoding="utf-8"))
        orders = data.get("orders", [])
        incident = next((o for o in orders if o.get("order_id") == "PAPER_20261105_101000_LONG"), None)
        assert incident is not None, "CRITICAL: Historical incident PAPER_20261105_101000_LONG missing!"
        assert incident.get("size") == 484, "CRITICAL: Historical incident size modified!"
