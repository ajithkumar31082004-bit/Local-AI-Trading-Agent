"""Real-time live market data feed for XAUTUSD from Delta Exchange India.

Connects to public WebSocket feed (wss://socket.india.delta.exchange) and
REST API (https://api.india.delta.exchange) without requiring trading API keys.

Features:
- Subscribes to candlestick_5m, v2/ticker, and all_trades for XAUTUSD.
- Validates product ID 131253 and symbol XAUTUSD.
- Pre-seeds completed 5m candles via REST on startup.
- Detects completed 5m candles and builds resampled 15m candles.
- Never emits incomplete/forming candles as completed signals.
- Handles WebSocket disconnects with automatic exponential backoff reconnection.
- Thread-safe access to latest prices, tickers, and completed candle DataFrames.
"""
import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable, Tuple
import pandas as pd
import requests
import websockets

from config.orb_settings import ORBSettings, settings as default_orb_settings
from data.delta_candles import fetch_5m_candles, DeltaCandleError
from indicators.orb_indicators import resample_to_15m

logger = logging.getLogger(__name__)

PRODUCT_ID_XAUTUSD = 131253
SYMBOL_XAUTUSD = "XAUTUSD"


class DeltaMarketDataError(RuntimeError):
    """Raised when market data connection or validation fails."""
    pass


class DeltaLiveMarketDataFeed:
    """Manages real-time WebSocket streaming and candle building for XAUTUSD."""

    def __init__(
        self,
        symbol: str = SYMBOL_XAUTUSD,
        product_id: int = PRODUCT_ID_XAUTUSD,
        cfg: Optional[ORBSettings] = None,
        ws_url: Optional[str] = None,
        rest_url: Optional[str] = None,
    ):
        self.symbol = symbol.strip().upper()
        self.product_id = int(product_id)
        self.cfg = cfg or default_orb_settings
        self.ws_url = ws_url or getattr(self.cfg, "ws_base_url", "wss://socket.india.delta.exchange")
        self.rest_url = rest_url or getattr(self.cfg, "api_base_url", "https://api.india.delta.exchange")

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Candle DataFrames
        self._completed_5m: pd.DataFrame = pd.DataFrame()
        self._completed_15m: pd.DataFrame = pd.DataFrame()
        self._current_forming_bar: Optional[Dict[str, Any]] = None
        self._current_forming_start_ts: Optional[int] = None

        # Ticker & State
        self._latest_price: float = 0.0
        self._mark_price: float = 0.0
        self._bid: float = 0.0
        self._ask: float = 0.0
        self._volume_24h: float = 0.0
        self._last_update_ts: Optional[datetime] = None
        self._last_candle_completed_ts: Optional[datetime] = None
        self._ws_connected: bool = False
        self._rest_connected: bool = False
        self._reconnect_attempts: int = 0
        self._last_error: Optional[str] = None

        # Callbacks
        self._candle_callbacks: List[Callable[[pd.DataFrame, pd.DataFrame, Dict[str, Any]], None]] = []
        self._tick_callbacks: List[Callable[[float, Dict[str, Any]], None]] = []

    # -------------------------------------------------------------------------
    # Public Control Methods
    # -------------------------------------------------------------------------

    def start(self, bootstrap_lookback: int = 300) -> bool:
        """Bootstrap historical candles via REST and start WebSocket streaming."""
        with self._lock:
            if self._running:
                logger.info("DeltaLiveMarketDataFeed is already running.")
                return True

            # 1. Validate Product on Delta Exchange
            val_ok, val_err = self.validate_product()
            if not val_ok:
                self._last_error = f"Product validation failed: {val_err}"
                logger.error(self._last_error)
                raise DeltaMarketDataError(self._last_error)

            # 2. Bootstrap initial historical candles
            logger.info("Bootstrapping %d completed 5m candles for %s...", bootstrap_lookback, self.symbol)
            try:
                seed_df = fetch_5m_candles(self.symbol, lookback_candles=bootstrap_lookback)
                if not seed_df.empty:
                    self._completed_5m = seed_df.copy()
                    self._completed_15m = resample_to_15m(self._completed_5m)
                    self._latest_price = float(seed_df["Close"].iloc[-1])
                    self._last_update_ts = datetime.now(timezone.utc)
                    self._rest_connected = True
                    logger.info("Successfully bootstrapped %d 5m candles (latest close: $%.2f).", len(seed_df), self._latest_price)
                else:
                    logger.warning("REST seed returned empty candle frame.")
            except Exception as exc:
                self._last_error = f"REST candle bootstrap failed: {exc}"
                logger.warning(self._last_error)

            # 3. Start background asyncio loop for WebSocket
            self._running = True
            self._thread = threading.Thread(target=self._run_async_loop, daemon=True, name="DeltaWSFeedThread")
            self._thread.start()
            return True

    def stop(self) -> None:
        """Stop background WebSocket feed gracefully."""
        with self._lock:
            self._running = False
            self._ws_connected = False

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("DeltaLiveMarketDataFeed stopped.")

    def register_candle_callback(self, cb: Callable[[pd.DataFrame, pd.DataFrame, Dict[str, Any]], None]) -> None:
        """Register callback invoked whenever a new 5-minute candle completes."""
        with self._lock:
            if cb not in self._candle_callbacks:
                self._candle_callbacks.append(cb)

    def register_tick_callback(self, cb: Callable[[float, Dict[str, Any]], None]) -> None:
        """Register callback invoked on each ticker or trade price update."""
        with self._lock:
            if cb not in self._tick_callbacks:
                self._tick_callbacks.append(cb)

    # -------------------------------------------------------------------------
    # Thread-Safe Data Getters
    # -------------------------------------------------------------------------

    def get_latest_price(self) -> float:
        with self._lock:
            return self._latest_price

    def get_completed_5m_candles(self) -> pd.DataFrame:
        with self._lock:
            return self._completed_5m.copy()

    def get_completed_15m_candles(self) -> pd.DataFrame:
        with self._lock:
            return self._completed_15m.copy()

    def is_candle_complete(self, timestamp_epoch: int, timeframe: str = "5m") -> bool:
        """Check whether a candle starting at timestamp_epoch has completed.
        
        A candle is complete if the current forming bar timestamp has strictly advanced past it.
        """
        interval_sec = 300 if timeframe == "5m" else 900
        with self._lock:
            if self._current_forming_start_ts is None:
                return False
            return (timestamp_epoch + interval_sec) <= self._current_forming_start_ts

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "symbol": self.symbol,
                "product_id": self.product_id,
                "is_running": self._running,
                "ws_connected": self._ws_connected,
                "is_connected": self._ws_connected,
                "rest_connected": self._rest_connected,
                "latest_price": self._latest_price,
                "mark_price": self._mark_price,
                "bid": self._bid,
                "ask": self._ask,
                "volume_24h": self._volume_24h,
                "completed_5m_bars": len(self._completed_5m),
                "completed_15m_bars": len(self._completed_15m),
                "last_update": self._last_update_ts.isoformat() if self._last_update_ts else None,
                "last_candle_completed": self._last_candle_completed_ts.isoformat() if self._last_candle_completed_ts else None,
                "reconnect_attempts": self._reconnect_attempts,
                "last_error": self._last_error,
                "ws_url": self.ws_url,
                "rest_url": self.rest_url,
            }

    # -------------------------------------------------------------------------
    # Validation & Diagnostics
    # -------------------------------------------------------------------------

    def validate_product(self) -> Tuple[bool, Optional[str]]:
        """Validate with Delta Exchange REST API that the symbol and product ID match."""
        url = f"{self.rest_url}/v2/products"
        try:
            resp = requests.get(url, params={"symbol": self.symbol}, timeout=10)
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code} querying products"
            payload = resp.json()
            products = payload.get("result", []) if isinstance(payload, dict) else payload
            for p in products:
                if str(p.get("symbol", "")).upper() == self.symbol:
                    actual_id = int(p.get("id", 0))
                    if self.product_id != 0 and actual_id != self.product_id:
                        return False, f"Product ID mismatch: expected {self.product_id}, exchange reported {actual_id}."
                    logger.info("Validated %s on Delta Exchange (Product ID: %d, Contract: %s).", self.symbol, actual_id, p.get("contract_type"))
                    return True, None
            return False, f"Symbol '{self.symbol}' not found in Delta Exchange product catalog."
        except Exception as exc:
            return False, f"Network error during product validation: {exc}"

    # -------------------------------------------------------------------------
    # Internal Async Worker
    # -------------------------------------------------------------------------

    def _run_async_loop(self) -> None:
        """Run asyncio event loop in dedicated background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._websocket_supervision_loop())
        except (RuntimeError, asyncio.CancelledError):
            pass
        finally:
            try:
                # Cancel all remaining tasks
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self._loop.close()
            except Exception:
                pass

    async def _websocket_supervision_loop(self) -> None:
        """Supervises WebSocket connection with automatic exponential backoff reconnection."""
        backoff = 1.0
        max_backoff = 30.0

        while self._running:
            try:
                logger.info("Connecting to Delta WebSocket: %s", self.ws_url)
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    open_timeout=10,
                ) as ws:
                    with self._lock:
                        self._ws_connected = True
                        self._last_error = None
                        self._reconnect_attempts = 0
                    backoff = 1.0
                    logger.info("Connected to Delta WebSocket. Subscribing to channels for %s...", self.symbol)

                    # Send subscription payload
                    sub_msg = {
                        "type": "subscribe",
                        "payload": {
                            "channels": [
                                {"name": "v2/ticker", "symbols": [self.symbol]},
                                {"name": "candlestick_5m", "symbols": [self.symbol]},
                                {"name": "all_trades", "symbols": [self.symbol]},
                            ]
                        },
                    }
                    await ws.send(json.dumps(sub_msg))

                    while self._running:
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            self._handle_raw_message(raw_msg)
                        except asyncio.TimeoutError:
                            # Send manual heartbeat ping if quiet
                            await ws.send(json.dumps({"type": "ping"}))

            except (websockets.ConnectionClosed, OSError, Exception) as exc:
                with self._lock:
                    self._ws_connected = False
                    self._reconnect_attempts += 1
                    self._last_error = f"WebSocket connection drop: {exc}"

                if not self._running:
                    break

                logger.warning(
                    "WebSocket disconnected (%s). Reconnecting in %.1fs (attempt #%d)...",
                    exc,
                    backoff,
                    self._reconnect_attempts,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, max_backoff)

    def _handle_raw_message(self, raw_msg: str) -> None:
        """Parse incoming WebSocket JSON messages."""
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")
        channel = data.get("channel") or msg_type

        # 1. 5m Candlestick Update
        if msg_type == "candlestick_5m" or channel == "candlestick_5m":
            self._process_candlestick_5m(data)

        # 2. Ticker Update
        elif msg_type == "v2/ticker" or channel == "v2/ticker":
            self._process_ticker(data)

        # 3. Trades Update
        elif msg_type == "all_trades" or channel == "all_trades":
            self._process_trade(data)

    def _process_candlestick_5m(self, data: Dict[str, Any]) -> None:
        """Process incoming 5m candlestick payload from Delta WebSocket."""
        # Check symbol match
        if str(data.get("symbol", "")).upper() != self.symbol:
            return

        start_time_raw = data.get("candle_start_time")
        if start_time_raw is None:
            return

        # Delta candle_start_time is in microseconds
        start_ts = int(start_time_raw) // 1_000_000
        opn = float(data.get("open", 0.0))
        high = float(data.get("high", 0.0))
        low = float(data.get("low", 0.0))
        close = float(data.get("close", 0.0))
        vol = float(data.get("volume", 0.0))

        bar_dict = {
            "timestamp": pd.to_datetime(start_ts, unit="s", utc=True),
            "Open": opn,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": vol,
            "start_ts": start_ts,
        }

        candle_to_dispatch: Optional[Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]] = None

        with self._lock:
            # Check if forming bar start timestamp has advanced
            if self._current_forming_start_ts is not None and start_ts > self._current_forming_start_ts:
                # The previous forming bar has officially completed!
                prev_bar = self._current_forming_bar
                if prev_bar:
                    prev_ts = prev_bar["timestamp"]
                    # Append completed bar to self._completed_5m if not already present
                    new_row = pd.DataFrame([{
                        "Open": prev_bar["Open"],
                        "High": prev_bar["High"],
                        "Low": prev_bar["Low"],
                        "Close": prev_bar["Close"],
                        "Volume": prev_bar["Volume"],
                    }], index=[prev_ts])
                    new_row.index.name = "timestamp"

                    if self._completed_5m.empty:
                        self._completed_5m = new_row
                    elif prev_ts not in self._completed_5m.index:
                        self._completed_5m = pd.concat([self._completed_5m, new_row]).sort_index()

                    # Keep last 500 completed candles in memory
                    if len(self._completed_5m) > 500:
                        self._completed_5m = self._completed_5m.iloc[-500:]

                    # Update 15m resampled candles
                    self._completed_15m = resample_to_15m(self._completed_5m)
                    self._last_candle_completed_ts = datetime.now(timezone.utc)
                    logger.info("New 5m completed candle finalized: %s Close=$%.2f Vol=%.0f", prev_ts, prev_bar["Close"], prev_bar["Volume"])

                    candle_to_dispatch = (self._completed_5m.copy(), self._completed_15m.copy(), dict(prev_bar))

            # Update currently forming bar cache
            self._current_forming_start_ts = start_ts
            self._current_forming_bar = bar_dict
            self._latest_price = close
            self._last_update_ts = datetime.now(timezone.utc)

        # Dispatch completed candle callbacks outside lock
        if candle_to_dispatch:
            df5, df15, bar_info = candle_to_dispatch
            for cb in self._candle_callbacks:
                try:
                    cb(df5, df15, bar_info)
                except Exception as exc:
                    logger.error("Error in candle callback: %s", exc, exc_info=True)

    def _process_ticker(self, data: Dict[str, Any]) -> None:
        """Process real-time ticker data."""
        if str(data.get("symbol", "")).upper() != self.symbol:
            return

        with self._lock:
            mark = float(data.get("mark_price", 0.0) or 0.0)
            close = float(data.get("close", 0.0) or mark or self._latest_price)
            if mark > 0:
                self._mark_price = mark
            if close > 0:
                self._latest_price = close
            self._bid = float(data.get("best_bid", 0.0) or 0.0)
            self._ask = float(data.get("best_ask", 0.0) or 0.0)
            self._volume_24h = float(data.get("volume_24h", 0.0) or 0.0)
            self._last_update_ts = datetime.now(timezone.utc)
            price = self._latest_price
            ticker_copy = {
                "price": price,
                "mark_price": self._mark_price,
                "bid": self._bid,
                "ask": self._ask,
                "volume_24h": self._volume_24h,
            }

        for cb in self._tick_callbacks:
            try:
                cb(price, ticker_copy)
            except Exception as exc:
                logger.debug("Error in tick callback: %s", exc)

    def _process_trade(self, data: Dict[str, Any]) -> None:
        """Process trade events."""
        if str(data.get("symbol", "")).upper() != self.symbol:
            return
        price_val = data.get("price")
        if price_val is not None:
            try:
                p = float(price_val)
                with self._lock:
                    self._latest_price = p
                    self._last_update_ts = datetime.now(timezone.utc)
                for cb in self._tick_callbacks:
                    try:
                        cb(p, {"price": p, "trade": True})
                    except Exception as exc:
                        logger.debug("Error in trade tick callback: %s", exc)
            except (ValueError, TypeError):
                pass
