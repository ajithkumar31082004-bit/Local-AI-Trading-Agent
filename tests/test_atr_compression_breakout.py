"""Unit and regression tests for ATR Volatility Compression Breakout strategy."""
import pandas as pd
import pytest
from config.orb_settings import ORBSettings
from strategy.atr_compression_breakout import evaluate_atr_compression_breakout


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "session_start": "09:00",
        "session_end": "23:30",
        "atr_compression_lookback": 15,
        "atr_compression_ratio": 0.90,
        "max_friction_to_risk_ratio": 0.60,
        "target_r": 2.0,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
        "leverage": 100.0,
        "risk_pct": 2.0,
        "max_margin_pct_equity": 50.0,
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def test_atr_compression_breakout_long():
    """Tight consolidation followed by strong candle breakout with volume expansion."""
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    
    # Flat base at 4000
    closes = [4000.0] * 160
    opens = [4000.0] * 160
    highs = [4001.0] * 160
    lows = [3999.0] * 160
    vols = [50000.0] * 160

    # 15 bars before current bar are tight compression (width 2.0)
    # Current bar 159: breakout above 4001
    opens[159] = 4000.5
    lows[159] = 4000.0
    closes[159] = 4005.0 # Decisive close above 4001
    highs[159] = 4005.5
    vols[159] = 200000.0 # Volume surge

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_atr_compression_breakout(df, cfg)

    assert sig.direction == "LONG"
    assert sig.entry == 4005.0
    assert sig.sl < sig.entry
    assert sig.tp > sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.strategy_name == "ATR_COMPRESSION_BREAKOUT"


def test_atr_compression_wick_only_rejected():
    """Wick pierces above consolidation high but closes inside -> rejected."""
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [4000.0] * 160
    opens = [4000.0] * 160
    highs = [4001.0] * 160
    lows = [3999.0] * 160
    vols = [50000.0] * 160

    # Current bar wicks to 4005, but closes back at 4000.5 (inside range)
    opens[159] = 4000.0
    highs[159] = 4005.0
    closes[159] = 4000.5
    lows[159] = 3999.5
    vols[159] = 150000.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_atr_compression_breakout(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "FALSE_BREAKOUT_WICK_ONLY"
