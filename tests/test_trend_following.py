"""Unit, regression, and comparison tests for the 15m/5m Trend-Following strategy."""
import pandas as pd
import pytest
from config.orb_settings import ORBSettings
from strategy.trend_following import evaluate_trend_following, resample_to_15m


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "session_start": "09:00",
        "session_end": "23:30",
        "orb_minutes": 15,
        "ema_period": 20,
        "volume_period": 20,
        "atr_period": 14,
        "target_r": 2.0,
        "fee_pct_per_side": 0.01,
        "spread_pct_round_trip": 0.02,
        "slippage_pct_round_trip": 0.02,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
        "leverage": 100.0,
        "risk_pct": 2.0,
        "max_margin_pct_equity": 50.0,
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def test_empty_or_insufficient_candles_returns_no_trade():
    cfg = make_cfg()
    sig1 = evaluate_trend_following(pd.DataFrame(), cfg)
    assert sig1.direction == "NO TRADE"
    assert sig1.diagnostic_code == "NO_CANDLE_DATA"

    # Only 10 candles
    idx = pd.date_range("2026-10-30 10:00", periods=10, freq="5min", tz="Asia/Kolkata")
    df_small = pd.DataFrame({"Open": [4000.0]*10, "High": [4005.0]*10, "Low": [3995.0]*10, "Close": [4000.0]*10, "Volume": [1000.0]*10}, index=idx)
    sig2 = evaluate_trend_following(df_small, cfg)
    assert sig2.direction == "NO TRADE"
    assert sig2.diagnostic_code == "INSUFFICIENT_CANDLES"


def test_outside_session_hours_rejected():
    cfg = make_cfg(session_start="09:00", session_end="23:30")
    # 60 candles from 01:00 AM to 05:55 AM IST (strictly before 09:00 AM)
    idx = pd.date_range("2026-10-30 01:00", periods=60, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({"Open": [4000.0]*60, "High": [4005.0]*60, "Low": [3995.0]*60, "Close": [4000.0]*60, "Volume": [1000.0]*60}, index=idx)
    sig = evaluate_trend_following(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "OUTSIDE_SESSION_HOURS"


def test_trend_following_confirmed_short_signal():
    """Construct a clean 15m bearish trend + 5m pullback + bearish rejection continuation candle."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")

    # 160 bars starting at 07:00 IST (ends at 20:15 IST, inside session window).
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    # Downtrend from 4100 down
    closes = [4100.0 - (i * 0.8) for i in range(160)]
    opens = [c + 0.5 for c in closes]
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    vols = [100000.0] * 160

    # Near the end (bars 155-157), make price rally up to test 5m EMA20 (pullback)
    closes[156] = closes[155] + 5.0
    highs[156] = closes[156] + 4.0

    # Bar 159 (current bar): Bearish continuation candle with upper rejection wick
    opens[159] = closes[158] + 1.0
    highs[159] = opens[159] + 3.0  # Upper rejection wick
    closes[159] = opens[159] - 4.0 # Bearish close below open
    lows[159] = closes[159] - 0.5
    vols[159] = 150000.0           # Strong volume

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_trend_following(df, cfg)

    assert sig.direction == "SHORT"
    assert sig.entry == closes[159]
    assert sig.sl > sig.entry
    assert sig.tp < sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.diagnostic_code == "SIGNAL_CONFIRMED"
    assert sig.checklist["trend_15m_aligned"] is True
    assert sig.checklist["pullback_confirmed"] is True
    assert sig.checklist["continuation_candle"] is True
    assert sig.checklist["volume_confirmed"] is True


def test_trend_following_confirmed_long_signal():
    """Construct a clean 15m bullish trend + 5m pullback + bullish rejection continuation candle."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")

    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    # Uptrend from 3800 up
    closes = [3800.0 + (i * 0.8) for i in range(160)]
    opens = [c - 0.5 for c in closes]
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    vols = [100000.0] * 160

    # Pullback dip near the end
    closes[156] = closes[155] - 5.0
    lows[156] = closes[156] - 4.0

    # Bar 159: Bullish continuation candle with lower rejection wick
    opens[159] = closes[158] - 1.0
    lows[159] = opens[159] - 3.0   # Lower rejection wick
    closes[159] = opens[159] + 4.0 # Bullish close above open
    highs[159] = closes[159] + 0.5
    vols[159] = 150000.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_trend_following(df, cfg)

    assert sig.direction == "LONG"
    assert sig.entry == closes[159]
    assert sig.sl < sig.entry
    assert sig.tp > sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.diagnostic_code == "SIGNAL_CONFIRMED"


def test_trend_following_overextended_rejected():
    """Verify that chasing an overextended candle (> 2.5 * ATR from EMA20) is rejected."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [4100.0 - (i * 0.8) for i in range(160)]
    opens = [c + 0.5 for c in closes]
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    vols = [100000.0] * 160

    # Dump the last candle far below EMA20 (overextended)
    closes[159] = closes[158] - 50.0
    lows[159] = closes[159] - 2.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_trend_following(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "PRICE_OVEREXTENDED"


def test_trend_following_low_volume_rejected():
    """Verify that continuation candle with insufficient volume is rejected."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [4100.0 - (i * 0.8) for i in range(160)]
    opens = [c + 0.5 for c in closes]
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    vols = [100000.0] * 160

    closes[156] = closes[155] + 5.0
    highs[156] = closes[156] + 4.0

    opens[159] = closes[158] + 1.0
    highs[159] = opens[159] + 3.0
    closes[159] = opens[159] - 4.0
    lows[159] = closes[159] - 0.5
    vols[159] = 1000.0  # Very low volume (<< 0.8 * 100,000)

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_trend_following(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "VOLUME_BELOW_THRESHOLD"


def test_trend_following_neutral_trend_rejected():
    """Verify that conflicting 15m trend (Close < EMA20 while EMA20 > EMA50) returns TREND_15M_NEUTRAL."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [3800.0 + (i * 1.5) for i in range(160)]
    for j in range(140, 158):
        closes[j] = 3970.0
    closes[158] = 3950.0
    closes[159] = 3950.0
    opens = [c for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    vols = [100000.0] * 160

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_trend_following(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "TREND_15M_NEUTRAL"
