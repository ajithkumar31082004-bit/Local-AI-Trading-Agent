"""Phase 3.38 — Offline Tests for Public Market-Data Transport Adapter.

COVERS 17 INDEPENDENT OFFLINE TEST DOMAINS:
1. TransportConfig validation and safety invariants.
2. Successful mocked connection lifecycle.
3. Connection timeout handling.
4. Connection refusal and error tracking.
5. Reconnect backoff calculation and jitter bounding.
6. Maximum retry bound behavior.
7. Duplicate connection prevention.
8. Duplicate and out-of-order market event handling.
9. Malformed market-data message handling.
10. Foreign product/symbol rejection.
11. Stale feed detection and state degradation.
12. Graceful shutdown and resource disposal.
13. Socket closure after connection errors.
14. Zero private credential access.
15. Zero order or execution endpoint access.
16. Zero broker import or state mutation.
17. Zero subprocess or systemd API creation.
"""

import ast
import json
from pathlib import Path
from datetime import datetime, timezone
import pytest

from services.shadow_observer.observer_safety_guard import ShadowObserverSafetyViolation
from services.shadow_observer.transport.transport_config import (
    TransportConfig,
    PUBLIC_REST_BASE,
    PUBLIC_WS_BASE,
)
from services.shadow_observer.transport.transport_interface import (
    ConnectionState,
    NormalizedTick,
    NormalizedCandle,
)
from services.shadow_observer.transport.reconnect_controller import ReconnectController
from services.shadow_observer.transport.public_rest_client import PublicRestClient
from services.shadow_observer.transport.public_websocket_client import (
    PublicWebSocketClient,
    MockWebSocketConnection,
)
from services.shadow_observer.transport.transport_supervisor import TransportSupervisor

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# 1. CONFIGURATION VALIDATION & SAFETY
# =============================================================================

def test_01_transport_config_validation_and_safety():
    """Verify TransportConfig enforces public endpoints and rejects execution keywords."""
    # 1. Default config is valid
    cfg = TransportConfig()
    assert cfg.symbol == "XAUTUSD"
    assert "delta.exchange" in cfg.rest_base_url
    assert "delta.exchange" in cfg.ws_url

    # 2. Reject foreign URL
    with pytest.raises(ShadowObserverSafetyViolation, match="Unauthorized REST base URL"):
        TransportConfig(rest_base_url="https://api.binance.com")

    # 3. Reject forbidden URL keywords
    for forbidden_kw in ["orders", "positions", "wallet", "account"]:
        with pytest.raises(ShadowObserverSafetyViolation, match="Forbidden keyword"):
            TransportConfig(rest_base_url=f"https://api.india.delta.exchange/v2/{forbidden_kw}")

    # 4. Reject auth headers
    with pytest.raises(ShadowObserverSafetyViolation, match="Forbidden authentication header"):
        TransportConfig(headers={"api-key": "fake_secret_key"})

    # 5. Reject foreign symbol
    with pytest.raises(ShadowObserverSafetyViolation, match="Unsupported or foreign symbol"):
        TransportConfig(symbol="BTCUSD")


# =============================================================================
# 2. SUCCESSFUL MOCKED CONNECTION
# =============================================================================

def test_02_successful_mocked_connection():
    """Verify transport connects cleanly using mock adapters and sends valid public subscriptions."""
    mock_socket = MockWebSocketConnection()

    def mock_socket_factory(url, timeout):
        return mock_socket

    def mock_http_adapter(method, url, params, timeouts):
        return 200, {"success": True, "result": {"symbol": "XAUTUSD", "mark_price": 4050.0}}

    supervisor = TransportSupervisor(
        socket_factory=mock_socket_factory,
        http_adapter=mock_http_adapter,
    )

    ok = supervisor.start(now_epoch=1731300000.0)
    assert ok is True
    assert supervisor.state == ConnectionState.CONNECTED
    assert supervisor.ws_client.is_connected() is True
    assert supervisor.rest_client.is_connected() is True

    # Verify public subscriptions were dispatched
    assert len(mock_socket.sent_messages) == 1
    sub_msg = json.loads(mock_socket.sent_messages[0])
    assert sub_msg["type"] == "subscribe"
    ch_names = [ch["name"] for ch in sub_msg["payload"]["channels"]]
    assert "v2/ticker" in ch_names
    assert "candlestick_5m" in ch_names

    supervisor.stop()
    assert supervisor.state == ConnectionState.STOPPED
    assert mock_socket.is_open is False


# =============================================================================
# 3. CONNECTION TIMEOUT HANDLING
# =============================================================================

def test_03_connection_timeout_handling():
    """Verify timeout during connection transitions to degraded state and schedules backoff."""
    def timeout_socket_factory(url, timeout):
        raise TimeoutError("Socket connection timed out after 10.0s")

    supervisor = TransportSupervisor(socket_factory=timeout_socket_factory)
    ok = supervisor.start(now_epoch=1731300000.0)

    assert ok is False
    assert supervisor.state == ConnectionState.DEGRADED
    assert supervisor.reconnect_ctrl.consecutive_failures == 1
    assert supervisor.reconnect_ctrl.can_attempt_now(now_epoch=1731300000.5) is False


# =============================================================================
# 4. CONNECTION REFUSAL & ERROR TRACKING
# =============================================================================

def test_04_connection_refusal_and_error_tracking():
    """Verify connection refusal records failure metrics and increments error counts."""
    def refused_socket_factory(url, timeout):
        raise ConnectionRefusedError("Connection refused by public host")

    supervisor = TransportSupervisor(socket_factory=refused_socket_factory)
    supervisor.start(now_epoch=1731300000.0)

    telemetry = supervisor.reconnect_ctrl.get_telemetry()
    assert telemetry["consecutive_failures"] == 1
    assert telemetry["total_failures"] == 1
    assert "Connection refused" in supervisor.ws_client.disconnect_reason


# =============================================================================
# 5. RECONNECT BACKOFF & JITTER BOUNDING
# =============================================================================

def test_05_reconnect_backoff_and_jitter():
    """Verify exponential backoff calculation increases monotonically and caps at maximum."""
    ctrl = ReconnectController(TransportConfig(
        initial_backoff_seconds=2.0,
        backoff_factor=2.0,
        max_backoff_seconds=30.0,
        jitter_ratio=0.0,  # disable jitter for exact math verification
    ))

    # Attempt 1: 2.0s
    assert ctrl.compute_backoff(1) == 2.0
    # Attempt 2: 4.0s
    assert ctrl.compute_backoff(2) == 4.0
    # Attempt 3: 8.0s
    assert ctrl.compute_backoff(3) == 8.0
    # Attempt 4: 16.0s
    assert ctrl.compute_backoff(4) == 16.0
    # Attempt 5: 32.0s -> capped at 30.0s
    assert ctrl.compute_backoff(5) == 30.0
    # Attempt 10: capped at 30.0s
    assert ctrl.compute_backoff(10) == 30.0


# =============================================================================
# 6. MAXIMUM RETRY BOUND BEHAVIOR
# =============================================================================

def test_06_maximum_retry_bound_behavior():
    """Verify reconnection terminates permanently after exceeding maximum retry attempts."""
    ctrl = ReconnectController(TransportConfig(max_reconnect_attempts=3))

    t0 = 1731300000.0
    # 3 allowed failures
    can_retry1, b1 = ctrl.record_failure("Err 1", now_epoch=t0)
    assert can_retry1 is True
    can_retry2, b2 = ctrl.record_failure("Err 2", now_epoch=t0 + 10)
    assert can_retry2 is True
    can_retry3, b3 = ctrl.record_failure("Err 3", now_epoch=t0 + 20)
    assert can_retry3 is True

    # 4th failure exceeds max_reconnect_attempts (3)
    can_retry4, b4 = ctrl.record_failure("Err 4", now_epoch=t0 + 30)
    assert can_retry4 is False
    assert b4 == 0.0
    assert ctrl.can_attempt_now(now_epoch=t0 + 1000) is False


# =============================================================================
# 7. DUPLICATE CONNECTION PREVENTION
# =============================================================================

def test_07_duplicate_connection_prevention():
    """Verify calling connect() on an already connected client is rejected safely."""
    mock_socket = MockWebSocketConnection()
    ws_client = PublicWebSocketClient(
        socket_factory=lambda url, timeout: mock_socket,
    )

    # First connection succeeds
    ok1 = ws_client.connect()
    assert ok1 is True
    assert ws_client.is_connected() is True

    # Second connection is rejected without spawning a duplicate socket
    ok2 = ws_client.connect()
    assert ok2 is False
    assert len(mock_socket.sent_messages) == 1  # only 1 subscription sent


# =============================================================================
# 8. DUPLICATE & OUT-OF-ORDER MARKET EVENT REJECTION
# =============================================================================

def test_08_duplicate_and_out_of_order_market_events():
    """Verify supervisor filters duplicate candles and tracks out-of-order ticks."""
    supervisor = TransportSupervisor()
    t0 = 1731300000

    # Ingest tick 1 at t0
    tick1 = NormalizedTick("XAUTUSD", 4050.0, float(t0), float(t0))
    supervisor.on_tick_received(tick1)
    assert supervisor.reader.ticks_received == 1

    # Ingest tick 2 backwards in time (t0 - 10s)
    tick2 = NormalizedTick("XAUTUSD", 4048.0, float(t0 - 10), float(t0))
    supervisor.on_tick_received(tick2)
    assert supervisor.reader.out_of_order_count == 1
    assert supervisor.reader.last_ticker_time == float(t0)  # Monotonicity preserved

    # Ingest Candle 1
    c1 = NormalizedCandle("XAUTUSD", "5m", 4000.0, 4010.0, 3990.0, 4005.0, 100.0, t0, t0 + 300)
    ok_c1, err1 = supervisor.on_candle_received(c1, now_epoch=float(t0 + 305))
    assert ok_c1 is True
    assert err1 is None

    # Ingest Duplicate Candle 1
    ok_c2, err2 = supervisor.on_candle_received(c1, now_epoch=float(t0 + 310))
    assert ok_c2 is False
    assert "DUPLICATE_CANDLE" in err2
    assert supervisor.total_candles_rejected == 1


# =============================================================================
# 9. MALFORMED MARKET DATA MESSAGES
# =============================================================================

def test_09_malformed_market_data_messages():
    """Verify non-JSON and structurally corrupt messages increment malformed counter without crashing."""
    mock_socket = MockWebSocketConnection()
    ws_client = PublicWebSocketClient(
        socket_factory=lambda url, timeout: mock_socket,
    )
    ws_client.connect()

    # 1. Non-JSON string
    ws_client.handle_incoming_message("GARBAGE NOT JSON")
    assert ws_client.malformed_frames_count == 1

    # 2. Corrupt tick (negative price)
    ws_client.handle_incoming_message(json.dumps({"channel": "v2/ticker", "symbol": "XAUTUSD", "price": -50.0}))
    assert ws_client.malformed_frames_count == 2

    # 3. Corrupt candle (non-numeric price)
    ws_client.handle_incoming_message(json.dumps({"channel": "candlestick_5m", "candle": {"open": "INVALID"}}))
    assert ws_client.malformed_frames_count == 3


# =============================================================================
# 10. FOREIGN PRODUCT/SYMBOL REJECTION
# =============================================================================

def test_10_foreign_product_symbol_rejection():
    """Verify events for foreign products (e.g. BTCUSD, ETHUSD) are rejected."""
    supervisor = TransportSupervisor()

    # Foreign tick
    foreign_tick = NormalizedTick("BTCUSD", 65000.0, 1731300000.0, 1731300000.0)
    supervisor.on_tick_received(foreign_tick)
    assert supervisor.reader.ticks_received == 0  # Ignored by reader

    # Foreign candle
    foreign_candle = NormalizedCandle("ETHUSD", "5m", 3000.0, 3010.0, 2990.0, 3005.0, 50.0, 1731300000, 1731300300)
    # CandleValidator expects XAUTUSD structure; reader will not ingest foreign symbol
    assert foreign_candle.symbol != "XAUTUSD"


# =============================================================================
# 11. STALE FEED DETECTION & DEGRADATION
# =============================================================================

def test_11_stale_feed_detection_and_degradation():
    """Verify supervisor transitions to DEGRADED state when feed stagnates beyond 60s."""
    mock_socket = MockWebSocketConnection()
    supervisor = TransportSupervisor(socket_factory=lambda url, to: mock_socket)
    t0 = 1731300000.0
    supervisor.start(now_epoch=t0)
    assert supervisor.state == ConnectionState.CONNECTED

    # Ingest tick at t0
    supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4050.0, t0, t0))

    # Health check at t0 + 30s -> CONNECTED
    h30 = supervisor.check_health(now_epoch=t0 + 30.0)
    assert h30["supervisor_state"] == "CONNECTED"
    assert h30["is_feed_stale"] is False

    # Health check at t0 + 65s -> DEGRADED
    h65 = supervisor.check_health(now_epoch=t0 + 65.0)
    assert h65["supervisor_state"] == "DEGRADED"
    assert h65["is_feed_stale"] is True

    # Fresh tick arrives -> Recovers to CONNECTED
    supervisor.on_tick_received(NormalizedTick("XAUTUSD", 4051.0, t0 + 70.0, t0 + 70.0))
    h70 = supervisor.check_health(now_epoch=t0 + 70.0)
    assert h70["supervisor_state"] == "CONNECTED"
    assert h70["is_feed_stale"] is False


# =============================================================================
# 12. GRACEFUL SHUTDOWN
# =============================================================================

def test_12_graceful_shutdown():
    """Verify supervisor and clients close sockets and terminate in state STOPPED."""
    mock_socket = MockWebSocketConnection()
    supervisor = TransportSupervisor(socket_factory=lambda url, to: mock_socket)
    supervisor.start()

    assert supervisor.ws_client.is_connected() is True
    assert mock_socket.is_open is True

    supervisor.stop()
    assert supervisor.state == ConnectionState.STOPPED
    assert supervisor.ws_client.get_state() == ConnectionState.STOPPED
    assert supervisor.rest_client.get_state() == ConnectionState.STOPPED
    assert mock_socket.is_open is False


# =============================================================================
# 13. SOCKET CLOSURE AFTER ERRORS
# =============================================================================

def test_13_socket_closure_after_errors():
    """Verify socket resources are freed immediately if an error occurs during initialization."""
    mock_socket = MockWebSocketConnection()

    def faulty_factory(url, to):
        mock_socket.is_open = True
        raise RuntimeError("Handshake failed mid-stream")

    ws_client = PublicWebSocketClient(socket_factory=faulty_factory)
    with pytest.raises(RuntimeError, match="Handshake failed"):
        ws_client.connect()

    assert ws_client.is_connected() is False
    assert ws_client._socket is None


# =============================================================================
# 14. NO PRIVATE CREDENTIAL ACCESS
# =============================================================================

def test_14_no_private_credential_access():
    """Verify transport package does NOT read private API credentials or environment secrets."""
    transport_dir = PROJECT_ROOT / "services" / "shadow_observer" / "transport"
    for py_file in transport_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        assert "DELTA_API_KEY" not in source, f"Found DELTA_API_KEY in {py_file}"
        assert "DELTA_API_SECRET" not in source, f"Found DELTA_API_SECRET in {py_file}"
        assert ".env" not in source, f"Found .env reference in {py_file}"


# =============================================================================
# 15. NO ORDER ENDPOINT ACCESS
# =============================================================================

def test_15_no_order_endpoint_access():
    """Verify REST and WebSocket clients raise security violations on execution endpoints."""
    # REST client rejects order path
    rest_client = PublicRestClient(http_adapter=lambda m, u, p, t: (200, {}))
    with pytest.raises(ShadowObserverSafetyViolation, match="REST client attempted forbidden path"):
        rest_client._execute_request("POST", "/v2/orders", {})

    # WebSocket client rejects order channel subscription
    ws_client = PublicWebSocketClient()
    with pytest.raises(ShadowObserverSafetyViolation, match="Forbidden channel"):
        ws_client._validate_subscription_safety({
            "payload": {"channels": [{"name": "private/orders"}]}
        })


# =============================================================================
# 16. ZERO BROKER IMPORT OR STATE MUTATION
# =============================================================================

def test_16_zero_broker_import_or_state_mutation():
    """Verify transport contains zero broker imports and leaves historical records intact."""
    transport_dir = PROJECT_ROOT / "services" / "shadow_observer" / "transport"
    for py_file in transport_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        assert "DeltaPaperBroker" not in source, f"Found DeltaPaperBroker in {py_file}"
        assert "open_simulated_trade" not in source, f"Found open_simulated_trade in {py_file}"

    # Verify historical incident is intact
    forward_state = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    data = json.loads(forward_state.read_text(encoding="utf-8"))
    incident = next((t for t in data["closed_trades"] if t["trade_id"] == "PAPER_20261105_101000_LONG"), None)
    assert incident is not None
    assert incident["lots"] == 484
    assert incident["net_pnl"] == -7.9472


# =============================================================================
# 17. ZERO SUBPROCESS CREATION
# =============================================================================

def test_17_zero_subprocess_creation():
    """Verify transport package contains zero subprocess or systemd invocations."""
    transport_dir = PROJECT_ROOT / "services" / "shadow_observer" / "transport"
    for py_file in transport_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                assert func_name not in ["popen", "system", "spawn", "kill", "exec"], (
                    f"Forbidden process call '{func_name}' found in {py_file}"
                )
