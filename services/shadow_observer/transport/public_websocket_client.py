"""Public WebSocket Client for Delta Exchange India Market Data.

OPERATING CONSTRAINTS:
1. Connects only to public unauthenticated WebSocket endpoint.
2. Subscribes only to public market data feeds ('v2/ticker', 'candlestick_5m').
3. Strictly rejects any attempt to subscribe to private channels or submit orders.
4. Provides full lifecycle management and injectable socket transport for offline testing.
"""

from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
import json
import math
import logging

from services.shadow_observer.observer_safety_guard import ShadowObserverSafetyViolation
from services.shadow_observer.transport.transport_config import TransportConfig, FORBIDDEN_ENDPOINT_KEYWORDS
from services.shadow_observer.transport.transport_interface import (
    ITransportClient,
    ConnectionState,
    NormalizedTick,
    NormalizedCandle,
)

logger = logging.getLogger("shadow_observer.transport.websocket")

# Whitelist of permissible public WebSocket channel names
ALLOWED_PUBLIC_CHANNELS = [
    "v2/ticker",
    "candlestick_5m",
    "candlestick_15m",
]


class MockWebSocketConnection:
    """Mock socket connection for offline testing."""
    def __init__(self):
        self.is_open = True
        self.sent_messages: List[str] = []

    def send(self, msg: str) -> None:
        if not self.is_open:
            raise RuntimeError("Cannot send on closed WebSocket connection.")
        self.sent_messages.append(msg)

    def close(self) -> None:
        self.is_open = False


class PublicWebSocketClient(ITransportClient):
    """Pure public unauthenticated WebSocket client for Delta Exchange India."""

    def __init__(
        self,
        config: Optional[TransportConfig] = None,
        socket_factory: Optional[Callable[[str, float], Any]] = None,
        on_tick: Optional[Callable[[NormalizedTick], None]] = None,
        on_candle: Optional[Callable[[NormalizedCandle], None]] = None,
    ):
        self.config = config or TransportConfig()
        self.socket_factory = socket_factory
        self.on_tick = on_tick
        self.on_candle = on_candle

        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self._socket: Optional[Any] = None
        self.ticks_received: int = 0
        self.candles_received: int = 0
        self.malformed_frames_count: int = 0
        self.last_heartbeat_time: Optional[float] = None
        self.disconnect_reason: Optional[str] = None

    def connect(self) -> bool:
        """Establish WebSocket connection with duplicate connection prevention."""
        # 1. Prevent duplicate connections
        if self.state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            logger.warning("Connection attempt ignored: Already in %s state.", self.state.value)
            return False

        self.config.validate_config()
        self.state = ConnectionState.CONNECTING
        self.disconnect_reason = None

        logger.info("Connecting to public WebSocket: %s", self.config.ws_url)

        try:
            if self.socket_factory:
                self._socket = self.socket_factory(self.config.ws_url, self.config.connect_timeout_seconds)
            else:
                raise RuntimeError("OFFLINE SAFETY: No socket factory injected. Live WebSocket disabled.")

            self.state = ConnectionState.CONNECTED
            self.last_heartbeat_time = datetime.now(timezone.utc).timestamp()
            self._send_subscription()
            return True

        except BaseException as exc:
            self.state = ConnectionState.DISCONNECTED
            self.disconnect_reason = f"Connection failed: {exc}"
            if isinstance(exc, Exception):
                logger.error("Failed to connect WebSocket: %s", exc)
            else:
                logger.info("WebSocket connection cancelled by process signal: %s", type(exc).__name__)
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None
            raise

    def disconnect(self) -> None:
        """Gracefully close socket and set state to STOPPED."""
        if self.state == ConnectionState.STOPPED:
            return

        self.state = ConnectionState.STOPPING
        logger.info("Closing public WebSocket client...")

        if self._socket:
            try:
                self._socket.close()
            except Exception as e:
                logger.warning("Error during socket close: %s", e)
            finally:
                self._socket = None

        self.state = ConnectionState.STOPPED
        logger.info("Public WebSocket client stopped.")

    def get_state(self) -> ConnectionState:
        return self.state

    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    def _send_subscription(self) -> None:
        """Send public channel subscription message."""
        if not self._socket:
            return

        sub_payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {"name": "v2/ticker", "symbols": [self.config.symbol]},
                    {"name": "candlestick_5m", "symbols": [self.config.symbol]},
                ]
            }
        }
        self._validate_subscription_safety(sub_payload)
        self._socket.send(json.dumps(sub_payload))
        logger.info("Sent public subscription for channels: v2/ticker, candlestick_5m on %s", self.config.symbol)

    def _validate_subscription_safety(self, payload: Dict[str, Any]) -> None:
        """Enforce that subscriptions only request permitted public market data channels."""
        channels = payload.get("payload", {}).get("channels", [])
        for ch in channels:
            name = ch.get("name", "").lower()
            for forbidden in FORBIDDEN_ENDPOINT_KEYWORDS:
                if forbidden in name:
                    msg = f"SECURITY VIOLATION: Forbidden channel '{name}' requested in subscription."
                    logger.critical(msg)
                    raise ShadowObserverSafetyViolation(msg)

            if name not in ALLOWED_PUBLIC_CHANNELS:
                msg = f"SECURITY VIOLATION: Channel '{name}' not in public allowlist: {ALLOWED_PUBLIC_CHANNELS}"
                logger.critical(msg)
                raise ShadowObserverSafetyViolation(msg)

    def handle_incoming_message(self, raw_message: str, now_epoch: Optional[float] = None) -> None:
        """Process an incoming raw JSON message frame from WebSocket feed."""
        if self.state not in (ConnectionState.CONNECTED, ConnectionState.DEGRADED):
            logger.warning("Incoming message ignored while client is in state: %s", self.state.value)
            return

        # Defensive check against oversized frames (e.g. payload DOS)
        if isinstance(raw_message, str) and len(raw_message) > 65536:
            self.malformed_frames_count += 1
            logger.warning("Rejected oversized WebSocket frame (%d bytes)", len(raw_message))
            return

        curr_time = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        self.last_heartbeat_time = curr_time

        try:
            msg = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
            if not isinstance(msg, dict):
                self.malformed_frames_count += 1
                return
        except Exception:
            self.malformed_frames_count += 1
            logger.warning("Malformed non-JSON WebSocket frame received.")
            return

        msg_type = msg.get("type", "")
        channel = msg.get("channel", "")

        # Handle heartbeat / ping
        if msg_type in ("ping", "heartbeat"):
            return

        # Handle Ticker
        if channel == "v2/ticker" or "mark_price" in msg or "price" in msg:
            self._process_ticker_message(msg, curr_time)

        # Handle Candle
        elif channel in ("candlestick_5m", "candlestick_15m") or "candle" in msg:
            self._process_candle_message(msg, curr_time)

    def _process_ticker_message(self, msg: Dict[str, Any], curr_time: float) -> None:
        """Normalize and emit a public ticker event."""
        sym = str(msg.get("symbol", self.config.symbol)).upper()
        if sym != self.config.symbol:
            logger.debug("Ignored ticker for foreign symbol: %s", sym)
            return

        try:
            price = float(msg.get("mark_price", msg.get("close", msg.get("price", 0.0))))
            if not math.isfinite(price) or price <= 0 or price > 1_000_000.0:
                self.malformed_frames_count += 1
                return
        except (ValueError, TypeError):
            self.malformed_frames_count += 1
            return

        ts = msg.get("timestamp")
        try:
            exchange_epoch = float(ts) if ts is not None else None
            if exchange_epoch is not None and (not math.isfinite(exchange_epoch) or exchange_epoch <= 0):
                exchange_epoch = None
        except (ValueError, TypeError):
            exchange_epoch = None

        tick = NormalizedTick(
            symbol=sym,
            price=price,
            exchange_timestamp=exchange_epoch,
            local_receipt_timestamp=curr_time,
            raw_payload=msg,
        )
        self.ticks_received += 1
        if self.on_tick:
            self.on_tick(tick)

    def _process_candle_message(self, msg: Dict[str, Any], curr_time: float) -> None:
        """Normalize and emit a completed candle event."""
        candle_data = msg.get("candle", msg)
        if not isinstance(candle_data, dict):
            self.malformed_frames_count += 1
            return

        sym = str(candle_data.get("symbol", self.config.symbol)).upper()
        if sym != self.config.symbol:
            return

        try:
            o = float(candle_data.get("open", candle_data.get("o", 0.0)))
            h = float(candle_data.get("high", candle_data.get("h", 0.0)))
            l = float(candle_data.get("low", candle_data.get("l", 0.0)))
            c = float(candle_data.get("close", candle_data.get("c", 0.0)))
            v = float(candle_data.get("volume", candle_data.get("v", 0.0)))
            t_start = int(candle_data.get("timestamp", candle_data.get("t", 0)))
        except (ValueError, TypeError):
            self.malformed_frames_count += 1
            return

        if not all(math.isfinite(x) and 0 < x <= 1_000_000.0 for x in (o, h, l, c)):
            self.malformed_frames_count += 1
            return
        if not math.isfinite(v) or v < 0 or t_start <= 0:
            self.malformed_frames_count += 1
            return

        # Verification of completion
        is_complete = bool(candle_data.get("is_complete", candle_data.get("completed", True)))

        candle = NormalizedCandle(
            symbol=sym,
            timeframe="5m",
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            candle_start_epoch=t_start,
            candle_end_epoch=t_start + 300,
            is_complete=is_complete,
            exchange_timestamp=float(t_start),
            local_receipt_timestamp=curr_time,
            raw_payload=msg,
        )
        self.candles_received += 1
        if self.on_candle:
            self.on_candle(candle)
