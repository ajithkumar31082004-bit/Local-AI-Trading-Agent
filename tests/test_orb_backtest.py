"""Unit tests for chronological backtest replay, metrics, and conservative collision handling."""
from pathlib import Path
import pandas as pd
import pytest
from backtest.orb_backtest import run_backtest
from config.orb_settings import ORBSettings
from services.orb_backtest import backtest_orb


def test_backtest_empty_rejected():
    with pytest.raises(ValueError, match="No candles"):
        run_backtest(pd.DataFrame())


def test_skipped_day_counted_as_zero_trade_day(tmp_path):
    # Flat candles where price stays inside range: zero trades, 1 day, 1 skipped day
    idx = pd.date_range("2026-01-01 09:00", periods=40, freq="5min", tz="Asia/Kolkata")
    frame = pd.DataFrame({
        "Open": [100.0] * 40,
        "High": [100.2] * 40,
        "Low": [99.8] * 40,
        "Close": [100.0] * 40,
        "Volume": [100.0] * 40,
    }, index=idx)
    chart_path = tmp_path / "chart.html"
    result = run_backtest(frame, initial_equity=10000.0, chart_output_path=chart_path)
    assert result.days == 1
    assert result.skipped_days == 1
    assert result.trades == 0
    assert result.total_r == 0.0
    assert chart_path.exists(), "Equity curve HTML must be generated"


def test_conservative_same_candle_sl_tp_collision():
    """Verify that if a simulated trade candle touches both TP and SL, stop loss is chosen conservatively."""
    idx = pd.date_range("2026-01-02 07:15", periods=27, freq="5min", tz="Asia/Kolkata")
    closes = [2590.0 + i * 0.5 for i in range(21)] + [2604.0, 2603.0, 2605.0, 2613.0, 2613.0, 2613.0]
    opens = [c - 0.2 for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    # Opening range (bars 21, 22, 23): ORB High = 2610, ORB Low = 2600
    highs[21], highs[22], highs[23] = 2610.0, 2608.0, 2607.0
    lows[21], lows[22], lows[23] = 2602.0, 2601.0, 2600.0

    # Breakout bar (bar 24 at 09:15)
    vols = [100.0] * 24 + [500.0, 150.0, 100.0]
    highs[24] = 2614.0
    lows[24] = 2608.0

    # Retest & rejection bar (bar 25 at 09:20): entry at 2613.0, SL ~ 2610.15, TP ~ 2623.8
    opens[25] = 2611.2
    lows[25] = 2610.2
    closes[25] = 2613.0
    highs[25] = 2614.0

    # Subsequent bar 26: extreme expansion touches both TP (>2624) and SL (<2610)
    highs[26] = 2650.0
    lows[26] = 2600.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    cfg = ORBSettings(rejection_wick_body_ratio=0.5, retest_tolerance=0.5, max_extension_atr=5.0, session_start="09:00", orb_minutes=15)
    result = run_backtest(df, initial_equity=10000.0, cfg=cfg, chart_output_path=None)

    assert result.trades == 1
    trade = result.trade_log[0]
    assert trade["exit_reason"] == "SL", "When both TP and SL are hit in the same bar, conservative rule must trigger SL"
    assert trade["r"] < 0, "Stop loss must result in negative R"


def test_end_of_day_position_close():
    """Verify that an open position at the end of the day is closed at the final bar's close price."""
    idx = pd.date_range("2026-01-02 07:15", periods=27, freq="5min", tz="Asia/Kolkata")
    closes = [2590.0 + i * 0.5 for i in range(21)] + [2604.0, 2603.0, 2605.0, 2613.0, 2613.0, 2618.0]
    opens = [c - 0.2 for c in closes]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    highs[21], highs[22], highs[23] = 2610.0, 2608.0, 2607.0
    lows[21], lows[22], lows[23] = 2602.0, 2601.0, 2600.0

    # Breakout bar
    vols = [100.0] * 24 + [500.0, 150.0, 100.0]
    highs[24] = 2614.0
    lows[24] = 2608.0

    # Retest & rejection bar (bar 25 at 09:20)
    opens[25] = 2611.2
    lows[25] = 2610.2
    closes[25] = 2613.0
    highs[25] = 2614.0

    # Final bar 26: closes at 2618.0 without hitting SL or TP
    opens[26] = 2613.0
    highs[26] = 2620.0  # Does not reach TP
    lows[26] = 2611.0   # Does not touch SL
    closes[26] = 2618.0

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
    cfg = ORBSettings(rejection_wick_body_ratio=0.5, retest_tolerance=0.5, max_extension_atr=5.0, session_start="09:00", orb_minutes=15)
    result = run_backtest(df, initial_equity=10000.0, cfg=cfg, chart_output_path=None)
    assert result.trades == 1
    trade = result.trade_log[0]
    assert trade["exit_reason"] == "END_OF_DAY"
    assert trade["r"] > 0, "Trade closed at 2618 above entry 2613 should yield positive net R"


def test_services_orb_backtest_alias_and_metrics():
    idx = pd.date_range("2026-01-01 09:00", periods=20, freq="5min", tz="Asia/Kolkata")
    frame = pd.DataFrame({
        "Open": [100.0] * 20,
        "High": [100.2] * 20,
        "Low": [99.8] * 20,
        "Close": [100.0] * 20,
        "Volume": [100.0] * 20,
    }, index=idx)
    res = backtest_orb(frame, initial_equity=1000.0, chart_output_path=None)
    assert res.days == 1
    assert res.trades == 0
    assert res.no_setup_pct == 100.0
    assert res.max_drawdown_r == 0.0


def test_walk_forward_runner_smoke(tmp_path):
    from scripts.run_backtest import run_walk_forward
    # Create 4 distinct days of flat data
    dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    dfs = []
    for d in dates:
        idx = pd.date_range(f"{d} 09:00", periods=10, freq="5min", tz="Asia/Kolkata")
        dfs.append(pd.DataFrame({
            "Open": [100.0] * 10,
            "High": [100.2] * 10,
            "Low": [99.8] * 10,
            "Close": [100.0] * 10,
            "Volume": [100.0] * 10,
        }, index=idx))
    combined = pd.concat(dfs)
    cfg = ORBSettings(session_start="09:00", orb_minutes=15)
    run_walk_forward(combined, initial_equity=1000.0, cfg=cfg, out_dir=tmp_path)
    assert (tmp_path / "is_equity_curve.html").exists()
    assert (tmp_path / "oos_equity_curve.html").exists()
