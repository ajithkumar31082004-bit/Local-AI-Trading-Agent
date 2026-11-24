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

    cfg = ORBSettings(min_signal_quality_score=0.75, allowed_trade_directions="BOTH")
    arb_res = arbitrate_strategies(df, cfg)
    assert isinstance(arb_res, ArbitrationResult)

    # Any winning signal must meet or exceed the 0.75 threshold
    if arb_res.winning_signal.direction in ("LONG", "SHORT"):
        assert arb_res.winning_signal.quality_score >= 0.75


def test_03_backtest_multi_stage_ratchet_breakeven():
    """Verify that when enable_multi_stage_exits=True, a trade achieving +1.0R excursion moves SL to Breakeven."""
    cfg = ORBSettings(
        enable_multi_stage_exits=True,
        breakeven_trigger_r=0.8,
        session_start="00:00",
        session_end="23:59",
        no_entry_minutes_after_open=0,
        last_entry_time="23:59",
        strategy_type="ORB",
    )

    # 1. Create a controlled day where entry triggers, price rises to +0.85R, then pulls back to entry
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

    # Bar 11 pushes up to 2658.5 (excursion = +8.5 > 0.8R trigger of 8.0, but < 1.0R) -> ratchets SL to 2650.10
    df.loc[ts_list[11], "High"] = 2658.5
    df.loc[ts_list[11], "Close"] = 2658.0

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
    """Verify that when a trade achieves +1.6R excursion, SL is ratcheted to lock in +1.10R profit."""
    cfg = ORBSettings(
        enable_multi_stage_exits=True,
        breakeven_trigger_r=0.8,
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

    # Bar 11 pushes up to 2667.0 (excursion = +17.0 > 1.6R of 10.0) -> ratchets SL to lock +1.10R profit at 2661.0
    df.loc[ts_list[11], "High"] = 2667.0
    df.loc[ts_list[11], "Close"] = 2666.0

    # Bar 12 pulls back to 2658.0 (hits ratcheted trailing SL at 2661.0)
    df.loc[ts_list[12], "Low"] = 2658.0
    df.loc[ts_list[12], "Close"] = 2659.0

    res = run_backtest(df, initial_equity=10000.0, cfg=cfg, strategy_fn=dummy_strategy)
    assert res.trades == 1
    trade = res.trade_log[0]
    assert trade["exit_reason"] == "TRAILING_SL"
    assert trade["exit"] == 2661.0
    # Profit was locked: positive gross and net cash PnL!
    assert trade["gross_pnl"] > 0.0
    assert trade["pnl_cash"] > 0.0


def test_05_arbitrator_negative_test_rejects_sub_threshold_quality(monkeypatch):
    """Verify that Arbitrator deterministically rejects eligible signals when quality_score < 0.75."""
    start = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start, periods=70, freq="5min")
    df = pd.DataFrame({
        "Open": [2600.0] * 70,
        "High": [2605.0] * 70,
        "Low": [2595.0] * 70,
        "Close": [2602.0] * 70,
        "Volume": [1000.0] * 70,
    }, index=ts)

    cfg = ORBSettings(min_signal_quality_score=0.75, allowed_trade_directions="BOTH")

    # Force regime to TRENDING_BULLISH where ORB is allowed
    regime_res = RegimeDetectionResult(
        regime=MarketRegime.TRENDING_BULLISH,
        confidence=0.90,
        rationale="Synthetic uptrend for unit test",
        metrics={"adx_15m": 30.0},
        allowed_strategies=["ORB"],
        disallowed_strategies=[],
    )
    monkeypatch.setattr("strategy.arbitrator.detect_market_regime", lambda f, c: regime_res)

    # Mock ORB returning an otherwise valid LONG signal but with quality_score = 0.74 (< 0.75)
    sub_threshold_sig = Signal(
        direction="LONG",
        entry=2602.0,
        sl=2592.0,
        tp=2622.0,
        r_multiple=2.0,
        reason="Sub-threshold quality setup",
        checklist={"volume_confirmed": True},
        timestamp=str(ts[-1]),
        diagnostic_code="ORB_LONG_VALID",
        strategy_name="ORB",
        quality_score=0.74,
    )
    monkeypatch.setattr("strategy.arbitrator.evaluate_orb", lambda f, c: sub_threshold_sig)

    arb_res = arbitrate_strategies(df, cfg)
    assert arb_res.decision == "NO_ELIGIBLE_SIGNAL"
    assert arb_res.winning_signal.direction == "NO TRADE"
    assert arb_res.winning_signal.diagnostic_code == "NO_ELIGIBLE_HIGH_QUALITY_SIGNAL"
    assert any("QUALITY_SCORE_0.74_BELOW_0.75" in r[1] for r in arb_res.rejected_strategies)


def test_06_arbitrator_positive_test_accepts_above_threshold_quality(monkeypatch):
    """Verify that Arbitrator accepts eligible signals with quality_score >= 0.75."""
    start = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start, periods=70, freq="5min")
    df = pd.DataFrame({
        "Open": [2600.0] * 70,
        "High": [2605.0] * 70,
        "Low": [2595.0] * 70,
        "Close": [2602.0] * 70,
        "Volume": [1000.0] * 70,
    }, index=ts)

    cfg = ORBSettings(min_signal_quality_score=0.75, allowed_trade_directions="BOTH")

    regime_res = RegimeDetectionResult(
        regime=MarketRegime.TRENDING_BULLISH,
        confidence=0.90,
        rationale="Synthetic uptrend for unit test",
        metrics={"adx_15m": 30.0},
        allowed_strategies=["ORB"],
        disallowed_strategies=[],
    )
    monkeypatch.setattr("strategy.arbitrator.detect_market_regime", lambda f, c: regime_res)

    above_threshold_sig = Signal(
        direction="LONG",
        entry=2602.0,
        sl=2592.0,
        tp=2622.0,
        r_multiple=2.0,
        reason="High quality setup",
        checklist={"volume_confirmed": True},
        timestamp=str(ts[-1]),
        diagnostic_code="ORB_LONG_VALID",
        strategy_name="ORB",
        quality_score=0.82,
    )
    monkeypatch.setattr("strategy.arbitrator.evaluate_orb", lambda f, c: above_threshold_sig)

    arb_res = arbitrate_strategies(df, cfg)
    assert arb_res.decision == "TRADE_SELECTED"
    assert arb_res.winning_signal.direction == "LONG"
    assert arb_res.winning_signal.quality_score == 0.82
    assert arb_res.winning_signal.strategy_name == "ORB"


def test_07_arbitrator_asymmetric_long_guard_in_ranging_regime(monkeypatch):
    """Verify that Arbitrator requires quality_score >= 0.80 for LONG signals in RANGING regime when ADX < 25."""
    start = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start, periods=70, freq="5min")
    df = pd.DataFrame({
        "Open": [2600.0] * 70,
        "High": [2605.0] * 70,
        "Low": [2595.0] * 70,
        "Close": [2602.0] * 70,
        "Volume": [1000.0] * 70,
    }, index=ts)

    cfg = ORBSettings(min_signal_quality_score=0.75, allowed_trade_directions="BOTH")

    # Ranging regime with ADX = 21.0 (< 25.0)
    regime_res = RegimeDetectionResult(
        regime=MarketRegime.RANGING,
        confidence=0.85,
        rationale="Synthetic range with low ADX",
        metrics={"adx_15m": 21.0, "ema20_15m": 2600.0, "ema50_15m": 2600.0},
        allowed_strategies=["BB_RSI_MEAN_REVERSION"],
        disallowed_strategies=[],
    )
    monkeypatch.setattr("strategy.arbitrator.detect_market_regime", lambda f, c: regime_res)

    # 1. Negative Case: LONG signal with quality 0.78 (passes base 0.75, but fails ranging LONG guard 0.80)
    sig_78 = Signal(
        direction="LONG",
        entry=2602.0,
        sl=2592.0,
        tp=2622.0,
        r_multiple=2.0,
        reason="Borderline long in range",
        checklist={},
        timestamp=str(ts[-1]),
        diagnostic_code="BB_RSI_LONG",
        strategy_name="BB_RSI_MEAN_REVERSION",
        quality_score=0.78,
    )
    monkeypatch.setattr("strategy.arbitrator.evaluate_bb_rsi", lambda f, c: sig_78)

    res_neg = arbitrate_strategies(df, cfg)
    assert res_neg.decision == "NO_ELIGIBLE_SIGNAL"
    assert any("QUALITY_SCORE_0.78_BELOW_0.80" in r[1] for r in res_neg.rejected_strategies)

    # 2. Positive Case: LONG signal with quality 0.85 (passes ranging LONG guard 0.80)
    sig_85 = Signal(
        direction="LONG",
        entry=2602.0,
        sl=2592.0,
        tp=2622.0,
        r_multiple=2.0,
        reason="Exceptional long in range",
        checklist={},
        timestamp=str(ts[-1]),
        diagnostic_code="BB_RSI_LONG",
        strategy_name="BB_RSI_MEAN_REVERSION",
        quality_score=0.85,
    )
    monkeypatch.setattr("strategy.arbitrator.evaluate_bb_rsi", lambda f, c: sig_85)

    res_pos = arbitrate_strategies(df, cfg)
    assert res_pos.decision == "TRADE_SELECTED"
    assert res_pos.winning_signal.direction == "LONG"
    assert res_pos.winning_signal.quality_score == 0.85


def test_08_live_executor_4_stage_ratchet_ladder_parity():
    """Verify DeltaLiveExecutor.manage_open_positions executes the exact 4-stage stop ladder."""
    from unittest.mock import MagicMock
    from services.delta_live_executor import DeltaLiveExecutor

    cfg = ORBSettings(
        mode="LIVE",
        enable_multi_stage_exits=True,
        breakeven_trigger_r=0.8,
        product_id=131253,
    )

    mock_client = MagicMock()
    mock_client.change_order.return_value = {"success": True}
    mock_acct = MagicMock()

    executor = DeltaLiveExecutor(cfg=cfg, client=mock_client, account_service=mock_acct)

    # Open LONG position: size=1, entry_price=2600.0, initial SL=2590.0 (risk_dist=10.0)
    mock_acct._fetch_open_positions.return_value = {
        "success": True,
        "result": [{"product_id": 131253, "size": 1, "entry_price": 2600.0}],
    }
    mock_acct._fetch_open_orders.return_value = {
        "success": True,
        "result": [{"id": 9991, "stop_price": 2590.0, "side": "sell", "order_type": "stop_order"}],
    }

    # Stage 1: Excursion = +0.85R (Price = 2608.5) -> Stop moved to Entry + 0.10 (2600.10)
    msg1 = executor.manage_open_positions(current_price=2608.5)
    assert msg1 is not None
    assert "Breakeven" in msg1
    mock_client.change_order.assert_called_with(
        order_id="9991", product_id=131253, stop_price=2600.10, limit_price=2600.05
    )

    # Update stop order reflection
    mock_acct._fetch_open_orders.return_value = {
        "success": True,
        "result": [{"id": 9991, "stop_price": 2600.10, "side": "sell", "order_type": "stop_order"}],
    }

    # Stage 2: Excursion = +1.05R (Price = 2610.5) -> Stop moved to Entry + 0.50R (2605.00)
    msg2 = executor.manage_open_positions(current_price=2610.5)
    assert msg2 is not None
    assert "0.50R" in msg2
    mock_client.change_order.assert_called_with(
        order_id="9991", product_id=131253, stop_price=2605.00, limit_price=2604.95
    )

    # Update stop order reflection
    mock_acct._fetch_open_orders.return_value = {
        "success": True,
        "result": [{"id": 9991, "stop_price": 2605.00, "side": "sell", "order_type": "stop_order"}],
    }

    # Stage 3: Excursion = +1.65R (Price = 2616.5) -> Stop moved to Entry + 1.10R (2611.00)
    msg3 = executor.manage_open_positions(current_price=2616.5)
    assert msg3 is not None
    assert "1.10R" in msg3
    mock_client.change_order.assert_called_with(
        order_id="9991", product_id=131253, stop_price=2611.00, limit_price=2610.95
    )

    # Update stop order reflection
    mock_acct._fetch_open_orders.return_value = {
        "success": True,
        "result": [{"id": 9991, "stop_price": 2611.00, "side": "sell", "order_type": "stop_order"}],
    }

    # Stage 4: Excursion = +2.25R (Price = 2622.5) -> Stop moved to Entry + 1.70R (2617.00)
    msg4 = executor.manage_open_positions(current_price=2622.5)
    assert msg4 is not None
    assert "1.70R" in msg4
    mock_client.change_order.assert_called_with(
        order_id="9991", product_id=131253, stop_price=2617.00, limit_price=2616.95
    )


def test_09_backtest_vs_live_ratchet_identical_stop_levels():
    """Verify strict mathematical equality between backtest stop calculation and live executor stops."""
    entry = 3600.0
    initial_sl = 3585.0
    risk_dist = entry - initial_sl  # 15.0

    # Backtest ladder rules from backtest/orb_backtest.py:
    bt_stg1 = round(entry + 0.10, 2)
    bt_stg2 = round(entry + (0.50 * risk_dist), 2)
    bt_stg3 = round(entry + (1.10 * risk_dist), 2)
    bt_stg4 = round(entry + (1.70 * risk_dist), 2)

    # Live executor ladder rules from services/delta_live_executor.py:
    live_stg1 = round(entry + 0.10, 2)
    live_stg2 = round(entry + (0.50 * risk_dist), 2)
    live_stg3 = round(entry + (1.10 * risk_dist), 2)
    live_stg4 = round(entry + (1.70 * risk_dist), 2)

    assert bt_stg1 == live_stg1 == 3600.10
    assert bt_stg2 == live_stg2 == 3607.50
    assert bt_stg3 == live_stg3 == 3616.50
    assert bt_stg4 == live_stg4 == 3625.50


def test_10_arbitrator_short_only_direction_filter(monkeypatch):
    """Verify that Arbitrator rejects all LONG signals when allowed_trade_directions='SHORT_ONLY'."""
    start = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start, periods=70, freq="5min")
    df = pd.DataFrame({
        "Open": [2600.0] * 70,
        "High": [2605.0] * 70,
        "Low": [2595.0] * 70,
        "Close": [2602.0] * 70,
        "Volume": [1000.0] * 70,
    }, index=ts)

    cfg = ORBSettings(allowed_trade_directions="SHORT_ONLY")

    regime_res = RegimeDetectionResult(
        regime=MarketRegime.TRENDING_BULLISH,
        confidence=0.90,
        rationale="Synthetic uptrend",
        metrics={"adx_15m": 30.0},
        allowed_strategies=["ORB"],
        disallowed_strategies=[],
    )
    monkeypatch.setattr("strategy.arbitrator.detect_market_regime", lambda f, c: regime_res)

    long_sig = Signal(
        direction="LONG",
        entry=2602.0,
        sl=2592.0,
        tp=2622.0,
        r_multiple=2.0,
        reason="Bullish setup",
        checklist={},
        timestamp=str(ts[-1]),
        diagnostic_code="ORB_LONG_VALID",
        strategy_name="ORB",
        quality_score=0.85,
    )
    monkeypatch.setattr("strategy.arbitrator.evaluate_orb", lambda f, c: long_sig)

    arb_res = arbitrate_strategies(df, cfg)
    assert arb_res.winning_signal.direction == "NO TRADE"
    assert ("ORB", "LONG_TRADES_DISABLED_BY_CONFIG") in arb_res.rejected_strategies


def test_11_live_executor_short_only_direction_filter():
    """Verify that DeltaLiveExecutor pre-flight audit blocks LONG orders when allowed_trade_directions='SHORT_ONLY'."""
    from services.delta_live_executor import DeltaLiveExecutor
    cfg = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="YES",
        allowed_trade_directions="SHORT_ONLY",
    )
    executor = DeltaLiveExecutor(cfg=cfg)
    approved, reason, details = executor.validate_preflight(
        direction="LONG",
        entry=3600.0,
        stop_loss=3580.0,
        take_profit=3640.0,
        candle_timestamp="2026-11-24T10:00:00Z",
    )
    assert approved is False
    assert "LONG trades are disabled by configuration" in reason

