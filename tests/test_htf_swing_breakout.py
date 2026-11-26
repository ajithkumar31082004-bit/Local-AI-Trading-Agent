"""Unit and walk-forward parity tests for HTF_SWING_BREAKOUT (1h Trend + 15m Donchian)."""
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings
from strategy.htf_swing_breakout import evaluate_htf_swing_breakout


def _make_trending_5m_df(n_bars: int = 750, direction: str = "LONG") -> pd.DataFrame:
    times = pd.date_range("2026-10-01 00:00:00", periods=n_bars, freq="5min", tz="Asia/Kolkata")
    rng = np.random.default_rng(42)
    if direction == "LONG":
        # Steady uptrend, consolidation for ~55 bars, then clean breakout on final 15m bar
        base = 3500.0 + np.linspace(0.0, 60.0, n_bars)
        noise = rng.normal(0.0, 0.4, n_bars)
        close = base + noise
        # Flatten the prior 105 bars (35 15m bars) into a tight range so 32-bar 15m high is ~3559
        close[-108:-3] = 3556.0 + rng.uniform(-1.5, 1.5, 105)
        # Final 15m bar (3 5m bars) breaks out strongly upward
        close[-3:] = [3562.0, 3566.0, 3570.0]
        opn = close - 1.5
        high = close + 2.5
        low = opn - 2.5
        vol = np.full(n_bars, 1000.0)
        vol[-3:] = 2500.0  # Strong breakout volume (> 1.0x 20-bar avg)
    else:
        base = 3600.0 - np.linspace(0.0, 60.0, n_bars)
        noise = rng.normal(0.0, 0.4, n_bars)
        close = base + noise
        close[-108:-3] = 3544.0 + rng.uniform(-1.5, 1.5, 105)
        close[-3:] = [3538.0, 3534.0, 3530.0]
        opn = close + 1.5
        high = opn + 2.5
        low = close - 2.5
        vol = np.full(n_bars, 1000.0)
        vol[-3:] = 2500.0

    return pd.DataFrame({"Open": opn, "High": high, "Low": low, "Close": close, "Volume": vol}, index=times)


def test_htf_swing_breakout_bullish_and_bearish_signals():
    cfg = ORBSettings(strategy_type="HTF_SWING_BREAKOUT", apply_gst=True)

    df_long = _make_trending_5m_df(750, "LONG")
    sig_long = evaluate_htf_swing_breakout(df_long, cfg)
    assert sig_long.direction == "LONG", f"Expected LONG, got {sig_long.direction} ({sig_long.reason})"
    assert sig_long.sl < sig_long.entry < sig_long.tp
    assert sig_long.r_multiple >= 2.5
    assert sig_long.quality_score >= 0.65
    assert sig_long.strategy_name == "HTF_SWING_BREAKOUT"

    df_short = _make_trending_5m_df(750, "SHORT")
    # Default LONG_ONLY mode rejects bearish breakdowns
    sig_short_blocked = evaluate_htf_swing_breakout(df_short, cfg)
    assert sig_short_blocked.direction == "NO TRADE"
    assert sig_short_blocked.diagnostic_code == "HTF_SHORT_DISABLED"

    # Explicit allow_short=True still emits valid SHORT setup
    sig_short = evaluate_htf_swing_breakout(df_short, cfg, allow_short=True)
    assert sig_short.direction == "SHORT", f"Expected SHORT, got {sig_short.direction} ({sig_short.reason})"
    assert sig_short.tp < sig_short.entry < sig_short.sl
    assert sig_short.r_multiple >= 2.5
    assert sig_short.quality_score >= 0.65


def test_htf_swing_breakout_waits_for_completed_15m_bar_only():
    cfg = ORBSettings(strategy_type="HTF_SWING_BREAKOUT")
    df_long = _make_trending_5m_df(750, "LONG")
    # Remove the last 5m bar so current bar is NOT a completed 15m close
    df_mid = df_long.iloc[:-1]
    sig = evaluate_htf_swing_breakout(df_mid, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "WAITING_FOR_15M_CLOSE"


def test_htf_swing_breakout_rejects_low_volume():
    cfg = ORBSettings(strategy_type="HTF_SWING_BREAKOUT")
    df_long = _make_trending_5m_df(750, "LONG")
    df_long.iloc[-3:, df_long.columns.get_loc("Volume")] = 200.0  # Below 1.1x average
    sig = evaluate_htf_swing_breakout(df_long, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "VOLUME_BELOW_THRESHOLD"
