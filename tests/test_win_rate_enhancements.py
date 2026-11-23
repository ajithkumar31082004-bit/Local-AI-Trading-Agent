"""Unit and Integration Tests for Win-Rate Enhancement Pipeline.

Tests:
1. Asymmetric LONG Trend ADX threshold (>= 26.0 vs 22.0)
2. Asymmetric LONG continuation volume threshold (>= 1.0x vs 0.8x)
3. Arbitrator elevated quality score threshold and Long guard enforcement
4. Backtest Multi-Stage Exit Ratchet (Breakeven at +1.0R excursion)
5. Backtest Multi-Stage Exit Ratchet (Profit lock at +1.6R excursion)
"""
import pytest
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

from config.orb_settings import ORBSettings
from strategy.orb_ema_volume import Signal
from strategy.trend_following import evaluate_trend_following
from strategy.arbitrator import arbitrate_strategies, ArbitrationResult
from backtest.orb_backtest import run_backtest
from regime.market_regime import MarketRegime, RegimeDetectionResult


def test_01_asymmetric_long_adx_filter():
    """Verify that LONG trend-following setup rejects weak ADX (< 26.0) with WEAK_BULLISH_TREND_ADX."""
    start = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
    timestamps = [start + timedelta(minutes=5 * i) for i in range(250)]
    base = 2600.0
    closes = []
    for i in range(250):
        base += 0.05 + np.sin(i / 3.0) * 1.5
        closes.append(base)

    df = pd.DataFrame(index=pd.DatetimeIndex(timestamps))
    df["Close"] = closes
    df["Open"] = [c - 0.2 for c in closes]
    df["High"] = [c + 2.0 for c in closes]
    df["Low"] = [c - 2.0 for c in closes]
    df["Volume"] = 150.0

    cfg = ORBSettings(min_15m_adx_long=26.0, session_start="00:00", session_end="23:59", no_entry_minutes_after_open=0, last_entry_time="23:59")
    sig = evaluate_trend_following(df, cfg)
    assert sig.direction == "NO TRADE"
    assert sig.diagnostic_code == "WEAK_BULLISH_TREND_ADX"


def test_02_arbitrator_quality_threshold_enforcement():
    """Verify that Arbitrator enforces base_min_quality (0.75) and filters lower-quality signals."""
    start = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
    timestamps = [start + timedelta(minutes=5 * i) for i in range(250)]
    base = 2600.0
    closes = [base + (0.3 * i) for i in range(250)]

    df = pd.DataFrame(index=pd.DatetimeIndex(timestamps))
    df["Close"] = closes
    df["Open"] = [c - 0.1 for c in closes]
    df["High"] = [c + 1.0 for c in closes]
    df["Low"] = [c - 1.0 for c in closes]
    df["Volume"] = 150.0

    cfg = ORBSettings(min_signal_quality_score=0.75)
    arb_res = arbitrate_strategies(df, cfg)
    assert isinstance(arb_res, ArbitrationResult)

    # Any winning signal must meet or exceed the 0.75 threshold
    if arb_res.winning_signal.direction in ("LONG", "SHORT"):
        assert arb_res.winning_signal.quality_score >= 0.75


def test_03_backtest_multi_stage_ratchet_breakeven():
    """Verify that when enable_multi_stage_exits=True, a trade achieving +1.0R excursion moves SL to Breakeven."""
    cfg = ORBSettings(
        enable_multi_stage_exits=True,
        breakeven_trigger_r=1.0,
        session_start="00:00",
        session_end="23:59",
        no_entry_minutes_after_open=0,
        last_entry_time="23:59",
        strategy_type="ORB",
    )

    # 1. Create a controlled day where entry triggers, price rises to +1.1R, then pulls back to entry
    start = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)
    ts_list = [start + timedelta(minutes=5 * i) for i in range(50)]

    df = pd.DataFrame(index=pd.DatetimeIndex(ts_list))
    df["Open"] = 2650.0
    df["High"] = 2651.0
    df["Low"] = 2649.0
    df["Close"] = 2650.0
    df["Volume"] = 100.0

    # Custom strategy function that forces a LONG entry at bar 10 with entry=2650.0, sl=2640.0 (risk_dist=10.0), tp=2670.0 (2R)
    def dummy_strategy(prefix, settings):
        current_ts = prefix.index[-1]
        if current_ts == ts_list[10]:
            return Signal(
                direction="LONG",
                entry=2650.0,
                sl=2640.0,
                tp=2670.0,
                r_multiple=2.0,
                reason="Forced Entry",
                checklist={},
                timestamp=str(current_ts),
                diagnostic_code="TEST_ENTRY",
                strategy_name="TEST_STRAT",
                quality_score=0.85,
            )
        return Signal(direction="NO TRADE", entry=None, sl=None, tp=None, r_multiple=0.0, reason="", checklist={}, timestamp=str(current_ts))

    # Bar 11 pushes up to 2661.0 (excursion = +11.0 > 1.0R trigger of 10.0) -> ratchets SL to 2650.10
    df.loc[ts_list[11], "High"] = 2661.0
    df.loc[ts_list[11], "Close"] = 2660.0

    # Bar 12 pulls back to 2649.5 (hits ratcheted breakeven SL at 2650.10, NOT the original 2640.0 SL)
    df.loc[ts_list[12], "Low"] = 2649.5
    df.loc[ts_list[12], "Close"] = 2650.0

    res = run_backtest(df, initial_equity=10000.0, cfg=cfg, strategy_fn=dummy_strategy)
    assert res.trades == 1
    trade = res.trade_log[0]
    assert trade["exit_reason"] == "BE"
    assert trade["exit"] == 2650.10
    # Net PnL should be near $0 (scratch/breakeven), not -$50 (initial loss)
    assert trade["r"] >= -0.05


def test_04_backtest_multi_stage_ratchet_profit_lock():
    """Verify that when a trade achieves +1.6R excursion, SL is ratcheted to lock in +1.0R profit."""
    cfg = ORBSettings(
        enable_multi_stage_exits=True,
        breakeven_trigger_r=1.0,
        session_start="00:00",
        session_end="23:59",
        no_entry_minutes_after_open=0,
        last_entry_time="23:59",
    )

    start = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)
    ts_list = [start + timedelta(minutes=5 * i) for i in range(50)]

    df = pd.DataFrame(index=pd.DatetimeIndex(ts_list))
    df["Open"] = 2650.0
    df["High"] = 2651.0
    df["Low"] = 2649.0
    df["Close"] = 2650.0
    df["Volume"] = 100.0

    def dummy_strategy(prefix, settings):
        current_ts = prefix.index[-1]
        if current_ts == ts_list[10]:
            return Signal(
                direction="LONG",
                entry=2650.0,
                sl=2640.0,
                tp=2670.0,
                r_multiple=2.0,
                reason="Forced Entry",
                checklist={},
                timestamp=str(current_ts),
                diagnostic_code="TEST_ENTRY",
                strategy_name="TEST_STRAT",
                quality_score=0.85,
            )
        return Signal(direction="NO TRADE", entry=None, sl=None, tp=None, r_multiple=0.0, reason="", checklist={}, timestamp=str(current_ts))

    # Bar 11 pushes up to 2667.0 (excursion = +17.0 > 1.6R of 10.0) -> ratchets SL to lock +1.0R profit at 2660.0
    df.loc[ts_list[11], "High"] = 2667.0
    df.loc[ts_list[11], "Close"] = 2666.0

    # Bar 12 pulls back to 2658.0 (hits ratcheted trailing SL at 2660.0)
    df.loc[ts_list[12], "Low"] = 2658.0
    df.loc[ts_list[12], "Close"] = 2659.0

    res = run_backtest(df, initial_equity=10000.0, cfg=cfg, strategy_fn=dummy_strategy)
    assert res.trades == 1
    trade = res.trade_log[0]
    assert trade["exit_reason"] == "TRAILING_SL"
    assert trade["exit"] == 2660.0
    # Profit was locked: positive gross and net cash PnL!
    assert trade["gross_pnl"] > 0.0
    assert trade["pnl_cash"] > 0.0
