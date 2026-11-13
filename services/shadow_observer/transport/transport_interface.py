"""Transport Interfaces and Normalized Market-Data Dataclasses.

DEFINES CONTRACTS FOR:
1. Connection lifecycle states.
2. Normalized Tick events with separate exchange and local receipt timestamps.
3. Normalized Candle events conforming to candle validator inputs.
4. Transport client abstractions.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional


class ConnectionState(str, Enum):
    """Lifecycle states for public market-data transport."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass
class NormalizedTick:
    """Normalized public ticker tick event."""
    symbol: str
    price: float
    exchange_timestamp: Optional[float]
    local_receipt_timestamp: float
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "exchange_timestamp": self.exchange_timestamp,
            "local_receipt_timestamp": self.local_receipt_timestamp,
            "raw_payload": self.raw_payload,
        }


@dataclass
class NormalizedCandle:
    """Normalized completed OHLCV candle event."""
    symbol: str
    timeframe: str  # '5m' or '15m'
    open: float
    high: float
    low: float
    close: float
    volume: float
    candle_start_epoch: int
    candle_end_epoch: int
    is_complete: bool = True
    exchange_timestamp: Optional[float] = None
    local_receipt_timestamp: float = 0.0
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timestamp": self.candle_start_epoch,
            "candle_start_epoch": self.candle_start_epoch,
            "candle_end_epoch": self.candle_end_epoch,
            "is_complete": self.is_complete,
            "exchange_timestamp": self.exchange_timestamp,
            "local_receipt_timestamp": self.local_receipt_timestamp,
            "raw_payload": self.raw_payload,
        }


class ITransportClient(ABC):
    """Abstract interface for public market-data transport implementations."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to public endpoint."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully terminate connection and free resources."""
        pass

    @abstractmethod
    def get_state(self) -> ConnectionState:
        """Return current lifecycle state."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if client is actively connected."""
        pass
