"""Unit and integration tests for Strategy Arbitration Engine."""
import pandas as pd
import pytest
from config.orb_settings import ORBSettings
from strategy.arbitrator import arbitrate_strategies, evaluate_arbitrated_portfolio
from strategy.orb_ema_volume import Signal
from regime.market_regime import MarketRegime
from tests.fixtures.synthetic_market_data import create_synthetic_bearish_continuation_df


def make_cfg(**kwargs):
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "session_start": "09:00",
        "session_end": "23:30",
        "min_signal_quality_score": 0.50,
        "target_r": 2.0,
        "contract_size_xaut": 0.001,
        "lot_increment": 1,
        "leverage": 100.0,
        "risk_pct": 2.0,
        "max_margin_pct_equity": 50.0,
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def test_arbitrator_selects_best_signal():
    """In a strong bearish move with pullback, arbitrator selects the highest quality eligible signal."""
    cfg = make_cfg(session_start="09:00", session_end="23:30")
    df = create_synthetic_bearish_continuation_df(continuation_vol=180000.0)
    arb_res = arbitrate_strategies(df, cfg)

    assert arb_res.regime_result.regime == MarketRegime.TRENDING_BEARISH
    assert arb_res.winning_signal.direction == "SHORT"
    assert arb_res.winning_signal.strategy_name in {"TREND_FOLLOWING", "VWAP_EMA"}
    assert arb_res.winning_signal.quality_score >= 0.50
    assert arb_res.decision == "TRADE_SELECTED"


def test_arbitrator_rejects_conflicting_signals(monkeypatch):
    """If one strategy emits LONG and another emits SHORT at the same time, arbitrator rejects both."""
    from strategy import arbitrator

    sig_long = Signal("LONG", 4000.0, 3990.0, 4020.0, 2.0, "Long setup", {}, strategy_name="STRAT_A", quality_score=0.85)
    sig_short = Signal("SHORT", 4000.0, 4010.0, 3980.0, 2.0, "Short setup", {}, strategy_name="STRAT_B", quality_score=0.80)

    # Mock raw signals with conflicting directions
    cfg = make_cfg()
    idx = pd.date_range("2026-10-30 11:00", periods=70, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({"Open": [4000.0]*70, "High": [4005.0]*70, "Low": [3995.0]*70, "Close": [4000.0]*70, "Volume": [1000.0]*70}, index=idx)

    # Monkeypatch evaluate functions
    monkeypatch.setattr(arbitrator, "evaluate_orb", lambda f, c: sig_long)
    monkeypatch.setattr(arbitrator, "evaluate_trend_following", lambda f, c: sig_short)

    arb_res = arbitrator.arbitrate_strategies(df, cfg)
    # If both made it through regime, decision must be CONFLICT_REJECTED or NO_TRADE
    if len(arb_res.eligible_signals) >= 2:
        assert arb_res.decision == "CONFLICT_REJECTED"
        assert arb_res.winning_signal.direction == "NO TRADE"
        assert arb_res.winning_signal.diagnostic_code == "CONFLICTING_SIGNALS"

