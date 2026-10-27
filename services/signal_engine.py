"""Signal Engine: Evaluates strategies deterministically against market data."""

from typing import List, Dict, Optional, Type
import pandas as pd
from database.models import StrategySignal, SignalType
from strategies.base import BaseStrategy
from strategies.moving_average import MovingAverageCrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.custom_strategy import CustomStrategy


class SignalEngine:
    """Orchestrates strategy execution on OHLCV data."""

    def __init__(self):
        self._strategies: Dict[str, BaseStrategy] = {
            "Moving Average Crossover": MovingAverageCrossoverStrategy(),
            "RSI Confirmation": RSIStrategy(),
            "Multi-Factor Trend Following": TrendFollowingStrategy(),
            "Custom Flexible Strategy": CustomStrategy(),
        }

    def get_available_strategies(self) -> List[str]:
        return list(self._strategies.keys())

    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        return self._strategies.get(name)

    def evaluate_strategy(
        self,
        strategy_name: str,
        df: pd.DataFrame,
        symbol: str,
        custom_strategy: Optional[BaseStrategy] = None,
    ) -> StrategySignal:
        """Evaluate a specific strategy and return a deterministic StrategySignal."""
        if custom_strategy:
            return custom_strategy.generate_signal(df, symbol)

        strat = self._strategies.get(strategy_name)
        if not strat:
            return StrategySignal(
                symbol=symbol,
                strategy_name=strategy_name,
                signal=SignalType.NO_SIGNAL,
                rationale=f"Strategy '{strategy_name}' not found.",
            )

        return strat.generate_signal(df, symbol)

    def evaluate_all(self, df: pd.DataFrame, symbol: str) -> List[StrategySignal]:
        """Evaluate all registered standard strategies for a symbol."""
        results = []
        for name, strat in self._strategies.items():
            results.append(strat.generate_signal(df, symbol))
        return results
