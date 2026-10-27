"""Base Strategy interface and contract."""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import pandas as pd
from database.models import StrategySignal, SignalType


class BaseStrategy(ABC):
    """Abstract trading strategy evaluated on OHLCV data."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Explanation of the strategy principles."""
        pass

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, symbol: str) -> StrategySignal:
        """Evaluate market data and generate a deterministic StrategySignal."""
        pass

    @abstractmethod
    def get_parameters(self) -> Dict[str, Any]:
        """Return configurable parameters and their current values."""
        pass
