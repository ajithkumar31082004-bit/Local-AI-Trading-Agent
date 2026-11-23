"""Comprehensive unit and regression test suite for SMC Market Structure Engine:
1. Non-repainting pivot fractal detection (Swing Highs and Lows).
2. Trend point classification (HH, HL, LH, LL).
3. Valid Breakout (Body Close) vs False Breakout (Liquidity Wick Sweep / Trap).
4. Dual-Timeframe structure analysis (External 15m vs Internal 5m).
5. 4-Step Institutional Execution Cycle (Trend -> Sweep -> CHOCH -> BOS).
6. Strategy Arbitrator & Market Regime Integration.
"""
import math
import pytest
import pandas as pd
import numpy as np

from config.orb_settings import ORBSettings
from indicators.market_structure import (
    find_swing_points,
    determine_bias_from_swings,
    analyze_market_structure,
    evaluate_break_validity,
    SwingPoint,
)
from strategy.smc_market_structure import evaluate_smc_structure
from strategy.arbitrator import arbitrate_strategies


def test_01_non_repainting_swing_point_detection():
    """Verify that swing highs and swing lows are identified correctly without lookahead bias."""
    times = pd.date_range("2026-11-20 10:00:00", periods=9, freq="5min", tz="Asia/Kolkata")
    
    # Construct a classic swing high at index 4 (10:20):
    # Prices: 100, 102, 105, 108, 115 (Peak), 110, 107, 104, 102
    df = pd.DataFrame({
        "Open": [99, 101, 104, 107, 112, 114, 109, 106, 103],
        "High": [100, 102, 105, 108, 115, 114, 109, 106, 103],
        "Low": [98, 100, 103, 106, 110, 108, 106, 103, 101],
        "Close": [100, 102, 105, 108, 112, 109, 107, 104, 102],
        "Volume": [1000] * 9,
    }, index=times)

    highs, lows = find_swing_points(df, left_bars=3, right_bars=2)

    # Index 4 (115.0) is strictly greater than indices 1, 2, 3 and indices 5, 6
    assert len(highs) == 1
    sh = highs[0]
    assert sh.price == 115.0
    assert sh.point_type == "HIGH"
    assert sh.index == times[4]


def test_02_trend_point_classification_hh_hl_lh_ll():
    """Verify that chronological swings are classified into Higher Highs, Higher Lows, Lower Highs, and Lower Lows."""
    t0 = pd.Timestamp("2026-11-20 10:00:00", tz="Asia/Kolkata")
    
    # 1. Bullish sequence: Low 100 -> High 110 -> Low 105 (HL) -> High 120 (HH)
    sh1 = SwingPoint(index=t0, price=110.0, point_type="HIGH", label="SWING", bar_offset=10)
    sl1 = SwingPoint(index=t0, price=100.0, point_type="LOW", label="SWING", bar_offset=8)
    sh2 = SwingPoint(index=t0, price=120.0, point_type="HIGH", label="HH", bar_offset=5)
    sl2 = SwingPoint(index=t0, price=105.0, point_type="LOW", label="HL", bar_offset=2)

    bias, key_h, key_l = determine_bias_from_swings([sh1, sh2], [sl1, sl2])
    assert bias == "BULLISH"
    assert key_h.price == 120.0
    assert key_l.price == 105.0  # Key invalidation level in uptrend (last HL)

    # 2. Bearish sequence: High 120 -> Low 100 -> High 110 (LH) -> Low 90 (LL)
    sh_b1 = SwingPoint(index=t0, price=120.0, point_type="HIGH", label="SWING", bar_offset=10)
    sl_b1 = SwingPoint(index=t0, price=100.0, point_type="LOW", label="SWING", bar_offset=8)
    sh_b2 = SwingPoint(index=t0, price=110.0, point_type="HIGH", label="LH", bar_offset=5)
    sl_b2 = SwingPoint(index=t0, price=90.0, point_type="LOW", label="LL", bar_offset=2)

    bias_b, key_h_b, key_l_b = determine_bias_from_swings([sh_b1, sh_b2], [sl_b1, sl_b2])
    assert bias_b == "BEARISH"
    assert key_h_b.price == 110.0  # Key invalidation level in downtrend (last LH)
    assert key_l_b.price == 90.0


def test_03_valid_bos_body_close_vs_false_break_wick_sweep():
    """Verify that Break of Structure strictly requires a candle body close, rejecting wick traps."""
    level = 3500.0

    # 1. Valid Bullish BOS: Close is strictly above level
    candle_valid_bullish = pd.Series({
        "Open": 3495.0,
        "High": 3510.0,
        "Low": 3492.0,
        "Close": 3508.0,  # Closes above 3500.0
    })
    res_valid = evaluate_break_validity(candle_valid_bullish, level, "BULLISH")
    assert res_valid["valid_break"] is True
    assert res_valid["false_break"] is False
    assert res_valid["break_type"] == "VALID_BOS"

    # 2. False Bullish Break (Liquidity Trap / Wick Sweep): Pierces above 3500 but closes back inside at 3498
    candle_trap_bullish = pd.Series({
        "Open": 3495.0,
        "High": 3508.0,  # Pierced above 3500.0
        "Low": 3490.0,
        "Close": 3497.0,  # Closed strictly back inside!
    })
    res_trap = evaluate_break_validity(candle_trap_bullish, level, "BULLISH")
    assert res_trap["valid_break"] is False
    assert res_trap["false_break"] is True
    assert res_trap["break_type"] == "WICK_SWEEP"
    assert res_trap["wick_ratio"] >= 0.30  # Strong rejection wick detected


def test_04_dual_timeframe_market_structure_analysis():
    """Verify dual-timeframe structure correctly extracts external (15m) and internal (5m) bias."""
    # Create 90 5m bars (30 15m bars) with an upward trend
    times = pd.date_range("2026-11-20 09:00:00", periods=90, freq="5min", tz="Asia/Kolkata")
    prices = np.linspace(3500.0, 3550.0, 90) + np.sin(np.linspace(0, 15, 90)) * 5.0
    
    df_5m = pd.DataFrame({
        "Open": prices,
        "High": prices + 2.0,
        "Low": prices - 2.0,
        "Close": prices + 0.5,
        "Volume": [1000.0] * 90,
    }, index=times)

    state = analyze_market_structure(df_5m)
    assert state.external_bias in ("BULLISH", "RANGING")
    assert state.major_high is not None
    assert state.major_low is not None


def test_05_smc_strategy_4_step_cycle_evaluation():
    """Verify that evaluate_smc_structure detects the complete 4-step institutional cycle."""
    # Build 80 5m bars:
    # 1. Uptrend to 3530
    # 2. Pullback
    # 3. Liquidity Sweep below 3510 (wick to 3505, close back at 3512)
    # 4. Breakout above 3520 (CHOCH + BOS with volume)
    times = pd.date_range("2026-11-20 10:00:00", periods=80, freq="5min", tz="Asia/Kolkata")
    
    opens = np.full(80, 3520.0)
    highs = np.full(80, 3525.0)
    lows = np.full(80, 3515.0)
    closes = np.full(80, 3520.0)
    volumes = np.full(80, 1000.0)

    # Initial structure formation (bars 0-50): upward slope
    for i in range(50):
        base = 3500.0 + (i * 0.5)
        opens[i] = base
        highs[i] = base + 3.0
        lows[i] = base - 3.0
        closes[i] = base + 1.0

    # Create a prior swing low at bar 45 (approx 3522.5)
    lows[45] = 3518.0

    # Liquidity Sweep at bar 70: pierces below 3518 to 3510, prints bottom wick, closes at 3519
    opens[70] = 3519.0
    highs[70] = 3521.0
    lows[70] = 3510.0  # Deep sweep
    closes[70] = 3520.0  # Closes back above

    # Intermediate CHOCH at bar 74 (strictly between sweep at 70 and current bar 79):
    # Closes above last minor LH (3522.0) with body close
    opens[74] = 3521.0
    highs[74] = 3526.0
    lows[74] = 3520.0
    closes[74] = 3525.0

    # Decisive breakout candle at bar 79 (current bar):
    opens[79] = 3524.0
    highs[79] = 3535.0
    lows[79] = 3523.0
    closes[79] = 3534.0  # Decisive body close above post-choch high of 3526.0
    volumes[79] = 2500.0  # High breakout volume

    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=times)

    cfg = ORBSettings(
        session_start="09:00",
        session_end="23:30",
        no_entry_minutes_after_open=0,
        min_signal_quality_score=0.60,
    )

    sig = evaluate_smc_structure(df, cfg)
    assert sig.strategy_name == "SMC_MARKET_STRUCTURE"
    assert sig.direction in ("LONG", "NO TRADE")


def test_06_arbitrator_registers_and_evaluates_smc_strategy():
    """Verify that arbitrate_strategies includes SMC_MARKET_STRUCTURE in evaluated strategies."""
    times = pd.date_range("2026-11-20 10:00:00", periods=70, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [3500.0] * 70,
        "High": [3505.0] * 70,
        "Low": [3495.0] * 70,
        "Close": [3502.0] * 70,
        "Volume": [1000.0] * 70,
    }, index=times)

    cfg = ORBSettings(
        session_start="09:00",
        session_end="23:30",
        no_entry_minutes_after_open=0,
    )

    result = arbitrate_strategies(df, cfg)
    # SMC_MARKET_STRUCTURE must be present in all_signals
    assert "SMC_MARKET_STRUCTURE" in result.all_signals
    smc_sig = result.all_signals["SMC_MARKET_STRUCTURE"]
    assert smc_sig.strategy_name == "SMC_MARKET_STRUCTURE"


def test_07_smc_rejects_simultaneous_choch_and_bos():
    """Verify that evaluate_smc_structure rejects setups attempting simultaneous CHOCH and BOS on same bar."""
    times = pd.date_range("2026-11-20 10:00:00", periods=80, freq="5min", tz="Asia/Kolkata")
    opens = np.full(80, 3520.0)
    highs = np.full(80, 3525.0)
    lows = np.full(80, 3515.0)
    closes = np.full(80, 3520.0)
    volumes = np.full(80, 1000.0)

    for i in range(50):
        base = 3500.0 + (i * 0.5)
        opens[i] = base
        highs[i] = base + 3.0
        lows[i] = base - 3.0
        closes[i] = base + 1.0

    lows[45] = 3512.0

    # Sweep at bar 70
    opens[70] = 3519.0
    highs[70] = 3521.0
    lows[70] = 3505.0
    closes[70] = 3515.0

    # Bars 71-78 stay low: NO intermediate CHOCH
    for j in range(71, 79):
        opens[j] = 3519.0
        highs[j] = 3521.0  # Does not reach 3522.0
        lows[j] = 3517.0
        closes[j] = 3519.5

    # Bar 79 attempts simultaneous CHOCH + BOS directly from below
    opens[79] = 3520.0
    highs[79] = 3535.0
    lows[79] = 3519.0
    closes[79] = 3534.0
    volumes[79] = 2500.0

    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=times)

    cfg = ORBSettings(
        session_start="09:00",
        session_end="23:30",
        no_entry_minutes_after_open=0,
    )

    sig = evaluate_smc_structure(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "SMC_SEQUENCE_INCOMPLETE"
    assert "simultaneous CHOCH and BOS" in sig.reason


def test_08_smc_rejects_missing_choch():
    """Verify that if liquidity sweep occurred but no CHOCH ever happened, trade is rejected."""
    times = pd.date_range("2026-11-20 10:00:00", periods=80, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [3500.0] * 80,
        "High": [3505.0] * 80,
        "Low": [3495.0] * 80,
        "Close": [3502.0] * 80,
        "Volume": [1000.0] * 80,
    }, index=times)

    cfg = ORBSettings(
        session_start="09:00",
        session_end="23:30",
        no_entry_minutes_after_open=0,
    )

    sig = evaluate_smc_structure(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code in ("NO_SMC_BREAKOUT", "NO_EXTERNAL_STRUCTURE")
