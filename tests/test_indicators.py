"""Unit tests for deterministic technical indicators."""

import pytest
import pandas as pd
import numpy as np

from services.indicators import (
    compute_sma,
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_bollinger_bands,
    compute_atr,
    compute_support_resistance,
    add_all_indicators,
    get_latest_indicators,
)


def test_sma_calculation():
    series = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    sma3 = compute_sma(series, 3)
    assert np.isnan(sma3.iloc[0])
    assert np.isnan(sma3.iloc[1])
    assert pytest.approx(sma3.iloc[2]) == 20.0
    assert pytest.approx(sma3.iloc[3]) == 30.0
    assert pytest.approx(sma3.iloc[4]) == 40.0


def test_ema_calculation():
    series = pd.Series([10.0, 10.0, 10.0, 20.0, 20.0])
    ema = compute_ema(series, 3)
    # EMA smooths towards 20
    assert len(ema) == 5
    assert ema.iloc[4] > ema.iloc[2]


def test_rsi_constant_and_trending():
    # Uptrend prices: RSI should be high
    uptrend = pd.Series([float(i) for i in range(1, 30)])
    rsi_up = compute_rsi(uptrend, 14)
    assert rsi_up.iloc[-1] > 80.0

    # Downtrend prices: RSI should be low
    downtrend = pd.Series([float(100 - i) for i in range(1, 30)])
    rsi_down = compute_rsi(downtrend, 14)
    assert rsi_down.iloc[-1] < 20.0


def test_macd_computation():
    prices = pd.Series([100.0 + i * 0.5 for i in range(50)])
    macd, signal, hist = compute_macd(prices, 12, 26, 9)
    assert len(macd) == 50
    assert len(signal) == 50
    assert len(hist) == 50
    # In persistent steady upward drift, MACD should be positive
    assert macd.iloc[-1] > 0


def test_bollinger_bands():
    prices = pd.Series([100.0] * 30)
    upper, mid, lower = compute_bollinger_bands(prices, 20, 2.0)
    assert pytest.approx(mid.iloc[-1]) == 100.0
    assert pytest.approx(upper.iloc[-1]) == 100.0
    assert pytest.approx(lower.iloc[-1]) == 100.0


def test_atr_positive():
    high = pd.Series([105.0] * 20)
    low = pd.Series([95.0] * 20)
    close = pd.Series([100.0] * 20)
    atr = compute_atr(high, low, close, 14)
    assert atr.iloc[-1] > 0
    assert pytest.approx(atr.iloc[-1], abs=0.5) == 10.0


def test_insufficient_data_handling():
    short_df = pd.DataFrame({
        "Open": [10.0, 11.0],
        "High": [12.0, 13.0],
        "Low": [9.0, 10.0],
        "Close": [11.0, 12.0],
        "Volume": [1000, 1200],
    })
    snapshot = get_latest_indicators(short_df)
    assert snapshot is None
