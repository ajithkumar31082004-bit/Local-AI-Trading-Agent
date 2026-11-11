"""Public REST Client for Delta Exchange India Market Data.

OPERATING CONSTRAINTS:
1. Accesses only verified public Delta Exchange India endpoints.
2. Rejects any attempt to query private, trading, account, or order endpoints.
3. Operates completely unauthenticated.
4. Uses injectable HTTP adapter for offline mock testing.
"""

from typing import Dict, Any, List, Optional, Tuple, Callable
from datetime import datetime, timezone
import json
import logging

from services.shadow_observer.observer_safety_guard import ShadowObserverSafetyViolation
from services.shadow_observer.transport.transport_config import TransportConfig, FORBIDDEN_ENDPOINT_KEYWORDS
from services.shadow_observer.transport.transport_interface import (
    ITransportClient,
    ConnectionState,
    NormalizedTick,
    NormalizedCandle,
)

logger = logging.getLogger("shadow_observer.transport.rest")

# Whitelist of permissible public REST paths
ALLOWED_REST_PATHS = [
    "/v2/tickers",
    "/v2/chart/history",
    "/v2/products",
]


class PublicRestClient(ITransportClient):
    """Pure public unauthenticated REST client for Delta Exchange India market data."""

    def __init__(
        self,
        config: Optional[TransportConfig] = None,
        http_adapter: Optional[Callable[[str, str, Dict[str, Any], Optional[Tuple[float, float]]], Tuple[int, Dict[str, Any]]]] = None,
    ):
        self.config = config or TransportConfig()
        self.http_adapter = http_adapter
        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self.request_count: int = 0
        self.rate_limited_count: int = 0
        self.last_response_time: Optional[float] = None

    def connect(self) -> bool:
        """Verify client readiness (REST is connectionless; validates config)."""
        self.config.validate_config()
        self.state = ConnectionState.CONNECTED
        logger.info("Public REST transport ready: %s", self.config.rest_base_url)
        return True

    def disconnect(self) -> None:
        """Set state to STOPPED and release adapter."""
        self.state = ConnectionState.STOPPED
        logger.info("Public REST transport disconnected.")

    def get_state(self) -> ConnectionState:
        return self.state

    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    def _validate_path_safety(self, path: str) -> None:
        """Validate that proposed REST path is strictly within the public allowlist."""
        p_lower = path.lower()
        for forbidden in FORBIDDEN_ENDPOINT_KEYWORDS:
            if forbidden in p_lower:
                msg = f"CRITICAL SECURITY VIOLATION: REST client attempted forbidden path: {path}"
                logger.critical(msg)
                raise ShadowObserverSafetyViolation(msg)

        is_allowed = any(p_lower.startswith(allowed) for allowed in ALLOWED_REST_PATHS)
        if not is_allowed:
            msg = f"SECURITY VIOLATION: Path '{path}' not in public allowlist: {ALLOWED_REST_PATHS}"
            logger.critical(msg)
            raise ShadowObserverSafetyViolation(msg)

    def _execute_request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """Dispatch HTTP request through injected adapter or standard public fetch."""
        self._validate_path_safety(path)
        self.request_count += 1
        now_epoch = datetime.now(timezone.utc).timestamp()
        self.last_response_time = now_epoch

        url = f"{self.config.rest_base_url.rstrip('/')}/{path.lstrip('/')}"
        timeouts = (self.config.connect_timeout_seconds, self.config.read_timeout_seconds)

        if self.http_adapter:
            return self.http_adapter(method, url, params or {}, timeouts)

        # In offline mode, if no adapter is injected, we do NOT touch the network
        raise RuntimeError("OFFLINE SAFETY: No HTTP adapter injected. Live network disabled.")

    def fetch_ticker(self, symbol: Optional[str] = None) -> NormalizedTick:
        """Fetch and normalize latest public ticker for instrument."""
        sym = (symbol or self.config.symbol).upper()
        status_code, body = self._execute_request("GET", f"/v2/tickers/{sym}")

        if status_code == 429:
            self.rate_limited_count += 1
            self.state = ConnectionState.DEGRADED
            raise RuntimeError("HTTP 429: Public REST rate limit exceeded.")

        if status_code != 200 or not body.get("success", False):
            raise RuntimeError(f"REST Ticker fetch failed (HTTP {status_code}): {body.get('error')}")

        result = body.get("result", {})
        price = float(result.get("mark_price", result.get("close", result.get("price", 0.0))))
        exchange_ts = result.get("timestamp")
        exchange_epoch = float(exchange_ts) if exchange_ts else None
        local_receipt = datetime.now(timezone.utc).timestamp()

        return NormalizedTick(
            symbol=sym,
            price=price,
            exchange_timestamp=exchange_epoch,
            local_receipt_timestamp=local_receipt,
            raw_payload=result,
        )

    def fetch_historical_candles(
        self,
        symbol: Optional[str] = None,
        resolution: str = "5m",
        start_epoch: Optional[int] = None,
        end_epoch: Optional[int] = None,
    ) -> List[NormalizedCandle]:
        """Fetch historical completed candles via public chart history endpoint."""
        sym = (symbol or self.config.symbol).upper()
        params: Dict[str, Any] = {
            "symbol": sym,
            "resolution": resolution,
        }
        if start_epoch is not None:
            params["from"] = start_epoch
        if end_epoch is not None:
            params["to"] = end_epoch

        status_code, body = self._execute_request("GET", "/v2/chart/history", params=params)

        if status_code == 429:
            self.rate_limited_count += 1
            self.state = ConnectionState.DEGRADED
            raise RuntimeError("HTTP 429: Rate limit exceeded during candle fetch.")

        if status_code != 200 or not body.get("success", False):
            raise RuntimeError(f"REST Candle fetch failed (HTTP {status_code}): {body.get('error')}")

        result = body.get("result", {})
        candles: List[NormalizedCandle] = []
        now_epoch = datetime.now(timezone.utc).timestamp()

        # Delta returns lists: o, h, l, c, v, t
        opens = result.get("o", [])
        highs = result.get("h", [])
        lows = result.get("l", [])
        closes = result.get("c", [])
        volumes = result.get("v", [])
        timestamps = result.get("t", [])

        length = min(len(opens), len(highs), len(lows), len(closes), len(volumes), len(timestamps))
        interval_sec = 300 if resolution == "5m" else 900

        for i in range(length):
            t_start = int(timestamps[i])
            candles.append(NormalizedCandle(
                symbol=sym,
                timeframe=resolution,
                open=float(opens[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                close=float(closes[i]),
                volume=float(volumes[i]),
                candle_start_epoch=t_start,
                candle_end_epoch=t_start + interval_sec,
                is_complete=True,
                exchange_timestamp=float(t_start),
                local_receipt_timestamp=now_epoch,
                raw_payload={"index": i},
            ))

        return candles
