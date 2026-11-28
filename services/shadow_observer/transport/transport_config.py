"""Configuration Specification & Validation for Public Market Data Transport.

STRICT PUBLIC-ONLY INVARIANTS:
1. Only Delta Exchange India public REST and WebSocket URLs are permitted.
2. Zero API keys, secrets, or authentication headers are allowed.
3. Any URL or configuration containing execution or account keywords fails immediately.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import logging

from services.shadow_observer.observer_safety_guard import ShadowObserverSafetyViolation

logger = logging.getLogger("shadow_observer.transport.config")

PUBLIC_REST_BASE = "https://api.india.delta.exchange"
PUBLIC_WS_BASE = "wss://socket.india.delta.exchange"
DEFAULT_SYMBOL = "XAUTUSD"

FORBIDDEN_ENDPOINT_KEYWORDS = [
    "order", "orders",
    "position", "positions",
    "wallet", "account",
    "fill", "fills",
    "private", "auth",
    "transfer", "withdraw"
]

FORBIDDEN_AUTH_HEADERS = [
    "api-key", "delta-signature", "delta-timestamp",
    "authorization", "signature", "secret"
]


@dataclass
class TransportConfig:
    """Configuration for public market-data transport."""

    rest_base_url: str = PUBLIC_REST_BASE
    ws_url: str = PUBLIC_WS_BASE
    symbol: str = DEFAULT_SYMBOL
    
    # Timeouts (seconds)
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 30.0
    ping_interval_seconds: float = 15.0
    ping_timeout_seconds: float = 10.0
    
    # Reconnection & Backoff
    max_reconnect_attempts: int = 5
    initial_backoff_seconds: float = 2.0
    max_backoff_seconds: float = 60.0
    backoff_factor: float = 2.0
    jitter_ratio: float = 0.1
    
    # Resource Bounds
    max_message_queue_size: int = 1000
    max_reconnect_history: int = 100
    stale_feed_threshold_seconds: float = 60.0

    # Optional custom headers (e.g. User-Agent)
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.validate_config()

    def validate_config(self) -> None:
        """Enforce strict isolation rules on configured transport parameters."""
        # 1. Validate REST endpoint
        rest_lower = self.rest_base_url.lower()
        if not rest_lower.startswith("https://api.india.delta.exchange") and not rest_lower.startswith("http://localhost"):
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Unauthorized REST base URL: {self.rest_base_url}"
            )
        for kw in FORBIDDEN_ENDPOINT_KEYWORDS:
            if kw in rest_lower:
                raise ShadowObserverSafetyViolation(
                    f"SECURITY VIOLATION: Forbidden keyword '{kw}' in REST URL: {self.rest_base_url}"
                )

        # 2. Validate WebSocket endpoint
        ws_lower = self.ws_url.lower()
        if not ws_lower.startswith("wss://socket.india.delta.exchange") and not ws_lower.startswith("ws://localhost"):
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Unauthorized WebSocket URL: {self.ws_url}"
            )
        for kw in FORBIDDEN_ENDPOINT_KEYWORDS:
            if kw in ws_lower:
                raise ShadowObserverSafetyViolation(
                    f"SECURITY VIOLATION: Forbidden keyword '{kw}' in WS URL: {self.ws_url}"
                )

        # 3. Validate Headers (reject authentication tokens)
        for header_key in self.headers.keys():
            k_lower = header_key.lower()
            for auth_kw in FORBIDDEN_AUTH_HEADERS:
                if auth_kw in k_lower:
                    raise ShadowObserverSafetyViolation(
                        f"SECURITY VIOLATION: Forbidden authentication header '{header_key}' detected in public transport."
                    )

        # 4. Validate symbol
        if not self.symbol or self.symbol.upper() != "XAUTUSD":
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Unsupported or foreign symbol configured: {self.symbol}"
            )

        # 5. Validate numerical limits
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("Timeouts must be positive numbers.")
        if self.max_reconnect_attempts < 1:
            raise ValueError("max_reconnect_attempts must be >= 1.")
        if self.max_message_queue_size < 10:
            raise ValueError("max_message_queue_size must be >= 10.")
