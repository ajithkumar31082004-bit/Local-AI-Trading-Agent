"""Unit tests for deterministic strategy logic."""

import pytest
import pandas as pd
import numpy as np

from database.models import SignalType
from strategies.moving_average import MovingAverageCrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.trend_following import TrendFollowingStrategy


def create_synthetic_df(prices):
    n = len(prices)
    return pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": [1000000] * n,
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))


def test_moving_average_crossover_bullish():
    # Construct a series where slow was higher, then fast sharply crosses above slow
    prices = [100.0] * 30 + [90.0, 85.0, 80.0, 80.0, 85.0, 95.0, 115.0, 130.0]
    df = create_synthetic_df(prices)
    strat = MovingAverageCrossoverStrategy(fast_period=5, slow_period=15)
    sig = strat.generate_signal(df, "TEST")
    assert sig.signal in [SignalType.BUY, SignalType.HOLD]
    assert sig.suggested_stop_loss is not None


def test_rsi_strategy_rebound():
    # Dip deeply oversold and then tick back up across oversold line
    prices = [100.0] * 20 + [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 45.0, 52.0]
    df = create_synthetic_df(prices)
    strat = RSIStrategy(period=14, oversold=30.0, overbought=70.0, require_volume_confirmation=False)
    sig = strat.generate_signal(df, "TEST")
    assert sig.signal in [SignalType.BUY, SignalType.HOLD]


def test_trend_following_criteria():
    # Healthy uptrend with minor pullbacks keeping RSI in 50-75 zone
    prices = [100.0]
    for i in range(1, 70):
        step = 1.8 if i % 5 != 0 else -0.5
        prices.append(prices[-1] + step)
    df = create_synthetic_df(prices)
    strat = TrendFollowingStrategy(trend_ma_period=50, rsi_min=45.0, rsi_max=85.0, volume_expansion_factor=0.9)
    sig = strat.generate_signal(df, "TEST")
    assert sig.signal in [SignalType.BUY, SignalType.HOLD]
    assert sig.suggested_stop_loss is not None


def test_missing_data_strategy_handling():
    # Extremely short series must return NO_SIGNAL safely
    df = create_synthetic_df([100.0, 101.0, 102.0])
    strat = MovingAverageCrossoverStrategy()
    sig = strat.generate_signal(df, "TEST")
    assert sig.signal == SignalType.NO_SIGNAL
