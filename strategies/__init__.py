"""Strategies package."""

from strategies.base import BaseStrategy
from strategies.moving_average import MovingAverageCrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.custom_strategy import CustomStrategy

__all__ = [
    "BaseStrategy",
    "MovingAverageCrossoverStrategy",
    "RSIStrategy",
    "TrendFollowingStrategy",
    "CustomStrategy",
]
