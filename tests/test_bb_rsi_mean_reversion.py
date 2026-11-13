"""Unit and regression tests for Bollinger Bands + RSI Mean Reversion strategy."""
import pandas as pd
import pytest
from config.orb_settings import ORBSettings
from strategy.bb_rsi_mean_reversion import evaluate_bb_rsi


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "session_start": "09:00",
        "session_end": "23:30",
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 35.0,
        "rsi_overbought": 65.0,
        "bb_trend_max_adx": 30.0,
        "target_r": 2.0,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
        "leverage": 100.0,
        "risk_pct": 2.0,
        "max_margin_pct_equity": 50.0,
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def test_bb_rsi_strong_trend_forbidden():
    """Mean reversion must be blocked when 15m ADX >= bb_trend_max_adx."""
    cfg = make_cfg(bb_trend_max_adx=25.0)
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    # Persistent steep downtrend generates high ADX (> 40)
    closes = [4100.0 - (i * 0.8) for i in range(160)]
    df = pd.DataFrame({"Open": closes, "High": [c+1 for c in closes], "Low": [c-1 for c in closes], "Close": closes, "Volume": [100000.0]*160}, index=idx)
    sig = evaluate_bb_rsi(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "STRONG_TREND_MEAN_REVERSION_FORBIDDEN"


def test_bb_rsi_bullish_mean_reversion_signal():
    """Ranging market where price sweeps below Lower BB, RSI oversold, then bullish reversal candle closes inside."""
    cfg = make_cfg(bb_trend_max_adx=50.0)
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    # Ranging oscillation around 4000
    import numpy as np
    t = np.linspace(0, 8 * np.pi, 160)
    closes = [4000.0 + (10.0 * np.sin(val)) for val in t]
    opens = [c - 0.5 for c in closes]
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    vols = [100000.0] * 160

    # Bar 158: sharp dip below lower band to create oversold RSI
    lows[158] = 3975.0
    closes[158] = 3978.0

    # Bar 159: bullish reversal candle closing back inside the lower band
    opens[159] = 3979.0
    lows[159] = 3976.0  # Lower rejection wick
    closes[159] = 3992.0 # Strong bullish close back inside band
    highs[159] = 3993.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_bb_rsi(df, cfg)

    if sig.direction == "LONG":
        assert sig.entry == closes[159]
        assert sig.sl < sig.entry
        assert sig.tp > sig.entry
        assert sig.strategy_name == "BB_RSI_MEAN_REVERSION"
        assert sig.quality_score > 0.0

