"""Unit and regression tests for Liquidity Sweep Reversal strategy."""
import pandas as pd
import pytest
from config.orb_settings import ORBSettings
from strategy.liquidity_sweep import evaluate_liquidity_sweep


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "session_start": "09:00",
        "session_end": "23:30",
        "liquidity_sweep_lookback": 20,
        "liquidity_sweep_min_buffer_atr": 0.1,
        "target_r": 2.0,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
        "leverage": 100.0,
        "risk_pct": 2.0,
        "max_margin_pct_equity": 50.0,
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def test_bearish_liquidity_sweep_signal():
    """Prior swing high at 4020. Current bar sweeps up to 4025, but prints upper rejection wick and closes at 4016."""
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [4000.0] * 160
    opens = [4000.0] * 160
    highs = [4005.0] * 160
    lows = [3995.0] * 160
    vols = [100000.0] * 160

    # Create prior swing high of 4020 around bar 145
    highs[145] = 4020.0
    closes[145] = 4015.0

    # Current bar 159 sweeps to 4026 (> 4020), then dumps and closes at 4014 (< 4020)
    opens[159] = 4018.0
    highs[159] = 4026.0  # Big upper rejection wick (4026 - 4018 = 8.0)
    closes[159] = 4014.0 # Closes in bottom of range below prior high 4020
    lows[159] = 4012.0
    vols[159] = 180000.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_liquidity_sweep(df, cfg)

    assert sig.direction == "SHORT"
    assert sig.entry == 4014.0
    assert sig.sl >= 4026.0 # Stop placed beyond sweep high
    assert sig.tp < sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.strategy_name == "LIQUIDITY_SWEEP"


def test_bullish_liquidity_sweep_signal():
    """Prior swing low at 3980. Current bar sweeps down to 3972, then prints lower rejection wick and closes at 3984."""
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 07:00", periods=160, freq="5min", tz="Asia/Kolkata")
    closes = [4000.0] * 160
    opens = [4000.0] * 160
    highs = [4005.0] * 160
    lows = [3995.0] * 160
    vols = [100000.0] * 160

    # Prior swing low of 3980 at bar 145
    lows[145] = 3980.0
    closes[145] = 3985.0

    # Current bar 159 sweeps down to 3972 (< 3980), then rallies to close at 3986 (> 3980)
    opens[159] = 3982.0
    lows[159] = 3972.0   # Big lower rejection wick
    closes[159] = 3986.0 # Closes above prior swing low 3980
    highs[159] = 3988.0
    vols[159] = 180000.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_liquidity_sweep(df, cfg)

    assert sig.direction == "LONG"
    assert sig.entry == 3986.0
    assert sig.sl <= 3972.0 # Stop placed beyond sweep low
    assert sig.tp > sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.strategy_name == "LIQUIDITY_SWEEP"

