"""Unit and regression tests for VWAP + EMA Confirmation strategy."""
import pandas as pd
import pytest
from config.orb_settings import ORBSettings
from strategy.vwap_ema import evaluate_vwap_ema
from tests.fixtures.synthetic_market_data import (
    create_synthetic_bearish_continuation_df,
    create_synthetic_bullish_continuation_df,
)


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "session_start": "09:00",
        "session_end": "23:30",
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
    sig1 = evaluate_vwap_ema(pd.DataFrame(), cfg)
    assert sig1.direction == "NO TRADE"
    assert sig1.diagnostic_code == "NO_CANDLE_DATA"

    idx = pd.date_range("2026-10-30 10:00", periods=10, freq="5min", tz="Asia/Kolkata")
    df_small = pd.DataFrame({"Open": [4000.0]*10, "High": [4005.0]*10, "Low": [3995.0]*10, "Close": [4000.0]*10, "Volume": [1000.0]*10}, index=idx)
    sig2 = evaluate_vwap_ema(df_small, cfg)
    assert sig2.direction == "NO TRADE"
    assert sig2.diagnostic_code == "INSUFFICIENT_CANDLES"


def test_confirmed_vwap_ema_short():
    """Bearish 15m trend + 5m below VWAP and EMA20 + pullback test + bearish close."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")
    df = create_synthetic_bearish_continuation_df()
    sig = evaluate_vwap_ema(df, cfg)

    assert sig.direction == "SHORT"
    assert sig.entry == df["Close"].iloc[159]
    assert sig.sl > sig.entry
    assert sig.tp < sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.quality_score > 0.5
    assert sig.strategy_name == "VWAP_EMA"


def test_confirmed_vwap_ema_long():
    """Bullish 15m trend + 5m above VWAP and EMA20 + pullback test + bullish close."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")
    df = create_synthetic_bullish_continuation_df()
    sig = evaluate_vwap_ema(df, cfg)

    assert sig.direction == "LONG"
    assert sig.entry == df["Close"].iloc[159]
    assert sig.sl < sig.entry
    assert sig.tp > sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.strategy_name == "VWAP_EMA"

