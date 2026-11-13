"""Comprehensive unit tests for the deterministic ORB + EMA20 + Volume strategy rules."""
import pandas as pd
import pytest
from strategy.orb_ema_volume import evaluate_orb, calculate_cost_adjusted_target
from config.orb_settings import ORBSettings


def make_config(**kwargs):
    defaults = {
        "ema_period": 20,
        "volume_period": 20,
        "atr_period": 14,
        "session_start": "09:00",
        "orb_minutes": 15,
        "max_ema_crosses": 3,
        "max_extension_atr": 5.0,
        "stop_buffer": 0.05,
        "rejection_wick_body_ratio": 0.5,
        "retest_tolerance": 0.5,
        "target_r": 2.0,
        "fee_pct_per_side": 0.01,
        "spread_pct_round_trip": 0.02,
        "slippage_pct_round_trip": 0.02,
        "timezone": "Asia/Kolkata",
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def test_empty_input_is_no_trade():
    sig = evaluate_orb(pd.DataFrame(), make_config())
    assert sig.direction == "NO TRADE"
    assert "no candle" in sig.reason.lower()
    assert sig.checklist["orb_set"] is False


def test_before_session_start_is_no_trade():
    # 25 warmup candles ending at 08:55 AM IST (before 09:00 AM session start)
    idx = pd.date_range("2026-01-02 06:55", periods=25, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [2600.0] * 25,
        "High": [2602.0] * 25,
        "Low": [2598.0] * 25,
        "Close": [2600.5] * 25,
        "Volume": [100.0] * 25,
    }, index=idx)
    sig = evaluate_orb(df, make_config())
    assert sig.direction == "NO TRADE"
    assert "not started yet" in sig.reason.lower()


def test_incomplete_opening_range_is_no_trade():
    # 21 warmup bars (07:15 to 08:55) + 2 opening range bars (09:00, 09:05) = 23 bars
    # The 3rd candle at 09:10 hasn't completed yet
    idx = pd.date_range("2026-01-02 07:15", periods=23, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [2600.0] * 23,
        "High": [2602.0] * 23,
        "Low": [2598.0] * 23,
        "Close": [2600.5] * 23,
        "Volume": [100.0] * 23,
    }, index=idx)
    sig = evaluate_orb(df, make_config())
    assert sig.direction == "NO TRADE"
    assert "opening range incomplete" in sig.reason.lower()


def test_wick_only_breakout_rejected():
    # 21 warmup bars + 3 opening bars (ORB High: 2610, ORB Low: 2600) + 1 bar at 09:15 that wicks to 2615 but closes inside (2605)
    idx = pd.date_range("2026-01-02 07:15", periods=25, freq="5min", tz="Asia/Kolkata")
    closes = [2590.0 + i * 0.5 for i in range(21)] + [2604.0, 2603.0, 2605.0, 2605.0]
    opens = [c - 0.2 for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    highs[21], highs[22], highs[23] = 2610.0, 2608.0, 2607.0
    lows[21], lows[22], lows[23] = 2602.0, 2601.0, 2600.0

    # Candle 24 at 09:15 wicks above 2610 to 2615, but closes at 2605
    highs[24] = 2615.0
    closes[24] = 2605.0
    vols = [100.0] * 24 + [500.0]

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_orb(df, make_config())
    assert sig.direction == "NO TRADE"
    assert sig.checklist["breakout_close"] is False
    assert "inside the opening range" in sig.reason.lower() or "waiting" in sig.reason.lower()


def test_breakout_volume_confirmation_failure():
    # 21 warmup + 3 opening (ORB High: 2610) + 1 breakout bar at 09:15 closing at 2614, but volume (50) fails (< 100)
    idx = pd.date_range("2026-01-02 07:15", periods=25, freq="5min", tz="Asia/Kolkata")
    closes = [2590.0 + i * 0.5 for i in range(21)] + [2604.0, 2603.0, 2605.0, 2614.0]
    opens = [c - 0.2 for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    highs[21], highs[22], highs[23] = 2610.0, 2608.0, 2607.0
    lows[21], lows[22], lows[23] = 2602.0, 2601.0, 2600.0

    vols = [100.0] * 24 + [50.0]  # Volume fails

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_orb(df, make_config())
    assert sig.direction == "NO TRADE"
    assert sig.checklist["volume_ok"] is False
    assert "volume" in sig.reason.lower()


def test_breakout_closes_back_inside_range_invalidates():
    # 21 warmup + 3 opening (ORB High: 2610, Low: 2600) + breakout at 09:15 (close 2614) + bar at 09:20 closing inside (2605)
    idx = pd.date_range("2026-01-02 07:15", periods=26, freq="5min", tz="Asia/Kolkata")
    closes = [2590.0 + i * 0.5 for i in range(21)] + [2604.0, 2603.0, 2605.0, 2614.0, 2605.0]
    opens = [c - 0.2 for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    highs[21], highs[22], highs[23] = 2610.0, 2608.0, 2607.0
    lows[21], lows[22], lows[23] = 2602.0, 2601.0, 2600.0

    vols = [100.0] * 24 + [500.0, 100.0]

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_orb(df, make_config())
    assert sig.direction == "NO TRADE"
    assert "back inside the opening range" in sig.reason.lower()


def test_valid_long_retest_and_rejection_signal():
    idx = pd.date_range("2026-01-02 07:15", periods=26, freq="5min", tz="Asia/Kolkata")
    closes = [2590.0 + i * 0.5 for i in range(21)] + [2604.0, 2603.0, 2605.0, 2613.0, 2613.0]
    opens = [c - 0.2 for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    # Opening bars: index 21 (09:00), 22 (09:05), 23 (09:10)
    highs[21], highs[22], highs[23] = 2610.0, 2608.0, 2607.0
    lows[21], lows[22], lows[23] = 2602.0, 2601.0, 2600.0

    # Breakout candle: index 24 (09:15)
    vols = [100.0] * 24 + [500.0, 150.0]
    highs[24] = 2614.0
    lows[24] = 2608.0

    # Retest & rejection candle: index 25 (09:20)
    opens[25] = 2611.2
    lows[25] = 2610.2
    closes[25] = 2613.0
    highs[25] = 2614.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    sig = evaluate_orb(df, make_config(rejection_wick_body_ratio=0.5, retest_tolerance=0.5, max_extension_atr=5.0))

    assert sig.direction == "LONG"
    assert sig.entry == 2613.0
    assert sig.sl < sig.entry
    assert sig.tp > sig.entry
    assert sig.r_multiple >= 2.0
    assert sig.checklist["retest"] is True
    assert sig.checklist["rejection"] is True
    assert sig.checklist["rr_ok"] is True


def test_cost_adjusted_net_target_at_least_2r():
    cfg = make_config(fee_pct_per_side=0.01, spread_pct_round_trip=0.02, slippage_pct_round_trip=0.02)
    entry, sl = 2600.0, 2590.0  # $10 gross stop distance
    tp, net_r = calculate_cost_adjusted_target("LONG", entry, sl, cfg, target_r=2.0)
    assert tp > entry + 20.0, "Cost-adjusted target must exceed nominal 2R target to cover fees and slippage"
    assert net_r >= 2.0, f"Net R must be >= 2.0, got {net_r}"
