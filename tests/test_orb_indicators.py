"""Comprehensive unit tests for pure deterministic ORB indicators."""
import numpy as np
import pandas as pd
import pytest
from indicators.orb_indicators import (
    ema,
    ema_slope,
    prior_volume_average,
    atr,
    ema_cross_count,
    add_orb_indicators,
)


def test_ema20_matches_expected_trend():
    s = pd.Series([float(x) for x in range(1, 35)])
    out = ema(s, 20)
    assert out.iloc[:19].isna().all(), "First (period - 1) bars must be NaN"
    assert pd.notna(out.iloc[19]), "Bar at index 19 should have the first valid EMA value"
    assert out.iloc[-1] > out.iloc[-2], "Upward trending series should produce increasing EMA"


def test_ema_slope_positive_negative_and_nan():
    # Empty series
    assert ema_slope(pd.Series(dtype=float)).empty

    # Constant series -> slope is 0.0
    const_s = pd.Series([100.0] * 10)
    slopes = ema_slope(const_s, period=1)
    assert np.isnan(slopes.iloc[0]), "First bar has no prior bar, should be NaN"
    assert slopes.iloc[1:].eq(0.0).all()

    # Increasing series -> slope > 0
    inc_s = pd.Series([10.0, 12.0, 15.0, 20.0])
    inc_slopes = ema_slope(inc_s, period=1)
    assert (inc_slopes.dropna() > 0).all()

    # Decreasing series -> slope < 0
    dec_s = pd.Series([20.0, 18.0, 15.0, 10.0])
    dec_slopes = ema_slope(dec_s, period=1)
    assert (dec_slopes.dropna() < 0).all()


def test_volume_average_strictly_excludes_current_bar():
    # 20 bars of 10.0, 21st bar is 9999.0
    v = pd.Series([10.0] * 20 + [9999.0])
    out = prior_volume_average(v, 20)
    # The 20th bar (index 19) does not have 20 prior completed bars
    assert np.isnan(out.iloc[19])
    # The 21st bar (index 20) has exactly 20 prior bars of 10.0, so mean is 10.0 (excludes 9999.0!)
    assert out.iloc[20] == pytest.approx(10.0)


def test_atr_positive_and_insufficient_history():
    high = pd.Series([102.0, 103.0, 104.0, 103.5])
    low = pd.Series([99.0, 100.0, 101.0, 101.5])
    close = pd.Series([101.0, 102.0, 103.0, 102.0])

    # Period 3 ATR
    out = atr(high, low, close, period=3)
    assert out.iloc[:2].isna().all(), "Bars before period must be NaN"
    assert out.iloc[2] > 0, "ATR must be strictly positive"
    assert out.iloc[3] > 0


def test_ema_cross_count_identifies_chop():
    # Alternating prices above and below EMA 100
    close = pd.Series([101, 99, 101, 99, 101, 99, 101])
    ema_line = pd.Series([100.0] * 7)
    # 6 reversals
    crosses = ema_cross_count(close, ema_line, lookback=7)
    assert crosses == 6

    # Monotonic trend -> 0 crosses
    trend_close = pd.Series([101, 102, 103, 104, 105])
    trend_ema = pd.Series([100.0] * 5)
    assert ema_cross_count(trend_close, trend_ema, lookback=5) == 0


def test_add_orb_indicators_enriches_dataframe():
    idx = pd.date_range("2026-01-01 09:00", periods=30, freq="5min")
    df = pd.DataFrame({
        "Open": [100.0] * 30,
        "High": [101.0] * 30,
        "Low": [99.0] * 30,
        "Close": [100.5] * 30,
        "Volume": [50.0] * 30,
    }, index=idx)
    res = add_orb_indicators(df, ema_period=20, volume_period=20, atr_period=14)
    assert "EMA20" in res.columns
    assert "EMA20_Slope" in res.columns
    assert "VolumeAvg20Prior" in res.columns
    assert "ATR" in res.columns
