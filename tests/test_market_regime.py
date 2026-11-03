"""Unit and regression tests for Market Regime Detection module."""
import pandas as pd
import pytest
from config.orb_settings import ORBSettings
from regime.market_regime import MarketRegime, detect_market_regime


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def test_insufficient_data_returns_uncertain():
    cfg = make_cfg()
    res = detect_market_regime(pd.DataFrame(), cfg)
    assert res.regime == MarketRegime.UNCERTAIN
    assert res.confidence == 0.0
    assert len(res.allowed_strategies) == 0


def test_trending_bearish_regime_detected():
    """Consistent downward trend with ADX >= 22 and price below VWAP."""
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [4100.0 - (i * 0.8) for i in range(160)]
    df = pd.DataFrame({
        "Open": [c + 0.5 for c in closes],
        "High": [c + 2.0 for c in closes],
        "Low": [c - 2.0 for c in closes],
        "Close": closes,
        "Volume": [100000.0] * 160
    }, index=idx)

    res = detect_market_regime(df, cfg)
    assert res.regime == MarketRegime.TRENDING_BEARISH
    assert res.confidence >= 0.60
    assert "TREND_FOLLOWING" in res.allowed_strategies
    assert "VWAP_EMA" in res.allowed_strategies
    assert "BB_RSI_MEAN_REVERSION" in res.disallowed_strategies


def test_trending_bullish_regime_detected():
    """Consistent upward trend with ADX >= 22 and price above VWAP."""
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [3800.0 + (i * 0.8) for i in range(160)]
    df = pd.DataFrame({
        "Open": [c - 0.5 for c in closes],
        "High": [c + 2.0 for c in closes],
        "Low": [c - 2.0 for c in closes],
        "Close": closes,
        "Volume": [100000.0] * 160
    }, index=idx)

    res = detect_market_regime(df, cfg)
    assert res.regime == MarketRegime.TRENDING_BULLISH
    assert res.confidence >= 0.60
    assert "TREND_FOLLOWING" in res.allowed_strategies
    assert "VWAP_EMA" in res.allowed_strategies
    assert "BB_RSI_MEAN_REVERSION" in res.disallowed_strategies


def test_ranging_regime_detected():
    """Ranging sideways market with low ADX and frequent EMA crosses."""
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    # Low-amplitude oscillation around 4000
    import numpy as np
    t = np.linspace(0, 10 * np.pi, 160)
    closes = [4000.0 + (2.0 * np.sin(val)) for val in t]
    df = pd.DataFrame({
        "Open": closes,
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [50000.0] * 160
    }, index=idx)

    res = detect_market_regime(df, cfg)
    assert res.regime == MarketRegime.RANGING
    assert "BB_RSI_MEAN_REVERSION" in res.allowed_strategies or "LIQUIDITY_SWEEP" in res.allowed_strategies
    assert "TREND_FOLLOWING" in res.disallowed_strategies

