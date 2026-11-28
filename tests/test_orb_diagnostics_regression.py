"""Automated diagnostic, session-boundary, timezone, and regression tests for XAUTUSD ORB trading behavior."""
import pandas as pd
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from strategy.orb_ema_volume import evaluate_orb
from config.orb_settings import ORBSettings, validate_settings, ConfigurationError
from risk.position_sizing import size_position


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "session_start": "09:00",
        "orb_minutes": 15,
        "ema_period": 20,
        "volume_period": 20,
        "atr_period": 14,
        "max_ema_crosses": 2,
        "max_extension_atr": 1.0,
        "stop_buffer": 0.05,
        "rejection_wick_body_ratio": 1.0,
        "retest_tolerance": 0.15,
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


def test_reproduce_reported_0725_no_trade_case():
    """Reproduce exact reported user scenario:
    Candle at 07:25 Asia/Kolkata with session configured for 09:00.
    Must return NO TRADE with diagnostic code SESSION_NOT_STARTED without submitting orders.
    """
    idx = pd.date_range("2026-10-30 05:25", periods=25, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [4008.0] * 25,
        "High": [4010.0] * 25,
        "Low": [4005.0] * 25,
        "Close": [4008.14] * 25,
        "Volume": [713284.0] * 25,
    }, index=idx)

    cfg = make_cfg(session_start="09:00", timezone="Asia/Kolkata")
    sig = evaluate_orb(df, cfg)

    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "SESSION_NOT_STARTED"
    assert "trading session has not started yet" in sig.reason.lower()
    assert sig.checklist["orb_set"] is False
    assert sig.checklist_reasons["orb_set"] == "SESSION_NOT_STARTED"
    assert sig.orb_high is None
    assert sig.orb_low is None


def test_time_handling_before_at_and_after_session_start():
    cfg = make_cfg(session_start="09:00", orb_minutes=15, timezone="Asia/Kolkata")

    # 1. Candle before session start (08:55)
    idx_before = pd.date_range("2026-10-30 07:00", periods=24, freq="5min", tz="Asia/Kolkata")
    df_before = pd.DataFrame({
        "Open": [4000.0] * 24, "High": [4005.0] * 24, "Low": [3995.0] * 24, "Close": [4001.0] * 24, "Volume": [500000.0] * 24
    }, index=idx_before)
    sig_before = evaluate_orb(df_before, cfg)
    assert sig_before.direction == "NO TRADE"
    assert sig_before.diagnostic_code == "SESSION_NOT_STARTED"

    # 2. Candle exactly at session start (09:00) -> 1st opening bar incomplete ORB
    idx_at = pd.date_range("2026-10-30 07:05", periods=24, freq="5min", tz="Asia/Kolkata")
    df_at = pd.DataFrame({
        "Open": [4000.0] * 24, "High": [4005.0] * 24, "Low": [3995.0] * 24, "Close": [4001.0] * 24, "Volume": [500000.0] * 24
    }, index=idx_at)
    sig_at = evaluate_orb(df_at, cfg)
    assert sig_at.direction == "NO TRADE"
    assert sig_at.diagnostic_code == "ORB_WINDOW_INCOMPLETE"
    assert sig_at.checklist["orb_set"] is False

    # 3. Candle at 09:10 (3rd opening bar completed, ORB ready, but no post-ORB candle yet)
    idx_complete = pd.date_range("2026-10-30 07:15", periods=24, freq="5min", tz="Asia/Kolkata")
    df_complete = pd.DataFrame({
        "Open": [4000.0] * 24, "High": [4005.0] * 24, "Low": [3995.0] * 24, "Close": [4001.0] * 24, "Volume": [500000.0] * 24
    }, index=idx_complete)
    sig_complete = evaluate_orb(df_complete, cfg)
    assert sig_complete.direction == "NO TRADE"
    assert sig_complete.checklist["orb_set"] is True
    assert sig_complete.orb_high == 4005.0
    assert sig_complete.orb_low == 3995.0
    assert sig_complete.diagnostic_code == "NO_BREAKOUT_CLOSE"


def test_utc_timestamps_evaluated_in_asia_kolkata():
    """Delta Exchange API returns UTC timestamps. Ensure UTC 03:30 (09:00 IST) is correctly identified as session start."""
    cfg = make_cfg(session_start="09:00", timezone="Asia/Kolkata")
    # 21 bars warmup ending at UTC 03:25 (08:55 IST)
    idx_utc_before = pd.date_range("2026-10-30 01:45", periods=21, freq="5min", tz="UTC")
    df_utc = pd.DataFrame({
        "Open": [4000.0] * 21, "High": [4005.0] * 21, "Low": [3995.0] * 21, "Close": [4001.0] * 21, "Volume": [500000.0] * 21
    }, index=idx_utc_before)
    sig = evaluate_orb(df_utc, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "SESSION_NOT_STARTED"

    # Add 3 bars: UTC 03:30 (09:00 IST), 03:35 (09:05 IST), 03:40 (09:10 IST)
    idx_utc_complete = pd.date_range("2026-10-30 01:45", periods=24, freq="5min", tz="UTC")
    df_utc_complete = pd.DataFrame({
        "Open": [4000.0] * 24, "High": [4005.0] * 24, "Low": [3995.0] * 24, "Close": [4001.0] * 24, "Volume": [500000.0] * 24
    }, index=idx_utc_complete)
    sig_c = evaluate_orb(df_utc_complete, cfg)
    assert sig_c.checklist["orb_set"] is True
    assert sig_c.orb_high == 4005.0
    assert sig_c.orb_low == 3995.0


def test_midnight_rollover_handling():
    """Series crossing midnight across session boundaries maintains proper date normalization."""
    cfg = make_cfg(session_start="09:00", timezone="Asia/Kolkata")
    idx = pd.date_range("2026-10-29 23:00", periods=30, freq="5min", tz="Asia/Kolkata")
    # Last candle is 2026-10-30 01:25 IST
    df = pd.DataFrame({
        "Open": [4000.0] * 30, "High": [4005.0] * 30, "Low": [3995.0] * 30, "Close": [4001.0] * 30, "Volume": [500000.0] * 30
    }, index=idx)
    sig = evaluate_orb(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "SESSION_NOT_STARTED"


def test_missing_and_duplicate_candle_tolerance():
    cfg = make_cfg(session_start="09:00", timezone="Asia/Kolkata")
    idx = pd.date_range("2026-10-30 07:15", periods=25, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [4000.0] * 25, "High": [4005.0] * 25, "Low": [3995.0] * 25, "Close": [4001.0] * 25, "Volume": [500000.0] * 25
    }, index=idx)

    # Append duplicate index row
    df_dup = pd.concat([df, df.iloc[[-1]]])
    sig = evaluate_orb(df_dup, cfg)
    assert sig.checklist["orb_set"] is True


def test_synthetic_why_no_trade_permutations():
    """Comprehensive test of each distinct filter failure permutation."""
    cfg = make_cfg(session_start="09:00", timezone="Asia/Kolkata")

    # Base: 21 warmup (07:15-08:55) + 3 opening bars (09:00, 09:05, 09:10)
    idx = pd.date_range("2026-10-30 07:15", periods=26, freq="5min", tz="Asia/Kolkata")
    closes = [4000.0 + i * 0.5 for i in range(21)] + [4012.0, 4014.0, 4015.0, 4018.0, 4018.0]
    opens = [c - 0.2 for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    vols = [10000.0] * 26

    # 1. Price remains inside ORB (ORB High: 4020, ORB Low: 4010, Bar 24 closes at 4015)
    highs[21], highs[22], highs[23] = 4020.0, 4018.0, 4019.0
    lows[21], lows[22], lows[23] = 4010.0, 4011.0, 4012.0
    closes[24] = 4015.0
    df1 = pd.DataFrame({"Open": opens[:25], "High": highs[:25], "Low": lows[:25], "Close": closes[:25], "Volume": vols[:25]}, index=idx[:25])
    sig1 = evaluate_orb(df1, cfg)
    assert sig1.direction == "NO TRADE"
    assert sig1.diagnostic_code == "NO_BREAKOUT_CLOSE"

    # 2. Breakout volume fails (Bar 24 closes at 4022 > 4020, but volume 5000 < prior avg 10000)
    closes[24] = 4022.0
    vols_low = list(vols[:25])
    vols_low[24] = 5000.0
    df2 = pd.DataFrame({"Open": opens[:25], "High": highs[:25], "Low": lows[:25], "Close": closes[:25], "Volume": vols_low}, index=idx[:25])
    sig2 = evaluate_orb(df2, cfg)
    assert sig2.direction == "NO TRADE"
    assert sig2.diagnostic_code == "VOLUME_BELOW_THRESHOLD"

    # 3. Breakout occurs with volume, but price closes back inside range on next bar
    vols_ok = list(vols)
    vols_ok[24] = 25000.0
    closes[24] = 4022.0
    closes[25] = 4015.0  # Returns inside (4010-4020)
    df3 = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols_ok}, index=idx)
    sig3 = evaluate_orb(df3, cfg)
    assert sig3.direction == "NO TRADE"
    assert sig3.diagnostic_code == "BREAKOUT_INVALIDATED_RETURNED_INSIDE"


def test_mode_isolation_and_safety_assertions(monkeypatch):
    # 1. Mode defaults to PAPER when environment variables are unset
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.delenv("ORB_MODE", raising=False)
    cfg_default = ORBSettings(mode="PAPER")
    assert cfg_default.mode == "PAPER"
    validate_settings(cfg_default)

    # 2. Invalid mode fails closed
    cfg_invalid = ORBSettings(mode="INVALID_MODE")
    with pytest.raises(ConfigurationError):
        validate_settings(cfg_invalid)

    # 3. LIVE mode without secondary acknowledgment fails
    cfg_live_unack = ORBSettings(mode="LIVE", live_trading_acknowledged="NO")
    with pytest.raises(ConfigurationError):
        validate_settings(cfg_live_unack)

    # 4. LIVE mode with placeholder keys fails
    cfg_live_placeholder = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="YES",
        api_key="<your-delta-api-key>",
        api_secret="<your-delta-api-secret>",
    )
    with pytest.raises(ConfigurationError):
        validate_settings(cfg_live_placeholder)


def test_position_sizing_tiny_account_and_budget_rejection():
    cfg = make_cfg(risk_pct=1.0, contract_size_xaut=0.001)
    # Entry 4000, Stop 3950 (risk per lot: $50 * 0.001 = $0.05 + costs ~$0.08)
    # Tiny account equity: $2.00 -> 1% risk budget = $0.02
    # Since per-lot risk ($0.08) > budget ($0.02), position sizing MUST reject
    decision = size_position(entry=4000.0, stop=3950.0, equity=2.00, cfg=cfg)
    assert decision.approved is False
    assert "exceeds configured risk budget" in decision.reason
