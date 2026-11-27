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


def test_htf_swing_breakout_close_at_eod_default_and_override(monkeypatch):
    monkeypatch.delenv("ORB_CLOSE_AT_EOD", raising=False)
    monkeypatch.setenv("STRATEGY_TYPE", "ORB")
    cfg_orb = ORBSettings(strategy_type="ORB")
    cfg_htf = ORBSettings(strategy_type="HTF_SWING_BREAKOUT")
    assert cfg_orb.close_at_eod is True
    assert cfg_htf.close_at_eod is False

    monkeypatch.setenv("ORB_CLOSE_AT_EOD", "true")
    assert ORBSettings(strategy_type="HTF_SWING_BREAKOUT").close_at_eod is True
    monkeypatch.setenv("ORB_CLOSE_AT_EOD", "false")
    assert ORBSettings(strategy_type="ORB").close_at_eod is False


def test_htf_swing_breakout_rejects_falling_1h_ema20():
    cfg = ORBSettings(strategy_type="HTF_SWING_BREAKOUT")
    times = pd.date_range("2026-10-01 00:00:00", periods=750, freq="5min", tz="Asia/Kolkata")
    # Strong rise up to bar 680 so 1h EMA20 > EMA50 and ADX >= 20, then slight fade over last ~5h so 1h EMA20 dips vs EMA20[-4],
    # followed by a 15m breakout above prior 32-bar high on the final 15m bar.
    close = np.full(750, 3500.0)
    close[:600] = np.linspace(3400.0, 3620.0, 600)
    close[600:680] = np.linspace(3620.0, 3630.0, 80)
    close[680:747] = np.linspace(3628.0, 3592.0, 67)
    close[744:747] = 3592.0
    close[747:] = [3595.0, 3598.0, 3601.0]
    df = pd.DataFrame(
        {
            "Open": close - 1.5,
            "High": close + 2.5,
            "Low": close - 3.5,
            "Close": close,
            "Volume": np.concatenate([np.full(747, 1000.0), np.full(3, 2500.0)]),
        },
        index=times,
    )
    # Keep 32-bar prior high slightly below 3601 during final pullback
    df.iloc[650:747, df.columns.get_loc("High")] = df.iloc[650:747]["Close"] + 1.0
    sig = evaluate_htf_swing_breakout(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "HTF_1H_EMA_NOT_RISING"


def test_htf_swing_breakout_rejects_misaligned_15m_ema():
    cfg = ORBSettings(strategy_type="HTF_SWING_BREAKOUT")
    times = pd.date_range("2026-10-01 00:00:00", periods=750, freq="5min", tz="Asia/Kolkata")
    # 1h trend clearly bullish and rising, but 15m dipped enough for 15m EMA20 < EMA50 before popping slightly above flat 32-bar high
    close = np.linspace(3400.0, 3650.0, 750)
    # Dip over bars 650..746 (~8h -> 32 15m bars) then pop above prior 32-bar high? Wait: flat range after quick dip
    close[650:710] = np.linspace(3640.0, 3590.0, 60)
    close[710:747] = 3592.0
    close[747:] = [3596.0, 3599.0, 3602.0]
    df = pd.DataFrame(
        {
            "Open": close - 1.0,
            "High": close + 1.5,
            "Low": close - 2.0,
            "Close": close,
            "Volume": np.concatenate([np.full(747, 1000.0), np.full(3, 2500.0)]),
        },
        index=times,
    )
    # Cap bars 651..746 high so 32-bar prior high is ~3594
    df.iloc[651:747, df.columns.get_loc("High")] = 3594.0
    sig = evaluate_htf_swing_breakout(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "LTF_15M_TREND_MISALIGNED"


def test_htf_swing_breakout_anti_chase_filters():
    cfg = ORBSettings(strategy_type="HTF_SWING_BREAKOUT")

    # 1) Reject oversized breakout spike (> 1.50 ATR past 32-bar high)
    df_spike = _make_trending_5m_df(750, "LONG")
    df_spike.iloc[-3:, df_spike.columns.get_loc("Close")] = [3568.0, 3578.0, 3590.0]
    df_spike.iloc[-3:, df_spike.columns.get_loc("High")] = [3570.0, 3580.0, 3591.0]
    df_spike.iloc[-3:, df_spike.columns.get_loc("Open")] = [3560.0, 3568.0, 3578.0]
    sig_spike = evaluate_htf_swing_breakout(df_spike, cfg)
    assert sig_spike.direction == "NO TRADE"
    assert sig_spike.diagnostic_code == "LATE_BREAKOUT_EXTENSION"

    # 2) Reject over-extended EMA distance (> 3.50 ATR above 15m EMA20) while breakout_ext <= 1.50 ATR
    df_ema_ext = _make_trending_5m_df(750, "LONG")
    # Stair-step prior highs smoothly upward so breakout_ext is small (~0.6 ATR) while close is > 3.5 ATR above 15m EMA20
    df_ema_ext.iloc[-60:-3, df_ema_ext.columns.get_loc("Close")] = np.linspace(3550.0, 3582.0, 57)
    df_ema_ext.iloc[-60:-3, df_ema_ext.columns.get_loc("Open")] = df_ema_ext.iloc[-60:-3]["Close"] - 0.5
    df_ema_ext.iloc[-60:-3, df_ema_ext.columns.get_loc("High")] = df_ema_ext.iloc[-60:-3]["Close"] + 0.8
    df_ema_ext.iloc[-60:-3, df_ema_ext.columns.get_loc("Low")] = df_ema_ext.iloc[-60:-3]["Close"] - 0.8
    df_ema_ext.iloc[-3:, df_ema_ext.columns.get_loc("Open")] = [3582.5, 3583.5, 3584.5]
    df_ema_ext.iloc[-3:, df_ema_ext.columns.get_loc("Close")] = [3583.5, 3584.5, 3585.5]
    df_ema_ext.iloc[-3:, df_ema_ext.columns.get_loc("High")] = [3584.0, 3585.0, 3586.0]
    df_ema_ext.iloc[-3:, df_ema_ext.columns.get_loc("Low")] = [3582.0, 3583.0, 3584.0]
    sig_ema = evaluate_htf_swing_breakout(df_ema_ext, cfg)
    assert sig_ema.direction == "NO TRADE"
    assert sig_ema.diagnostic_code == "LATE_EMA_EXTENSION"


def test_htf_swing_breakout_no_lookahead_and_backtest_live_parity():
    from backtest.orb_backtest import run_backtest

    cfg = ORBSettings(
        strategy_type="HTF_SWING_BREAKOUT",
        mode="ALERT_ONLY",
        risk_pct=1.0,
        leverage=10.0,
        max_margin_pct_equity=30.0,
        session_start="00:00",
        session_end="23:59",
        last_entry_time="23:59",
        no_entry_minutes_after_open=0,
    )
    assert cfg.close_at_eod is False

    df_base = _make_trending_5m_df(750, "LONG")
    # Append 125 calm bars spanning past midnight (2026-10-03 14:25 -> 2026-10-04 00:50)
    extra_idx = pd.date_range(df_base.index[-1] + pd.Timedelta(minutes=5), periods=125, freq="5min", tz="Asia/Kolkata")
    df_extra = pd.DataFrame(
        {
            "Open": np.full(125, 3556.0),
            "High": np.full(125, 3558.0),
            "Low": np.full(125, 3554.0),
            "Close": np.full(125, 3556.5),
            "Volume": np.full(125, 1000.0),
        },
        index=extra_idx,
    )
    df_full = pd.concat([df_base, df_extra])

    # 1) No-lookahead invariance: future bars after 750 cannot change signal evaluated on prefix[:750]
    sig_prefix = evaluate_htf_swing_breakout(df_base, cfg)
    assert sig_prefix.direction == "LONG"
    df_perturbed = df_full.copy()
    df_perturbed.iloc[750:, df_perturbed.columns.get_loc("Close")] = 9999.0
    sig_from_perturbed = evaluate_htf_swing_breakout(df_perturbed.iloc[:750], cfg)
    assert sig_prefix == sig_from_perturbed

    # 2) Backtest/live parity + 24/7 overnight hold (close_at_eod=False preserves open swing across midnight)
    res = run_backtest(df_full, initial_equity=10000.0, cfg=cfg, chart_output_path=None, strategy_fn=evaluate_htf_swing_breakout)
    assert res.trades == 0
    assert len(res.open_positions) == 1
    pos = res.open_positions[0]
    entry_ts = pd.Timestamp(pos["time"])
    sig_live_at_entry = evaluate_htf_swing_breakout(df_full.loc[:entry_ts], cfg)
    assert sig_live_at_entry.direction == pos["direction"]
    assert sig_live_at_entry.entry == pos["entry"]
    assert sig_live_at_entry.sl == pos["sl"]
    assert sig_live_at_entry.tp == pos["tp"]
