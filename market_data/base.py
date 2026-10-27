"""Base abstract market data provider."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
import pandas as pd


@dataclass
class MarketDataMeta:
    """Metadata regarding retrieved market data."""
    symbol: str
    provider: str
    interval: str
    latest_timestamp: Optional[str] = None
    is_delayed: bool = True
    delay_minutes: int = 15
    is_stale: bool = False
    warning: Optional[str] = None
    row_count: int = 0


@dataclass
class MarketDataResult:
    """Result of market data retrieval containing OHLCV DataFrame and metadata."""
    df: pd.DataFrame
    meta: MarketDataMeta
    error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.error is None and not self.df.empty and len(self.df) > 0


class BaseMarketDataProvider(ABC):
    """Abstract interface for financial market data providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the data provider."""
        pass

    @abstractmethod
    def get_historical_ohlcv(
        self,
        symbol: str,
        period: str = "6mo",
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> MarketDataResult:
        """Fetch historical Open, High, Low, Close, Volume data."""
        pass

    @abstractmethod
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Fetch the latest observable price for a given ticker."""
        pass
