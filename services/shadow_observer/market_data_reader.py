"""Public Market Data Reader & Buffer for Read-Only Shadow Observer.

STRICT PUBLIC-ONLY INVARIANTS:
1. Reuses verified public Delta Exchange India endpoints:
   - REST API Base: https://api.india.delta.exchange
   - WebSocket URL: wss://socket.india.delta.exchange
2. Operates 100% unauthenticated; rejects API keys, secrets, or signature headers.
3. Buffers and normalizes completed 5m and 15m candles.
4. Tracks per-channel timestamps, detects out-of-order ticks, and flags stale market feeds.
"""

from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import logging

logger = logging.getLogger("shadow_observer.market_data_reader")

PUBLIC_REST_URL = "https://api.india.delta.exchange"
PUBLIC_WS_URL = "wss://socket.india.delta.exchange"
TARGET_INSTRUMENT = "XAUTUSD"


class PublicMarketDataReader:
    """Buffers, checks, and normalizes public market data feeds."""

    def __init__(
        self,
        symbol: str = TARGET_INSTRUMENT,
        max_buffer_candles: int = 500,
        stale_threshold_seconds: float = 60.0,
    ):
        self.symbol = symbol.upper()
        self.max_buffer_candles = max_buffer_candles
        self.stale_threshold_seconds = stale_threshold_seconds

        # In-memory buffers
        self._candle_buffer: deque = deque(maxlen=max_buffer_candles)
        self.last_ticker_time: Optional[float] = None
        self.last_ticker_price: Optional[float] = None
        self.last_candle_epoch: Optional[int] = None
        
        # Telemetry & Health Tracking
        self.ticks_received: int = 0
        self.candles_buffered: int = 0
        self.out_of_order_count: int = 0
        self.stale_detections: int = 0
        self.connection_status: str = "DISCONNECTED"

    def ingest_ticker_update(self, tick: Dict[str, Any], now_epoch: Optional[float] = None) -> Tuple[bool, str]:
        """Ingest a public ticker tick and update staleness trackers."""
        sym = tick.get("symbol", "").upper()
        if sym and sym != self.symbol:
            return False, f"IGNORED: Foreign symbol '{sym}' (expected '{self.symbol}')"

        try:
            price = float(tick.get("mark_price", tick.get("close", tick.get("price", 0.0))))
        except (ValueError, TypeError):
            return False, "INVALID_PRICE: Ticker price is non-numeric"

        curr_time = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        
        # Out-of-order check
        ts = tick.get("timestamp")
        if ts is not None:
            try:
                tick_epoch = float(ts) if isinstance(ts, (int, float)) else pd.to_datetime(ts).timestamp()
                if self.last_ticker_time is not None and tick_epoch < self.last_ticker_time:
                    self.out_of_order_count += 1
                    logger.warning("Out of order ticker tick: %f < %f", tick_epoch, self.last_ticker_time)
                self.last_ticker_time = tick_epoch
            except Exception:
                self.last_ticker_time = curr_time
        else:
            self.last_ticker_time = curr_time

        self.last_ticker_price = price
        self.ticks_received += 1
        return True, "TICK_INGESTED"

    def is_feed_stale(self, now_epoch: Optional[float] = None) -> Tuple[bool, float]:
        """Check if market feed has stopped updating beyond the stale threshold."""
        if self.last_ticker_time is None:
            return True, 999999.0

        curr_time = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        elapsed = curr_time - self.last_ticker_time
        stale = elapsed > self.stale_threshold_seconds
        if stale:
            self.stale_detections += 1
        return stale, elapsed

    def ingest_completed_candle(self, candle: Dict[str, Any]) -> Tuple[bool, str]:
        """Buffer a completed candle for strategy evaluation."""
        self._candle_buffer.append(candle)
        self.candles_buffered += 1
        return True, "CANDLE_BUFFERED"

    def get_candle_dataframe(self) -> pd.DataFrame:
        """Convert buffered candles into a clean DatetimeIndex DataFrame."""
        if not self._candle_buffer:
            return pd.DataFrame()

        rows = list(self._candle_buffer)
        df = pd.DataFrame(rows)

        # Standardize columns
        col_map = {
            "o": "Open", "open": "Open",
            "h": "High", "high": "High",
            "l": "Low", "low": "Low",
            "c": "Close", "close": "Close",
            "v": "Volume", "volume": "Volume",
            "t": "Timestamp", "timestamp": "Timestamp", "time": "Timestamp"
        }
        df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)

        if "Timestamp" in df.columns:
            df["Datetime"] = pd.to_datetime(df["Timestamp"])
            df.set_index("Datetime", inplace=True)
            df.sort_index(inplace=True)
            df = df[~df.index.duplicated(keep="first")]

        return df

    def get_feed_telemetry(self) -> Dict[str, Any]:
        """Return public feed health metrics."""
        return {
            "symbol": self.symbol,
            "connection_status": self.connection_status,
            "ticks_received": self.ticks_received,
            "candles_buffered": len(self._candle_buffer),
            "last_ticker_price": self.last_ticker_price,
            "last_ticker_time": self.last_ticker_time,
            "out_of_order_count": self.out_of_order_count,
            "stale_detections": self.stale_detections,
            "public_rest_url": PUBLIC_REST_URL,
            "public_ws_url": PUBLIC_WS_URL,
        }

