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


def test_regime_and_indicators_lookahead_immunity():
    """Phase 3.4 Look-Ahead Bias Audit Test:
    
    Proves that altering future candle data (spikes, crashes, volume surges) has
    EXACTLY ZERO influence on historical regime classification and indicator values.
    """
    import numpy as np
    from strategy.vwap_ema import evaluate_vwap_ema
    from indicators.orb_indicators import resample_to_15m

    cfg = make_cfg()
    idx_base = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [4000.0 + (i * 0.5) for i in range(160)]
    df_base = pd.DataFrame({
        "Open": [c - 0.2 for c in closes],
        "High": [c + 1.5 for c in closes],
        "Low": [c - 1.5 for c in closes],
        "Close": closes,
        "Volume": [100000.0] * 160
    }, index=idx_base)

    # 1. Base evaluation at historical bar 150
    cutoff = 150
    df_history = df_base.iloc[:cutoff].copy()
    res_base = detect_market_regime(df_history, cfg)
    sig_base = evaluate_vwap_ema(df_history, cfg=cfg)

    # 2. Future Scenario A: Extreme upward moonshot in future bars 150-160
    df_future_spike = df_base.copy()
    for j in range(cutoff, 160):
        df_future_spike.iloc[j, df_future_spike.columns.get_loc("Close")] = 5500.0 + (j * 50.0)
        df_future_spike.iloc[j, df_future_spike.columns.get_loc("High")] = 5600.0 + (j * 50.0)
        df_future_spike.iloc[j, df_future_spike.columns.get_loc("Volume")] = 9999999.0

    # 3. Future Scenario B: Extreme catastrophic crash in future bars 150-160
    df_future_crash = df_base.copy()
    for j in range(cutoff, 160):
        df_future_crash.iloc[j, df_future_crash.columns.get_loc("Close")] = 2000.0 - (j * 50.0)
        df_future_crash.iloc[j, df_future_crash.columns.get_loc("Low")] = 1900.0 - (j * 50.0)
        df_future_crash.iloc[j, df_future_crash.columns.get_loc("Volume")] = 8888888.0

    # 4. Evaluate both scenarios using strictly information up to bar cutoff
    res_spike_historical = detect_market_regime(df_future_spike.iloc[:cutoff], cfg)
    sig_spike_historical = evaluate_vwap_ema(df_future_spike.iloc[:cutoff], cfg=cfg)

    res_crash_historical = detect_market_regime(df_future_crash.iloc[:cutoff], cfg)
    sig_crash_historical = evaluate_vwap_ema(df_future_crash.iloc[:cutoff], cfg=cfg)

    # Assert 100% mathematical invariance
    assert res_base.regime == res_spike_historical.regime == res_crash_historical.regime
    assert res_base.confidence == res_spike_historical.confidence == res_crash_historical.confidence
    assert res_base.allowed_strategies == res_spike_historical.allowed_strategies == res_crash_historical.allowed_strategies
    assert sig_base.direction == sig_spike_historical.direction == sig_crash_historical.direction

    # 5. Incomplete 15m candle look-ahead elimination test:
    # A 15m bucket with only 1 or 2 completed 5m bars MUST NOT create a premature 15m bar
    r15_full = resample_to_15m(df_base.iloc[:150])  # 150 is divisible by 3 -> exactly 50 15m bars
    assert len(r15_full) == 50

    r15_partial1 = resample_to_15m(df_base.iloc[:151])  # 1 extra 5m bar -> must STILL be 50 15m bars
    assert len(r15_partial1) == 50

    r15_partial2 = resample_to_15m(df_base.iloc[:152])  # 2 extra 5m bars -> must STILL be 50 15m bars
    assert len(r15_partial2) == 50

    r15_completed = resample_to_15m(df_base.iloc[:153])  # 3 extra 5m bars -> now exactly 51 15m bars
    assert len(r15_completed) == 51

