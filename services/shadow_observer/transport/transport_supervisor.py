"""Transport Supervisor for Read-Only Shadow Observer.

COORDINATES:
1. Public REST and WebSocket client lifecycles.
2. Reconnection controller pacing and exponential backoff.
3. Ingestion into PublicMarketDataReader and CandleValidator.
4. Bounded message queue management.
5. Overall health telemetry and state degradation detection.
"""

from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Callable
import logging

from services.shadow_observer.transport.transport_config import TransportConfig
from services.shadow_observer.transport.transport_interface import (
    ConnectionState,
    NormalizedTick,
    NormalizedCandle,
)
from services.shadow_observer.transport.reconnect_controller import ReconnectController
from services.shadow_observer.transport.public_rest_client import PublicRestClient
from services.shadow_observer.transport.public_websocket_client import PublicWebSocketClient
from services.shadow_observer.market_data_reader import PublicMarketDataReader
from services.shadow_observer.candle_validator import CandleValidator, CandleValidationReport

logger = logging.getLogger("shadow_observer.transport.supervisor")


class TransportSupervisor:
    """Supervises public market data transport, feed health, and ingestion into shadow validator."""

    def __init__(
        self,
        config: Optional[TransportConfig] = None,
        market_data_reader: Optional[PublicMarketDataReader] = None,
        candle_validator: Optional[CandleValidator] = None,
        http_adapter: Optional[Callable] = None,
        socket_factory: Optional[Callable] = None,
    ):
        self.config = config or TransportConfig()
        self.reader = market_data_reader or PublicMarketDataReader(
            symbol=self.config.symbol,
            stale_threshold_seconds=self.config.stale_feed_threshold_seconds,
        )
        self.validator = candle_validator or CandleValidator(expected_timeframe_minutes=5)
        self.reconnect_ctrl = ReconnectController(self.config)

        # Clients
        self.rest_client = PublicRestClient(self.config, http_adapter=http_adapter)
        self.ws_client = PublicWebSocketClient(
            config=self.config,
            socket_factory=socket_factory,
            on_tick=self.on_tick_received,
            on_candle=self.on_candle_received,
        )

        # Bounded in-memory event queue
        self.event_queue: deque = deque(maxlen=self.config.max_message_queue_size)
        self.state: ConnectionState = ConnectionState.DISCONNECTED

        # Counters
        self.total_ticks_processed: int = 0
        self.total_candles_processed: int = 0
        self.total_candles_rejected: int = 0
        self.queue_overflow_count: int = 0

    def start(self, now_epoch: Optional[float] = None) -> bool:
        """Initialize transport components and establish connections."""
        if self.state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            logger.warning("Supervisor start ignored: already %s", self.state.value)
            return False

        self.state = ConnectionState.CONNECTING

        # 1. Start REST client
        try:
            self.rest_client.connect()
        except BaseException as exc:
            self.state = ConnectionState.STOPPED
            self.stop()
            if isinstance(exc, Exception):
                logger.error("REST connection initialization failed: %s", exc)
                self.state = ConnectionState.DEGRADED
                self.reconnect_ctrl.record_failure(f"REST_INIT_FAIL: {exc}", now_epoch=now_epoch)
                return False
            raise

        # 2. Start WebSocket client
        try:
            connected = self.ws_client.connect()
            if connected:
                self.state = ConnectionState.CONNECTED
                self.reconnect_ctrl.record_success(now_epoch=now_epoch)
                logger.info("TransportSupervisor started successfully in state CONNECTED.")
                return True
            else:
                self.state = ConnectionState.DEGRADED
                return False
        except BaseException as exc:
            self.state = ConnectionState.STOPPED
            self.stop()
            if isinstance(exc, Exception):
                self.state = ConnectionState.DEGRADED
                can_retry, backoff = self.reconnect_ctrl.record_failure(f"WS_INIT_FAIL: {exc}", now_epoch=now_epoch)
                logger.error("WebSocket connection failed (can_retry=%s, backoff=%.2fs): %s", can_retry, backoff, exc)
                return False
            raise

    def stop(self) -> None:
        """Gracefully terminate transport and all active connections."""
        self.state = ConnectionState.STOPPING
        logger.info("Stopping TransportSupervisor...")

        try:
            self.ws_client.disconnect()
        except Exception as e:
            logger.warning("Error disconnecting WS client: %s", e)

        try:
            self.rest_client.disconnect()
        except Exception as e:
            logger.warning("Error disconnecting REST client: %s", e)

        self.state = ConnectionState.STOPPED
        logger.info("TransportSupervisor stopped cleanly.")

    def on_tick_received(self, tick: NormalizedTick) -> None:
        """Callback for incoming public ticker ticks."""
        self.total_ticks_processed += 1
        if len(self.event_queue) >= self.config.max_message_queue_size:
            self.queue_overflow_count += 1
            if self.queue_overflow_count == 1 or self.queue_overflow_count % 100 == 0:
                logger.warning(
                    "Event queue overflow (capacity %d). Oldest event dropped. Total drops: %d",
                    self.config.max_message_queue_size, self.queue_overflow_count
                )
        self.event_queue.append(tick)

        # Ingest into existing market data reader
        tick_dict = {
            "symbol": tick.symbol,
            "price": tick.price,
            "timestamp": tick.exchange_timestamp,
        }
        self.reader.ingest_ticker_update(tick_dict, now_epoch=tick.local_receipt_timestamp)

    def on_candle_received(self, candle: NormalizedCandle, now_epoch: Optional[float] = None) -> Tuple[bool, Optional[str]]:
        """Callback for incoming public candles; validates through CandleValidator."""
        self.total_candles_processed += 1
        candle_dict = candle.to_dict()

        curr_time = now_epoch if now_epoch is not None else candle.local_receipt_timestamp

        # 1. Enforce completed-candle barrier
        val_rep: CandleValidationReport = self.validator.validate_incoming_candle(
            candle_dict,
            now_epoch=curr_time,
        )

        if not val_rep.is_valid:
            self.total_candles_rejected += 1
            logger.warning("Candle rejected by validator: %s", val_rep.rejection_reason)
            return False, val_rep.rejection_reason

        # 2. Buffer validated completed candle
        self.reader.ingest_completed_candle(candle_dict)
        if len(self.event_queue) >= self.config.max_message_queue_size:
            self.queue_overflow_count += 1
            if self.queue_overflow_count == 1 or self.queue_overflow_count % 100 == 0:
                logger.warning(
                    "Event queue overflow (capacity %d). Oldest event dropped. Total drops: %d",
                    self.config.max_message_queue_size, self.queue_overflow_count
                )
        self.event_queue.append(candle)
        return True, None

    def check_health(self, now_epoch: Optional[float] = None) -> Dict[str, Any]:
        """Perform comprehensive health check and update degradation status."""
        curr_time = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()

        # Check ticker staleness and queue data loss
        is_stale, elapsed = self.reader.is_feed_stale(now_epoch=curr_time)
        has_data_loss = self.queue_overflow_count > 0

        if (is_stale or has_data_loss) and self.state == ConnectionState.CONNECTED:
            self.state = ConnectionState.DEGRADED
            reason = f"staleness ({elapsed:.1f}s elapsed)" if is_stale else f"queue overflow ({self.queue_overflow_count} drops)"
            logger.warning("Feed degraded: %s", reason)
        elif not is_stale and not has_data_loss and self.state == ConnectionState.DEGRADED and self.ws_client.is_connected():
            self.state = ConnectionState.CONNECTED
            logger.info("Feed recovered. State restored to CONNECTED.")

        is_feed_healthy = (self.state == ConnectionState.CONNECTED) and (not is_stale) and (not has_data_loss)

        telemetry = {
            "supervisor_state": self.state.value,
            "is_feed_stale": is_stale,
            "has_data_loss": has_data_loss,
            "is_feed_healthy": is_feed_healthy,
            "elapsed_since_last_tick": elapsed,
            "total_ticks_processed": self.total_ticks_processed,
            "total_candles_processed": self.total_candles_processed,
            "total_candles_rejected": self.total_candles_rejected,
            "queue_depth": len(self.event_queue),
            "queue_overflow_count": self.queue_overflow_count,
            "reconnect_telemetry": self.reconnect_ctrl.get_telemetry(),
            "ws_state": self.ws_client.get_state().value,
            "rest_state": self.rest_client.get_state().value,
        }
        return telemetry

    def reset_overflow_counter(self) -> None:
        """Explicitly reset overflow counter after downstream consumers acknowledge loss."""
        self.queue_overflow_count = 0

    def drain_events(self, acknowledge_overflow: bool = False) -> List[Any]:
        """Drain and return all queued market data events."""
        events = list(self.event_queue)
        self.event_queue.clear()
        if acknowledge_overflow:
            self.queue_overflow_count = 0
        return events
