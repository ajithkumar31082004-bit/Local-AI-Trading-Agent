"""Unit tests for statistical validation, Wilson score confidence intervals, and metric calculations."""
import pytest
from services.statistics import wilson_score_interval, required_sample_size, calculate_trade_statistics


def test_wilson_score_interval_known_examples():
    """Verify Wilson score confidence interval against known mathematical benchmarks."""
    # 0 observations
    p, low, up = wilson_score_interval(0, 0)
    assert p == 0.0 and low == 0.0 and up == 0.0

    # 5 wins out of 10 trades (p = 0.50)
    p, low, up = wilson_score_interval(5, 10, confidence=0.95)
    assert p == 0.50
    assert 0.23 < low < 0.24  # ~0.2366
    assert 0.76 < up < 0.77   # ~0.7634

    # 2 wins out of 7 trades (Phase 3 forward test: p = 0.2857)
    p, low, up = wilson_score_interval(2, 7, confidence=0.95)
    assert round(p, 4) == 0.2857
    assert 0.08 < low < 0.09  # ~0.0822 (8.2%)
    assert 0.64 < up < 0.65   # ~0.6411 (64.1%)

    # 100% win rate on 10 trades
    p, low, up = wilson_score_interval(10, 10, confidence=0.95)
    assert p == 1.0
    assert 0.72 < low < 0.73  # Lower bound reflects uncertainty despite 10/10 wins
    assert up == 1.0


def test_sample_size_requirements():
    """Verify sample size required for statistical precision."""
    # With p=0.35 and +/-5% margin of error, N >= 350 trades
    n_350 = required_sample_size(p=0.35, margin_of_error=0.05)
    assert n_350 == 350

    # With p=0.50 and +/-5% margin of error, N >= 385 trades
    n_385 = required_sample_size(p=0.50, margin_of_error=0.05)
    assert n_385 == 385

    # 7, 25, or 30 trades cannot achieve +/-5% precision
    assert 7 < n_350
    assert 30 < n_350


def test_calculate_trade_statistics():
    """Verify trade statistics calculation across multiple trades."""
    trades = [
        {
            "trade_id": "T1",
            "direction": "SHORT",
            "market_regime": "TRENDING_BEARISH",
            "gross_pnl": 0.035,
            "fees": 0.002,
            "gst": 0.00036,
            "spread_cost": 0.0008,
            "slippage_cost": 0.0008,
            "total_friction": 0.00396,
            "net_pnl": 0.03104,
            "trade_r_multiple": 2.2,
        },
        {
            "trade_id": "T2",
            "direction": "LONG",
            "market_regime": "TRENDING_BULLISH",
            "gross_pnl": -0.012,
            "fees": 0.002,
            "gst": 0.00036,
            "spread_cost": 0.0008,
            "slippage_cost": 0.0008,
            "total_friction": 0.00396,
            "net_pnl": -0.01596,
            "trade_r_multiple": -1.0,
        },
    ]

    stats = calculate_trade_statistics(trades)
    assert stats["total_trades"] == 2
    assert stats["winning_trades"] == 1
    assert stats["losing_trades"] == 1
    assert stats["win_rate_pct"] == 50.0
    assert stats["profit_factor"] == round(0.03104 / 0.01596, 2)
    assert stats["long_trades"] == 1
    assert stats["short_trades"] == 1
    assert "TRENDING_BEARISH" in stats["regime_attribution"]
    assert "TRENDING_BULLISH" in stats["regime_attribution"]

