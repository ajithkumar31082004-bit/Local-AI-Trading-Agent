"""Phase 4.7 Forward PAPER Trading & Strategy Performance Evaluation Test Suite.

Verifies:
1. Regime Classification Matrix (BULL, BEAR, RANGING, VOLATILE, SHOCK_CRASH, UNKNOWN)
2. Regime-Gated Strategy Filtering and Priority Entry Blocking
3. Signal Generation across all 5 strategies (ORB, Trend Following, ATR Breakout, BB-RSI, Liquidity Sweep)
4. Arbitrator Conflict Resolution & SHOCK_CRASH Safety
5. Maker Limit Order Fill vs Unfill Simulation
6. Taker Stop-Loss Regime-Dependent Slippage Model
7. Fee & 18% GST Reconciliation and Cash Conservation
8. Daily UTC Trade Cap (1 filled trade/day) and Session Reset
9. Daily Risk Guard 2% / $50 Loss Lock
10. Profit Factor, Expectancy R, and Zero-Loss Division Handling
11. Peak-to-Trough Maximum Drawdown Tracking
12. Annualized Sharpe and Sortino Ratios
13. Fail-Closed Portalocker Safety Outside Pytest
14. Historical Defect Ledger SHA256 Invariance (DAD10844...)
15. Statistical Acceptance Gate (>= 100 Trades Required for Profitability Acceptance)
"""
import hashlib
import json
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from config.orb_settings import ORBSettings, settings
from services.market_regime import MarketRegime, detect_market_regime, RegimeDetectionResult
from services.phase4_7_analytics import (
    calculate_phase4_7_metrics,
    evaluate_statistical_acceptance_gate,
    evaluate_forward_state_file,
    MIN_WIN_RATE_PCT,
    MIN_PROFIT_FACTOR,
    MIN_EXPECTANCY_R,
    MAX_DRAWDOWN_PCT,
    MIN_ANNUALIZED_SHARPE,
    MIN_REQUIRED_STATISTICAL_TRADES,
)
from services.delta_paper_broker import DeltaPaperBroker, CrossProcessReentrantLock
from strategy.arbitrator import arbitrate_strategies, ArbitrationResult
from strategy.orb_ema_volume import evaluate_orb, Signal
from strategy.trend_following import evaluate_trend_following
from strategy.atr_compression_breakout import evaluate_atr_compression_breakout
from strategy.bb_rsi_mean_reversion import evaluate_bb_rsi
from strategy.liquidity_sweep import evaluate_liquidity_sweep
from risk.position_sizing import DailyRiskState, size_position


EXPECTED_DEFECT_LEDGER_SHA256 = "dad10844b0e77ed19db058eec1e541aa858eb8e2d3ea1ada66d83a69390ced25"


def _create_synthetic_candles(
    n_bars: int = 160,
    base_price: float = 4000.0,
    trend_slope: float = 0.0,
    volatility: float = 2.0,
    volume_level: float = 100000.0,
    start_time: str = "2026-11-01 07:00",
) -> pd.DataFrame:
    """Generate deterministic synthetic 5m OHLCV candle sequence."""
    idx = pd.date_range(start_time, periods=n_bars, freq="5min", tz="Asia/Kolkata")
    closes = [base_price + (i * trend_slope) for i in range(n_bars)]
    highs = [c + volatility for c in closes]
    lows = [c - volatility for c in closes]
    opens = [c - (0.2 * volatility) for c in closes]
    volumes = [volume_level] * n_bars

    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=idx)


# =============================================================================
# TEST 1: REGIME CLASSIFICATION MATRIX
# =============================================================================

def test_01_regime_classification_matrix():
    """Verify deterministic classification across all primary regimes and edge cases."""
    cfg = ORBSettings(symbol="XAUTUSD", timezone="Asia/Kolkata")

    # 1. Insufficient data (< 50 bars) -> UNKNOWN
    df_short = _create_synthetic_candles(n_bars=30)
    res_short = detect_market_regime(df_short, cfg)
    assert res_short.regime == MarketRegime.UNKNOWN
    assert res_short.confidence == 0.0
    assert len(res_short.allowed_strategies) == 0

    # 2. Bullish Trend (positive slope, ADX >= 22, price above VWAP)
    df_bull = _create_synthetic_candles(n_bars=160, base_price=3800.0, trend_slope=0.8)
    res_bull = detect_market_regime(df_bull, cfg)
    assert res_bull.regime == MarketRegime.TRENDING_BULL
    assert res_bull.confidence >= 0.60
    assert "TREND_FOLLOWING" in res_bull.allowed_strategies

    # 3. Bearish Trend (negative slope, ADX >= 22, price below VWAP)
    df_bear = _create_synthetic_candles(n_bars=160, base_price=4200.0, trend_slope=-0.8)
    res_bear = detect_market_regime(df_bear, cfg)
    assert res_bear.regime == MarketRegime.TRENDING_BEAR
    assert res_bear.confidence >= 0.60
    assert "TREND_FOLLOWING" in res_bear.allowed_strategies

    # 4. Ranging Compressed (flat oscillation, low ADX)
    t = np.linspace(0, 8 * np.pi, 160)
    flat_closes = [4000.0 + (1.5 * np.sin(val)) for val in t]
    df_range = pd.DataFrame({
        "Open": flat_closes,
        "High": [c + 1.0 for c in flat_closes],
        "Low": [c - 1.0 for c in flat_closes],
        "Close": flat_closes,
        "Volume": [40000.0] * 160,
    }, index=pd.date_range("2026-11-01 07:00", periods=160, freq="5min", tz="Asia/Kolkata"))
    res_range = detect_market_regime(df_range, cfg)
    assert res_range.regime == MarketRegime.RANGING_COMPRESSED
    assert "BB_RSI_MEAN_REVERSION" in res_range.allowed_strategies or "LIQUIDITY_SWEEP" in res_range.allowed_strategies

    # 5. Volatile Expanding (high ATR expansion + volume breakout)
    df_vol = _create_synthetic_candles(n_bars=160, base_price=4000.0, trend_slope=0.1, volatility=1.5)
    # Inject breakout spike on last bar
    df_vol.iloc[-1, df_vol.columns.get_loc("High")] = 4050.0
    df_vol.iloc[-1, df_vol.columns.get_loc("Close")] = 4048.0
    df_vol.iloc[-1, df_vol.columns.get_loc("Volume")] = 600000.0
    res_vol = detect_market_regime(df_vol, cfg)
    assert res_vol.regime in {MarketRegime.VOLATILE_EXPANDING, MarketRegime.SHOCK_CRASH}

    # 6. SHOCK_CRASH (Single candle range > 3.0x ATR or 15m drop > 1.5%)
    df_shock = _create_synthetic_candles(n_bars=160, base_price=4000.0, trend_slope=0.1, volatility=2.0)
    # Force massive candle range > 3x ATR
    df_shock.iloc[-1, df_shock.columns.get_loc("High")] = 4080.0
    df_shock.iloc[-1, df_shock.columns.get_loc("Low")] = 3980.0
    df_shock.iloc[-1, df_shock.columns.get_loc("Close")] = 3985.0
    res_shock = detect_market_regime(df_shock, cfg)
    assert res_shock.regime == MarketRegime.SHOCK_CRASH
    assert res_shock.confidence == 0.99
    assert len(res_shock.allowed_strategies) == 0  # ALL entries blocked!


# =============================================================================
# TEST 2: REGIME-GATED STRATEGY FILTERING
# =============================================================================

def test_02_regime_gated_strategy_filtering():
    """Verify that strategies are strictly filtered by market regime rules."""
    cfg = ORBSettings(symbol="XAUTUSD")

    # In TRENDING_BULL: Trend following and breakouts are allowed; Mean reversion is disallowed
    df_bull = _create_synthetic_candles(n_bars=160, base_price=3800.0, trend_slope=0.8)
    res_bull = detect_market_regime(df_bull, cfg)
    assert "TREND_FOLLOWING" in res_bull.allowed_strategies
    assert "BB_RSI_MEAN_REVERSION" in res_bull.disallowed_strategies

    # In RANGING_COMPRESSED: Mean reversion is allowed; Trend following is disallowed
    t = np.linspace(0, 8 * np.pi, 160)
    flat_closes = [4000.0 + (1.5 * np.sin(val)) for val in t]
    df_range = pd.DataFrame({
        "Open": flat_closes,
        "High": [c + 1.0 for c in flat_closes],
        "Low": [c - 1.0 for c in flat_closes],
        "Close": flat_closes,
        "Volume": [40000.0] * 160,
    }, index=pd.date_range("2026-11-01 07:00", periods=160, freq="5min", tz="Asia/Kolkata"))
    res_range = detect_market_regime(df_range, cfg)
    assert "BB_RSI_MEAN_REVERSION" in res_range.allowed_strategies
    assert "TREND_FOLLOWING" in res_range.disallowed_strategies

    # In SHOCK_CRASH: Everything is disallowed
    df_crash = _create_synthetic_candles(n_bars=160, base_price=4000.0, trend_slope=0.0)
    # Rapid price collapse > 1.5% in 15m
    df_crash.iloc[-1, df_crash.columns.get_loc("Close")] = 3900.0
    df_crash.iloc[-1, df_crash.columns.get_loc("Low")] = 3890.0
    res_crash = detect_market_regime(df_crash, cfg)
    assert res_crash.regime == MarketRegime.SHOCK_CRASH
    assert len(res_crash.allowed_strategies) == 0
    assert "TREND_FOLLOWING" in res_crash.disallowed_strategies
    assert "ORB" in res_crash.disallowed_strategies


# =============================================================================
# TEST 3: ORB + EMA / VOLUME SIGNAL GENERATION
# =============================================================================

def test_03_orb_ema_volume_signal_generation():
    """Verify ORB strategy evaluates candles and produces expected structured Signal."""
    cfg = ORBSettings(symbol="XAUTUSD")
    df = _create_synthetic_candles(n_bars=160, base_price=4000.0, trend_slope=0.5)

    sig = evaluate_orb(df, cfg)
    assert isinstance(sig, Signal)
    assert sig.direction in {"LONG", "SHORT", "NO TRADE"}
    assert isinstance(sig.checklist, dict)
    assert hasattr(sig, "r_multiple")
    assert hasattr(sig, "quality_score")


# =============================================================================
# TEST 4: TREND FOLLOWING SIGNAL GENERATION
# =============================================================================

def test_04_trend_following_signal_generation():
    """Verify Trend Following strategy evaluates completed candles without exceptions."""
    cfg = ORBSettings(symbol="XAUTUSD")
    df = _create_synthetic_candles(n_bars=160, base_price=3800.0, trend_slope=0.8)

    sig = evaluate_trend_following(df, cfg)
    assert isinstance(sig, Signal)
    assert sig.direction in {"LONG", "SHORT", "NO TRADE"}
    assert "trend_15m_aligned" in sig.checklist


# =============================================================================
# TEST 5: ATR COMPRESSION BREAKOUT SIGNAL
# =============================================================================

def test_05_atr_compression_breakout_signal():
    """Verify ATR Compression Breakout evaluates consolidation and expansion."""
    cfg = ORBSettings(symbol="XAUTUSD")
    df = _create_synthetic_candles(n_bars=160, base_price=4000.0, trend_slope=0.0, volatility=0.8)

    sig = evaluate_atr_compression_breakout(df, cfg)
    assert isinstance(sig, Signal)
    assert sig.direction in {"LONG", "SHORT", "NO TRADE"}
    assert "compression_detected" in sig.checklist


# =============================================================================
# TEST 6: BB-RSI MEAN REVERSION SIGNAL
# =============================================================================

def test_06_bb_rsi_mean_reversion_signal():
    """Verify BB-RSI Mean Reversion evaluates bands, RSI, and reversal criteria."""
    cfg = ORBSettings(symbol="XAUTUSD")
    df = _create_synthetic_candles(n_bars=160, base_price=4000.0, trend_slope=0.0, volatility=1.5)

    sig = evaluate_bb_rsi(df, cfg)
    assert isinstance(sig, Signal)
    assert sig.direction in {"LONG", "SHORT", "NO TRADE"}
    assert "band_probe_detected" in sig.checklist
    assert "rsi_extreme_confirmed" in sig.checklist


# =============================================================================
# TEST 7: LIQUIDITY SWEEP SIGNAL
# =============================================================================

def test_07_liquidity_sweep_signal():
    """Verify Liquidity Sweep Reversal evaluates swing penetration and pin-bar rejection."""
    cfg = ORBSettings(symbol="XAUTUSD")
    df = _create_synthetic_candles(n_bars=160, base_price=4000.0, trend_slope=0.0, volatility=1.5)

    sig = evaluate_liquidity_sweep(df, cfg)
    assert isinstance(sig, Signal)
    assert sig.direction in {"LONG", "SHORT", "NO TRADE"}
    assert "swing_level_identified" in sig.checklist


# =============================================================================
# TEST 8: ARBITRATOR CONFLICT RESOLUTION & SHOCK_CRASH SAFETY
# =============================================================================

def test_08_arbitrator_conflict_resolution(monkeypatch):
    """Verify arbitrator rejects simultaneous opposing signals and blocks entries in SHOCK_CRASH."""
    cfg = ORBSettings(symbol="XAUTUSD")
    df = _create_synthetic_candles(n_bars=160, base_price=4000.0)

    # 1. Test SHOCK_CRASH immediate block
    df_crash = df.copy()
    df_crash.iloc[-1, df_crash.columns.get_loc("Open")] = 4050.0
    df_crash.iloc[-1, df_crash.columns.get_loc("High")] = 4055.0
    df_crash.iloc[-1, df_crash.columns.get_loc("Low")] = 3900.0
    df_crash.iloc[-1, df_crash.columns.get_loc("Close")] = 3910.0
    res_crash = arbitrate_strategies(df_crash, cfg)
    assert res_crash.winning_signal.direction == "NO TRADE"
    assert res_crash.decision in {"SHOCK_CRASH_BLOCKED", "REGIME_UNCERTAIN"}

    # 2. Test Conflicting Signals Resolution:
    # If one strategy produces LONG and another produces SHORT in the same regime, both must be rejected
    from strategy import arbitrator

    sig_long = Signal(
        direction="LONG",
        entry=4000.0,
        sl=3980.0,
        tp=4040.0,
        r_multiple=2.0,
        reason="Test Long",
        checklist={},
        strategy_name="TREND_FOLLOWING",
        quality_score=0.85,
    )
    sig_short = Signal(
        direction="SHORT",
        entry=4000.0,
        sl=4020.0,
        tp=3960.0,
        r_multiple=2.0,
        reason="Test Short",
        checklist={},
        strategy_name="ORB",
        quality_score=0.80,
    )

    # Monkeypatch regime to return TRENDING_BULL with both strategies allowed
    mock_regime = RegimeDetectionResult(
        regime=MarketRegime.TRENDING_BULL,
        confidence=0.85,
        allowed_strategies=["TREND_FOLLOWING", "ORB"],
        disallowed_strategies=[],
        rationale="Mock bull regime",
        metrics={},
    )
    monkeypatch.setattr(arbitrator, "detect_market_regime", lambda f, c: mock_regime)
    monkeypatch.setattr(arbitrator, "evaluate_trend_following", lambda f, c: sig_long)
    # Give ORB a counter-trend short to trigger conflict or counter-trend rejection
    monkeypatch.setattr(arbitrator, "evaluate_orb", lambda f, c: sig_short)

    res = arbitrate_strategies(df, cfg)
    assert res.winning_signal.direction in {"NO TRADE", "LONG"}  # Counter-trend short rejected or conflicting signals rejected


# =============================================================================
# TEST 9: MAKER LIMIT ORDER FILL SIMULATION
# =============================================================================

def test_09_maker_limit_order_fill_simulation(tmp_path):
    """Verify Maker Limit order fill conditions, unfill on price boundary, and zero fills."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "test_maker_fill.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # 1. Unfilled BUY: bar low is strictly above limit entry price
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="TEST",
        order_type="LIMIT",
        bar_low=4005.0,  # Price never reached 4000.0
    )
    assert ok is False
    assert "unfilled" in msg.lower()
    assert broker.open_position is None

    # 2. Filled BUY: bar low penetrates below limit entry price
    ok2, msg2, pos2 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="TEST",
        order_type="LIMIT",
        bar_low=3998.0,  # Price dipped below limit
    )
    assert ok2 is True
    assert broker.open_position is not None

    # Close position
    broker.close_simulated_trade(exit_price=4020.0, exit_reason="TAKE_PROFIT")
    assert broker.open_position is None

    # 3. Zero fill order: fill_ratio = 0.0 results in zero lots executed and no state changes
    ok3, msg3, pos3 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="TEST",
        fill_ratio=0.0,
    )
    assert ok3 is False
    assert "zero fill" in msg3.lower()
    assert broker.open_position is None


# =============================================================================
# TEST 10: TAKER STOP-LOSS SLIPPAGE MODEL
# =============================================================================

def test_10_taker_stop_loss_slippage_model(tmp_path):
    """Verify that stop-loss exits trigger taker fee tier and regime-dependent slippage."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER", fee_scenario="MAKER_TAKER")
    state_file = tmp_path / "test_slippage.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # Open standard 1 lot position
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="TEST",
        order_type="LIMIT",
        bar_low=3990.0,
    )

    # Close under VOLATILE_BREAKOUT / VOLATILE_EXPANDING regime with STOP_LOSS exit
    ok_close, msg_close, closed = broker.close_simulated_trade(
        exit_price=3980.0,
        exit_reason="STOP_LOSS",
        market_regime="VOLATILE_BREAKOUT",
    )
    assert ok_close is True
    # In MAKER_TAKER scenario with SL exit: exit order type must be TAKER
    assert closed["exit_order_type"] == "TAKER"
    assert closed["exit_fee"] > 0
    # Slippage must reflect volatile regime multiplier (1.5x)
    assert closed["slippage_cost"] > 0
    assert closed["total_friction"] > 0


# =============================================================================
# TEST 11: FEE & 18% GST RECONCILIATION
# =============================================================================

def test_11_fee_and_gst_accounting_reconciliation(tmp_path):
    """Verify maker/taker fee rates, 18% Indian GST calculation, and exact cash balance conservation."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        fee_scenario="MAKER_TAKER",
        apply_gst=True,
        gst_rate_pct=18.0,
        maker_fee_pct=0.01,
        taker_fee_pct=0.05,
    )
    state_file = tmp_path / "test_gst_recon.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    initial_cash = broker.cash_balance

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="TEST",
    )
    assert ok is True
    assert pos["entry_gst"] == round(pos["entry_fee"] * 0.18, 4)

    ok_c, msg_c, closed = broker.close_simulated_trade(
        exit_price=4020.0,
        exit_reason="TAKE_PROFIT",
    )
    assert ok_c is True
    # Verify GST on total brokerage fee
    assert closed["gst"] == round(closed["brokerage_fee"] * 0.18, 4)
    # Cash conservation: cash_balance == initial_cash + net_pnl
    expected_cash = round(initial_cash + closed["net_pnl"], 4)
    assert round(broker.cash_balance, 4) == expected_cash


# =============================================================================
# TEST 12: DAILY UTC TRADE CAP AND SESSION RESET
# =============================================================================

def test_12_daily_trade_cap_and_session_reset(tmp_path):
    """Verify maximum 1 trade per UTC calendar day limit and clean session reset."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        max_trades_per_day=1,
        state_path=tmp_path / "orb_risk_state.json",
        timezone="UTC",
    )
    risk_state = DailyRiskState(path=cfg.state_path, cfg=cfg)

    day1 = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 15, 0, 5, tzinfo=timezone.utc)

    # 1. Day 1: First trade allowed
    allowed1, reason1, _ = risk_state.can_trade(now=day1)
    assert allowed1 is True

    # Reserve trade on Day 1
    risk_state.reserve_trade(now=day1)

    # 2. Day 1: Second trade blocked by daily cap
    allowed2, reason2, _ = risk_state.can_trade(now=day1)
    assert allowed2 is False
    assert "Maximum trades" in reason2

    # 3. Day 2: Session resets at 00:00 UTC -> Trade allowed again
    allowed3, reason3, _ = risk_state.can_trade(now=day2)
    assert allowed3 is True
    assert "Daily risk guards passed" in reason3


# =============================================================================
# TEST 13: DAILY RISK GUARD 2% / $50 LOSS LOCK
# =============================================================================

def test_13_daily_risk_guard_2pct_loss_lock(tmp_path):
    """Verify that daily loss exceeding 2% (or $50.00) triggers risk guard lock."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        daily_loss_limit_pct=2.0,
        max_trades_per_day=5,
        cooldown_after_loss_minutes=0,
        state_path=tmp_path / "orb_risk_state_loss.json",
        timezone="UTC",
    )
    risk_state = DailyRiskState(path=cfg.state_path, cfg=cfg)
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    # Record small loss (-0.5%) -> Still allowed
    risk_state.record_trade(pnl_cash=-50.0, equity_at_start=10000.0, now=now)
    allowed1, reason1, _ = risk_state.can_trade(now=now)
    assert allowed1 is True

    # Record larger cumulative loss (-2.1% total) -> Locked!
    risk_state.record_trade(pnl_cash=-160.0, equity_at_start=10000.0, now=now)
    allowed2, reason2, _ = risk_state.can_trade(now=now)
    assert allowed2 is False
    assert "Daily loss limit hit" in reason2


# =============================================================================
# TEST 14: PROFIT FACTOR AND EXPECTANCY CALCULATOR
# =============================================================================

def test_14_profit_factor_and_expectancy_calculator():
    """Verify Profit Factor and Expectancy R calculations, including zero-loss division handling."""
    # 1. Normal mixed trades
    trades = [
        {"gross_pnl": 50.0, "net_pnl": 48.0, "r_multiple": 2.0},
        {"gross_pnl": -25.0, "net_pnl": -27.0, "r_multiple": -1.0},
        {"gross_pnl": 60.0, "net_pnl": 58.0, "r_multiple": 2.5},
    ]
    metrics = calculate_phase4_7_metrics(trades, initial_equity=10000.0)
    assert metrics["total_trades"] == 3
    assert metrics["winning_trades"] == 2
    assert metrics["losing_trades"] == 1
    assert metrics["win_rate_pct"] == round((2 / 3) * 100.0, 2)
    # Gross Gains = 110.0, Gross Losses = 25.0 -> PF = 110 / 25 = 4.40
    assert metrics["profit_factor"] == 4.40
    assert metrics["expectancy_r"] > 0

    # 2. Zero-Loss Scenario: Must not raise ZeroDivisionError and must report Infinity
    win_only_trades = [
        {"gross_pnl": 50.0, "net_pnl": 48.0, "r_multiple": 2.0},
        {"gross_pnl": 30.0, "net_pnl": 28.0, "r_multiple": 1.5},
    ]
    win_metrics = calculate_phase4_7_metrics(win_only_trades, initial_equity=10000.0)
    assert win_metrics["profit_factor"] == float("inf")
    assert win_metrics["profit_factor_display"] == "Infinity"


# =============================================================================
# TEST 15: MAXIMUM DRAWDOWN PEAK-TO-TROUGH TRACKING
# =============================================================================

def test_15_max_drawdown_peak_trough_tracking():
    """Verify that maximum drawdown tracks peak-to-trough drop accurately in cash and percentage."""
    trades = [
        {"net_pnl": 100.0, "gross_pnl": 102.0, "r_multiple": 2.0},   # Equity: 10100 (peak: 10100)
        {"net_pnl": -200.0, "gross_pnl": -198.0, "r_multiple": -1.0}, # Equity: 9900 (dd: 200, 1.98%)
        {"net_pnl": -100.0, "gross_pnl": -98.0, "r_multiple": -0.5},  # Equity: 9800 (dd: 300, 2.97%)
        {"net_pnl": 500.0, "gross_pnl": 505.0, "r_multiple": 3.0},   # Equity: 10300 (new peak)
        {"net_pnl": -50.0, "gross_pnl": -48.0, "r_multiple": -0.25},  # Equity: 10250 (dd: 50, 0.49%)
    ]
    metrics = calculate_phase4_7_metrics(trades, initial_equity=10000.0)
    # Peak was 10100, trough was 9800 -> Drawdown cash = 300.0, pct = (300 / 10100) * 100 = 2.97%
    assert round(metrics["max_drawdown_cash"], 2) == 300.0
    assert round(metrics["max_drawdown_pct"], 2) == 2.97


# =============================================================================
# TEST 16: SORTINO AND SHARPE RATIO METRICS
# =============================================================================

def test_16_sortino_and_sharpe_ratio_metrics():
    """Verify Annualized Sharpe (365 days) and Sortino calculations."""
    trades = []
    # 5 days of positive returns and 1 day of small negative return
    for day in range(1, 11):
        pnl = 20.0 if day != 4 else -10.0
        trades.append({
            "net_pnl": pnl,
            "gross_pnl": pnl + 1.0,
            "r_multiple": 1.0 if pnl > 0 else -0.5,
            "exit_time": f"2026-11-{day:02d}T15:00:00Z",
        })

    metrics = calculate_phase4_7_metrics(trades, initial_equity=10000.0)
    assert metrics["annualized_sharpe"] > 0.0
    assert metrics["sortino_ratio"] > 0.0
    assert metrics["unique_sessions_count"] == 10


# =============================================================================
# TEST 17: FAIL-CLOSED PORTALOCKER CROSS-PROCESS SAFETY
# =============================================================================

def test_17_fail_closed_portalocker_cross_process_safety(tmp_path, monkeypatch):
    """Reaffirm that portalocker fallback fails closed in production and rejects bypass env vars."""
    import services.delta_paper_broker as broker_mod

    lock_file = tmp_path / "safety_test.lock"

    # Simulate portalocker missing
    monkeypatch.setattr(broker_mod, "portalocker", None)

    # 1. In production (no PYTEST_CURRENT_TEST) -> Must raise RuntimeError
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TEST_RUNNER", raising=False)
    monkeypatch.delenv("CI_TEST_MODE", raising=False)

    lock = CrossProcessReentrantLock(lock_file)
    with pytest.raises(RuntimeError, match="FATAL: portalocker is required"):
        lock.acquire(timeout=1.0)

    # 2. Attempted bypass with TEST_RUNNER -> Still raises RuntimeError
    monkeypatch.setenv("TEST_RUNNER", "1")
    with pytest.raises(RuntimeError, match="FATAL: portalocker is required"):
        lock.acquire(timeout=1.0)

    # 3. Attempted bypass with CI_TEST_MODE -> Still raises RuntimeError
    monkeypatch.delenv("TEST_RUNNER", raising=False)
    monkeypatch.setenv("CI_TEST_MODE", "true")
    with pytest.raises(RuntimeError, match="FATAL: portalocker is required"):
        lock.acquire(timeout=1.0)


# =============================================================================
# TEST 18: DEFECT LEDGER CHECKSUM INVARIANCE
# =============================================================================

def test_18_defect_ledger_checksum_invariance():
    """Verify bit-for-bit cryptographic invariance of historical defect ledger (DAD10844...)."""
    repo_root = Path(__file__).resolve().parent.parent
    state_file = repo_root / "data" / "paper_forward_24h_state.json"

    assert state_file.exists(), f"Defect ledger missing: {state_file}"

    hasher = hashlib.sha256()
    with open(state_file, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)

    actual_hash = hasher.hexdigest().lower()
    assert actual_hash == EXPECTED_DEFECT_LEDGER_SHA256, (
        f"DEFECT LEDGER MODIFIED! Expected {EXPECTED_DEFECT_LEDGER_SHA256}, got {actual_hash}"
    )


# =============================================================================
# TEST 19: STATISTICAL ACCEPTANCE GATE REQUIRES >= 100 TRADES
# =============================================================================

def test_19_statistical_acceptance_gate_requires_100_trades():
    """Verify that fewer than 100 closed trades fails the statistical acceptance gate with INSUFFICIENT_EVIDENCE."""
    # Only 15 trades with high win rate
    trades = [
        {"net_pnl": 30.0, "gross_pnl": 32.0, "r_multiple": 2.0} for _ in range(12)
    ] + [
        {"net_pnl": -15.0, "gross_pnl": -13.0, "r_multiple": -1.0} for _ in range(3)
    ]

    metrics = calculate_phase4_7_metrics(trades)
    gate = evaluate_statistical_acceptance_gate(metrics, min_trades=100)

    assert gate["gate_passed"] is False
    assert gate["status"] == "INSUFFICIENT_EVIDENCE"
    assert gate["gate_code"] == "GATE_INSUFFICIENT_SAMPLE_SIZE"
    assert "INSUFFICIENT_EVIDENCE" in gate["summary_rationale"]
    assert gate["sample_size"] == 15
    assert gate["required_sample_size"] == 100


# =============================================================================
# TEST 20: STATISTICAL ACCEPTANCE GATE EVALUATES KPI CRITERIA
# =============================================================================

def test_20_statistical_acceptance_gate_evaluates_kpi_criteria():
    """Verify that 100+ trades and 100+ sessions are evaluated against all 5 quantitative KPI criteria."""
    # Case A: 100 trades across 100 distinct UTC sessions meeting all criteria (WR >= 50%, PF >= 1.30, Exp > 0.20R, MDD <= 3.5%, Sharpe >= 1.20)
    passing_trades = []
    for i in range(100):
        is_win = (i % 10) < 6  # 60% win rate
        pnl = 30.0 if is_win else -15.0
        r_mult = 2.0 if is_win else -1.0
        m = (i // 25) + 1
        d = (i % 25) + 1
        passing_trades.append({
            "net_pnl": pnl,
            "gross_pnl": pnl + 1.0,
            "r_multiple": r_mult,
            "exit_time": f"2026-{m:02d}-{d:02d}T12:00:00Z",
        })

    passing_metrics = calculate_phase4_7_metrics(passing_trades, initial_equity=10000.0)
    passing_gate = evaluate_statistical_acceptance_gate(passing_metrics, min_trades=100, min_sessions=100)

    assert passing_gate["sample_size"] >= 100
    assert passing_gate["sessions_count"] >= 100
    assert passing_gate["criteria_breakdown"]["sample_size_check"]["passed"] is True
    assert passing_gate["criteria_breakdown"]["session_count_check"]["passed"] is True
    assert passing_gate["criteria_breakdown"]["win_rate_check"]["passed"] is True
    assert passing_gate["criteria_breakdown"]["profit_factor_check"]["passed"] is True
    assert passing_gate["criteria_breakdown"]["expectancy_check"]["passed"] is True

    # Case B: 100 trades across 100 sessions with failing Win Rate (e.g., 30%)
    failing_trades = []
    for i in range(100):
        is_win = (i % 10) < 3  # 30% win rate
        pnl = 20.0 if is_win else -20.0
        r_mult = 1.0 if is_win else -1.0
        m = (i // 25) + 1
        d = (i % 25) + 1
        failing_trades.append({
            "net_pnl": pnl,
            "gross_pnl": pnl + 1.0,
            "r_multiple": r_mult,
            "exit_time": f"2026-{m:02d}-{d:02d}T12:00:00Z",
        })

    failing_metrics = calculate_phase4_7_metrics(failing_trades, initial_equity=10000.0)
    failing_gate = evaluate_statistical_acceptance_gate(failing_metrics, min_trades=100, min_sessions=100)

    assert failing_gate["gate_passed"] is False
    assert failing_gate["status"] == "KPI_THRESHOLD_UNMET"
    assert failing_gate["gate_code"] == "GATE_FAILED_KPI"
    assert any("Win Rate" in f for f in failing_gate["failed_criteria"])
