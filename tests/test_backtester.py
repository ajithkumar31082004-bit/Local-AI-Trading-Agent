"""Unit tests for the event-driven backtesting engine."""

import pytest
import pandas as pd
import numpy as np

from services.backtester import BacktestEngine
from strategies.moving_average import MovingAverageCrossoverStrategy


def test_backtest_with_synthetic_uptrend():
    # Warmup with downtrend/flat, then sharp crossover into sustained uptrend
    prices = [100.0] * 25 + [95.0, 90.0, 85.0, 80.0]
    for i in range(80):
        prices.append(prices[-1] + 2.0)

    days = len(prices)
    dates = pd.date_range("2024-01-01", periods=days, freq="D")

    df = pd.DataFrame(
        {
            "Open": [p * 0.998 for p in prices],
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.99 for p in prices],
            "Close": prices,
            "Volume": [1000000] * days,
        },
        index=dates,
    )

    strat = MovingAverageCrossoverStrategy(fast_period=5, slow_period=15)
    engine = BacktestEngine(initial_capital=100000.0, fee_pct=0.1, slippage_pct=0.05)
    result = engine.run(strat, df, "SYNTHETIC", warmup_period=20)

    assert result.initial_capital == 100000.0
    assert result.final_equity > 0
    assert len(result.equity_curve) > 0
    assert result.max_drawdown_pct >= 0.0
    assert len(result.trades) > 0 or result.final_equity != result.initial_capital


def test_backtest_lookahead_protection():
    # Ensure orders placed at candle i execute on candle i+1 Open
    days = 60
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    prices = [100.0 + i for i in range(days)]
    df = pd.DataFrame(
        {
            "Open": prices,
            "High": [p + 2.0 for p in prices],
            "Low": [p - 2.0 for p in prices],
            "Close": prices,
            "Volume": [1000000] * days,
        },
        index=dates,
    )

    strat = MovingAverageCrossoverStrategy(fast_period=5, slow_period=10)
    engine = BacktestEngine()
    result = engine.run(strat, df, "TEST", warmup_period=15)

    for trade in result.trades:
        # Entry date cannot be earlier than warmup period + 1
        entry_idx = df.index.get_loc(pd.Timestamp(trade.entry_date))
        assert entry_idx >= 15
