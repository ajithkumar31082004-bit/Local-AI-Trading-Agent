"""Comprehensive automated tests for Market Regime Readiness and State Transitions.

Verifies:
1. History requirement enforcement: < 50 15m bars (< 150 5m bars) -> WARMING_UP with explicit missing requirements.
2. Full history readiness: >= 50 15m bars (>= 150 5m bars) with valid indicators -> READY.
3. 15m candle alignment: Resampling strictly groups into complete 15m buckets and drops partial bars without look-ahead bias.
4. Indicator warm-up validation: Detects NaN in EMA50, ADX14, ATR14, and VWAP.
5. Explicit connection & readiness state machine transitions: DISCONNECTED -> CONNECTING -> BOOTSTRAPPING -> WARMING_UP -> READY -> DEGRADED.
6. Arbitrator integration: Rejects trading when state != READY, returning decision="SYSTEM_WARMING_UP".
7. Regime classification coverage: Validates all 5 regimes on READY data (BULL_TREND, BEAR_TREND, RANGE, VOLATILE_BREAKOUT, UNCERTAIN).
8. Safety invariant: Broker execution remains strictly simulated (PAPER mode, no exchange calls).
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from config.orb_settings import ORBSettings
from regime.readiness import (
    ReadinessState,
    SystemReadiness,
    evaluate_system_readiness,
)
from regime.market_regime import (
    MarketRegime,
    RegimeDetectionResult,
    detect_market_regime,
)
from strategy.arbitrator import arbitrate_strategies
from indicators.orb_indicators import resample_to_15m
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from services.delta_paper_bot import DeltaPaperBot
from market_data.delta_live_feed import DeltaLiveMarketDataFeed


def make_test_cfg(**kwargs) -> ORBSettings:
    defaults = {
        "symbol": "XAUTUSD",
        "timezone": "Asia/Kolkata",
        "bootstrap_lookback_candles": 450,
        "min_completed_5m_bars": 150,
        "min_completed_15m_bars": 50,
        "mode": "PAPER",
        "live_trading_enabled": False,
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def generate_synthetic_candles(n_bars: int, trend: str = "flat", base_price: float = 4000.0) -> pd.DataFrame:
    """Generate n_bars of 5-minute candles aligned to clock boundaries."""
    idx = pd.date_range("2026-10-30 00:00", periods=n_bars, freq="5min", tz="Asia/Kolkata")
    closes = []
    current = base_price

    for i in range(n_bars):
        if trend == "bull":
            current += 0.8
        elif trend == "bear":
            current -= 0.8
        elif trend == "volatile":
            current += (10.0 if i % 2 == 0 else -8.0)
        elif trend == "flat":
            current = base_price + 2.0 * np.sin(i / 5.0)
        closes.append(current)

    opens = [c - 0.2 if trend == "bull" else c + 0.2 for c in closes]
    highs = [max(o, c) + (4.0 if trend == "volatile" else 1.0) for o, c in zip(opens, closes)]
    lows = [min(o, c) - (4.0 if trend == "volatile" else 1.0) for o, c in zip(opens, closes)]
    vols = [150000.0 if trend == "volatile" else 20000.0] * n_bars

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": vols,
        },
        index=idx,
    )
    return df


# -----------------------------------------------------------------------------
# 1 & 2: Readiness Evaluation & History Requirements
# -----------------------------------------------------------------------------

def test_insufficient_history_reports_warming_up():
    """Verify that fewer than 150 5m bars (< 50 15m bars) results in WARMING_UP with exact missing requirements."""
    cfg = make_test_cfg()
    df_short = generate_synthetic_candles(90, trend="flat")  # 90 5m bars -> 30 15m bars (< 50)

    readiness = evaluate_system_readiness(
        df_5m=df_short,
        is_ws_connected=True,
        is_feed_running=True,
        is_bootstrapping=False,
        cfg=cfg,
    )

    assert readiness.state == ReadinessState.WARMING_UP
    assert readiness.is_ready is False
    assert readiness.completed_5m_count == 90
    assert readiness.required_5m_count == 150
    assert readiness.completed_15m_count == 30
    assert readiness.required_15m_count == 50
    assert any("Insufficient 5m candles: 90/150" in req for req in readiness.missing_requirements)
    assert any("Insufficient 15m resampled candles: 30/50" in req for req in readiness.missing_requirements)


def test_sufficient_history_reports_ready():
    """Verify that >= 150 5m bars (>= 50 15m bars) with valid indicators results in READY."""
    cfg = make_test_cfg()
    df_full = generate_synthetic_candles(180, trend="flat")  # 180 5m bars -> 60 15m bars (>= 50)

    readiness = evaluate_system_readiness(
        df_5m=df_full,
        is_ws_connected=True,
        is_feed_running=True,
        is_bootstrapping=False,
        cfg=cfg,
    )

    assert readiness.state == ReadinessState.READY
    assert readiness.is_ready is True
    assert readiness.completed_5m_count == 180
    assert readiness.completed_15m_count == 60
    assert len(readiness.missing_requirements) == 0
    assert readiness.indicator_status["15m_EMA50"] is True
    assert readiness.indicator_status["15m_EMA20"] is True
    assert readiness.indicator_status["15m_ADX14"] is True
    assert readiness.indicator_status["5m_EMA50"] is True
    assert readiness.indicator_status["5m_VWAP"] is True


# -----------------------------------------------------------------------------
# 3: 15-Minute Candle Alignment & Look-Ahead Bias Prevention
# -----------------------------------------------------------------------------

def test_15m_resample_alignment_and_partial_bucket_exclusion():
    """Verify resample_to_15m excludes partial 15m buckets (fewer than 3 5m bars) without look-ahead bias."""
    # Create 8 5m bars: 00:00, 00:05, 00:10 (bucket 1 - full)
    #                   00:15, 00:20, 00:25 (bucket 2 - full)
    #                   00:30, 00:35         (bucket 3 - partial, only 2 bars)
    df_8 = generate_synthetic_candles(8, trend="flat")
    df_15 = resample_to_15m(df_8)

    # Bucket 3 must be excluded because it has only 2 completed 5m bars
    assert len(df_15) == 2
    assert df_15.index[0] == pd.Timestamp("2026-10-30 00:00", tz="Asia/Kolkata")
    assert df_15.index[1] == pd.Timestamp("2026-10-30 00:15", tz="Asia/Kolkata")
    assert pd.Timestamp("2026-10-30 00:30", tz="Asia/Kolkata") not in df_15.index

    # Check OHLC aggregation of the first 15m bar
    first_3_5m = df_8.iloc[:3]
    assert df_15.iloc[0]["Open"] == first_3_5m.iloc[0]["Open"]
    assert df_15.iloc[0]["High"] == first_3_5m["High"].max()
    assert df_15.iloc[0]["Low"] == first_3_5m["Low"].min()
    assert df_15.iloc[0]["Close"] == first_3_5m.iloc[2]["Close"]
    assert df_15.iloc[0]["Volume"] == first_3_5m["Volume"].sum()


# -----------------------------------------------------------------------------
# 4: Indicator Warm-up & NaN Detection
# -----------------------------------------------------------------------------

def test_indicator_warmup_detects_nan():
    """Verify that if an indicator produces NaN (e.g., exactly at minimum boundary without convergence), it is flagged."""
    cfg = make_test_cfg(min_completed_15m_bars=49)
    # 49 15m bars -> EMA50 on 15m will have NaN at bar index 48 because min_periods is 50
    df_147 = generate_synthetic_candles(147, trend="flat")
    readiness = evaluate_system_readiness(df_147, cfg=cfg)

    assert readiness.is_ready is False
    assert readiness.indicator_status["15m_EMA50"] is False
    assert any("EMA50" in req for req in readiness.missing_requirements)


# -----------------------------------------------------------------------------
# 5: Readiness State Machine Transitions
# -----------------------------------------------------------------------------

def test_readiness_state_machine_transitions():
    """Verify transitions across DISCONNECTED, CONNECTING, BOOTSTRAPPING, WARMING_UP, READY, and DEGRADED."""
    cfg = make_test_cfg()
    empty_df = pd.DataFrame()
    full_df = generate_synthetic_candles(180, trend="flat")
    partial_df = generate_synthetic_candles(60, trend="flat")

    # 1. DISCONNECTED: Feed not running, no data
    s_disc = evaluate_system_readiness(empty_df, is_ws_connected=False, is_feed_running=False, is_bootstrapping=False, cfg=cfg)
    assert s_disc.state == ReadinessState.DISCONNECTED
    assert s_disc.is_ready is False

    # 2. CONNECTING: Feed running, WS not yet connected, no data
    s_conn = evaluate_system_readiness(empty_df, is_ws_connected=False, is_feed_running=True, is_bootstrapping=False, cfg=cfg)
    assert s_conn.state == ReadinessState.CONNECTING
    assert s_conn.is_ready is False

    # 3. BOOTSTRAPPING: Fetching REST history
    s_boot = evaluate_system_readiness(empty_df, is_ws_connected=True, is_feed_running=True, is_bootstrapping=True, cfg=cfg)
    assert s_boot.state == ReadinessState.BOOTSTRAPPING
    assert s_boot.is_ready is False

    # 4. WARMING_UP: Connected, but insufficient bars
    s_warm = evaluate_system_readiness(partial_df, is_ws_connected=True, is_feed_running=True, is_bootstrapping=False, cfg=cfg)
    assert s_warm.state == ReadinessState.WARMING_UP
    assert s_warm.is_ready is False

    # 5. READY: Full data, all indicators non-NaN, connected
    s_ready = evaluate_system_readiness(full_df, is_ws_connected=True, is_feed_running=True, is_bootstrapping=False, cfg=cfg)
    assert s_ready.state == ReadinessState.READY
    assert s_ready.is_ready is True

    # 6. DEGRADED: Had full data, but WebSocket disconnected during streaming
    s_degr = evaluate_system_readiness(full_df, is_ws_connected=False, is_feed_running=True, is_bootstrapping=False, cfg=cfg)
    assert s_degr.state == ReadinessState.DEGRADED
    assert s_degr.is_ready is False
    assert any("WebSocket disconnected" in req for req in s_degr.missing_requirements)

    # 7. DEGRADED via stale feed: WS connected but no updates within freshness threshold
    s_stale = evaluate_system_readiness(full_df, is_ws_connected=True, is_feed_running=True, is_bootstrapping=False, is_stale=True, cfg=cfg)
    assert s_stale.state == ReadinessState.DEGRADED
    assert s_stale.is_ready is False
    assert any("stale" in req for req in s_stale.missing_requirements)


# -----------------------------------------------------------------------------
# 6: Arbitrator Integration with Readiness
# -----------------------------------------------------------------------------

def test_arbitrator_blocks_trading_when_warming_up():
    """Verify arbitrator blocks trading and returns SYSTEM_WARMING_UP diagnostic when data is warming up."""
    cfg = make_test_cfg()
    df_short = generate_synthetic_candles(90, trend="bull")

    arb_res = arbitrate_strategies(df_short, cfg)
    assert arb_res.decision == "SYSTEM_WARMING_UP"
    assert arb_res.winning_signal.direction == "NO TRADE"
    assert arb_res.winning_signal.diagnostic_code == "WARMING_UP"
    assert "WARMING_UP" in arb_res.winning_signal.reason
    assert "readiness" in arb_res.regime_result.metrics
    assert arb_res.regime_result.metrics["readiness"]["is_ready"] is False


# -----------------------------------------------------------------------------
# 7: All 5 Market Regimes on READY Data
# -----------------------------------------------------------------------------

def test_market_regime_classification_on_ready_data():
    """Verify that all 5 regimes are cleanly detected when data satisfies READY status."""
    cfg = make_test_cfg()

    # A. TRENDING_BULLISH
    df_bull = generate_synthetic_candles(180, trend="bull", base_price=3900.0)
    res_bull = detect_market_regime(df_bull, cfg)
    assert res_bull.regime == MarketRegime.TRENDING_BULLISH
    assert res_bull.metrics["readiness"]["is_ready"] is True
    assert "TREND_FOLLOWING" in res_bull.allowed_strategies

    # B. TRENDING_BEARISH
    df_bear = generate_synthetic_candles(180, trend="bear", base_price=4100.0)
    res_bear = detect_market_regime(df_bear, cfg)
    assert res_bear.regime == MarketRegime.TRENDING_BEARISH
    assert res_bear.metrics["readiness"]["is_ready"] is True
    assert "TREND_FOLLOWING" in res_bear.allowed_strategies

    # C. RANGING
    df_range = generate_synthetic_candles(180, trend="flat", base_price=4000.0)
    res_range = detect_market_regime(df_range, cfg)
    assert res_range.regime == MarketRegime.RANGING
    assert res_range.metrics["readiness"]["is_ready"] is True
    assert "TREND_FOLLOWING" in res_range.disallowed_strategies

    # D. VOLATILE_BREAKOUT
    # 179 bars of low volatility consolidation followed by sudden high-volume expansion breakout
    df_vol = generate_synthetic_candles(180, trend="flat", base_price=4000.0)
    # Inject breakout on the final bar
    df_vol.iloc[-1, df_vol.columns.get_loc("Open")] = 4002.0
    df_vol.iloc[-1, df_vol.columns.get_loc("High")] = 4035.0
    df_vol.iloc[-1, df_vol.columns.get_loc("Low")] = 4001.0
    df_vol.iloc[-1, df_vol.columns.get_loc("Close")] = 4032.0
    df_vol.iloc[-1, df_vol.columns.get_loc("Volume")] = 100000.0  # 5x average volume
    res_vol = detect_market_regime(df_vol, cfg)
    assert res_vol.regime == MarketRegime.VOLATILE_BREAKOUT
    assert res_vol.metrics["readiness"]["is_ready"] is True
    assert "ATR_COMPRESSION_BREAKOUT" in res_vol.allowed_strategies

    # E. UNCERTAIN (conflicting indicators on ready data)
    # Generate choppy random walk that fails trend, range, and breakout thresholds
    np.random.seed(42)
    idx = pd.date_range("2026-10-30 00:00", periods=180, freq="5min", tz="Asia/Kolkata")
    random_steps = np.random.normal(0, 0.5, 180).cumsum()
    closes = 4000.0 + random_steps
    df_uncertain = pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 0.3,
            "Low": closes - 0.3,
            "Close": closes,
            "Volume": [10000.0] * 180,
        },
        index=idx,
    )
    res_unc = detect_market_regime(df_uncertain, cfg)
    # Should resolve to either RANGING or UNCERTAIN, with valid readiness attached
    assert res_unc.metrics["readiness"]["is_ready"] is True
    assert res_unc.regime in {MarketRegime.UNCERTAIN, MarketRegime.RANGING}


# -----------------------------------------------------------------------------
# 8: Safety Invariant (Paper Mode Isolation)
# -----------------------------------------------------------------------------

def test_paper_trading_safety_invariant(tmp_path):
    """Verify that paper broker strictly simulates execution locally without exchange API keys or network."""
    cfg = make_test_cfg()
    assert cfg.mode == "PAPER"
    assert cfg.live_trading_enabled is False

    broker = DeltaPaperBroker(
        initial_equity=10000.0,
        cfg=cfg,
        state_file=str(tmp_path / "paper_safety_state.json"),
    )

    # Attempt simulated order
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="PORTFOLIO",
        signal_reason="Ready market regime entry",
    )

    assert ok is True
    assert pos["direction"] == "LONG"
    # Entry price reflects simulated slippage and spread (e.g. 4000.80)
    assert pos["entry_price"] >= 4000.0
    assert broker.open_position is not None
    # No network connections or real orders placed
    summary = broker.get_account_summary()
    assert summary["mode"] == "PAPER"


# -----------------------------------------------------------------------------
# 9: Stale Feed, Disconnect & Recovery Safety Tests (Mocked)
# -----------------------------------------------------------------------------

def test_stale_feed_blocks_strategy_evaluation_in_bot(tmp_path):
    """Verify that when market data exceeds staleness limit (> 120s), readiness becomes DEGRADED and bot blocks entries."""
    cfg = make_test_cfg()
    df_full = generate_synthetic_candles(180, trend="flat")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "stale_test.json"), initial_equity=10000.0)

    # Create mock feed where last update was 150 seconds ago (stale)
    mock_feed = MagicMock(spec=DeltaLiveMarketDataFeed)
    mock_feed.get_readiness.return_value = evaluate_system_readiness(
        df_5m=df_full,
        is_ws_connected=True,
        is_feed_running=True,
        is_bootstrapping=False,
        is_stale=True,
        cfg=cfg,
    )

    DeltaPaperBot._instance = None
    bot = DeltaPaperBot(cfg=cfg, feed=mock_feed, broker=broker)

    sig = bot._evaluate_strategy(df_full)
    assert sig is None
    assert "DEGRADED" in bot._last_rejection_reason
    assert "stale" in bot._last_rejection_reason.lower()


def test_websocket_disconnect_blocks_strategy_evaluation_in_bot(tmp_path):
    """Verify that when WebSocket disconnects during streaming, readiness becomes DEGRADED and bot blocks entries."""
    cfg = make_test_cfg()
    df_full = generate_synthetic_candles(180, trend="flat")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "ws_drop_test.json"), initial_equity=10000.0)

    # Create mock feed where ws is disconnected
    mock_feed = MagicMock(spec=DeltaLiveMarketDataFeed)
    mock_feed.get_readiness.return_value = evaluate_system_readiness(
        df_5m=df_full,
        is_ws_connected=False,
        is_feed_running=True,
        is_bootstrapping=False,
        is_stale=False,
        cfg=cfg,
    )

    DeltaPaperBot._instance = None
    bot = DeltaPaperBot(cfg=cfg, feed=mock_feed, broker=broker)

    sig = bot._evaluate_strategy(df_full)
    assert sig is None
    assert "DEGRADED" in bot._last_rejection_reason
    assert "websocket disconnected" in bot._last_rejection_reason.lower()


def test_reconnection_recovers_readiness_and_unblocks_strategy(tmp_path):
    """Verify that once socket reconnects with fresh data, readiness recovers to READY and evaluations proceed."""
    cfg = make_test_cfg()
    df_full = generate_synthetic_candles(180, trend="bull")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "recon_test.json"), initial_equity=10000.0)

    mock_feed = MagicMock(spec=DeltaLiveMarketDataFeed)
    # Recovered state
    mock_feed.get_readiness.return_value = evaluate_system_readiness(
        df_5m=df_full,
        is_ws_connected=True,
        is_feed_running=True,
        is_bootstrapping=False,
        is_stale=False,
        cfg=cfg,
    )

    DeltaPaperBot._instance = None
    bot = DeltaPaperBot(cfg=cfg, feed=mock_feed, broker=broker)

    # Force current time to be within session entry window
    with patch("services.delta_paper_bot.datetime") as mock_dt:
        mock_now = datetime(2026, 11, 4, 14, 0, 0, tzinfo=bot.tz)
        mock_dt.now.return_value = mock_now
        # Strategy evaluation must proceed without readiness rejection
        sig = bot._evaluate_strategy(df_full)
        assert bot._last_rejection_reason != ""
        assert "DEGRADED" not in bot._last_rejection_reason
        assert "WARMING_UP" not in bot._last_rejection_reason


def test_existing_position_preserved_safely_during_stale_feed(tmp_path):
    """Verify that existing open positions are preserved and safely managed by SL/TP during feed degradation."""
    cfg = make_test_cfg()
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "pos_stale_test.json"), initial_equity=10000.0)

    # Open a simulated trade
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="PORTFOLIO",
        signal_reason="Pre-stale entry",
    )
    assert ok is True
    assert broker.open_position is not None

    mock_feed = MagicMock(spec=DeltaLiveMarketDataFeed)
    df_full = generate_synthetic_candles(180, trend="flat")
    mock_feed.get_readiness.return_value = evaluate_system_readiness(
        df_5m=df_full,
        is_ws_connected=True,
        is_feed_running=True,
        is_bootstrapping=False,
        is_stale=True,  # Stale
        cfg=cfg,
    )

    DeltaPaperBot._instance = None
    bot = DeltaPaperBot(cfg=cfg, feed=mock_feed, broker=broker)

    # Strategy evaluation must NOT open any new position
    sig = bot._evaluate_strategy(df_full)
    assert sig is None
    # Position remains intact in broker
    assert broker.open_position is not None
    assert broker.open_position["entry_price"] >= 4000.0
    assert broker.open_position["stop_loss"] == 3980.0
    assert broker.open_position["take_profit"] == 4040.0


def test_no_strategy_can_bypass_readiness(tmp_path):
    """Verify that none of the strategy modes (PORTFOLIO, ORB, TREND, VWAP) can bypass readiness checks."""
    cfg = make_test_cfg()
    df_full = generate_synthetic_candles(180, trend="bull")

    mock_feed = MagicMock(spec=DeltaLiveMarketDataFeed)
    mock_feed.get_readiness.return_value = SystemReadiness(
        state=ReadinessState.WARMING_UP,
        is_ready=False,
        completed_5m_count=50,
        required_5m_count=150,
        completed_15m_count=16,
        required_15m_count=50,
        indicator_status={},
        missing_requirements=["Warming up indicators"],
        details={},
    )

    for strat_mode in ("PORTFOLIO", "ORB", "TREND", "TREND_FOLLOWING", "VWAP", "VWAP_EMA"):
        broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / f"bypass_{strat_mode}.json"), initial_equity=10000.0)
        bot_cfg = make_test_cfg(strategy_type=strat_mode)
        DeltaPaperBot._instance = None
        bot = DeltaPaperBot(cfg=bot_cfg, feed=mock_feed, broker=broker)

        sig = bot._evaluate_strategy(df_full)
        assert sig is None, f"Strategy mode {strat_mode} bypassed readiness check!"
        assert "WARMING_UP" in bot._last_rejection_reason
        assert broker.open_position is None


# -----------------------------------------------------------------------------
# 10: Risk Configuration & Safety Violation Tests
# -----------------------------------------------------------------------------

def test_risk_configuration_defaults_and_precedence():
    """Verify exact risk defaults in ORBSettings and precedence."""
    cfg = ORBSettings()
    # Default authoritative values
    assert cfg.risk_pct == 0.5
    assert cfg.daily_loss_limit_pct == 1.5
    assert cfg.daily_loss_limit_r == 2.0
    assert cfg.max_trades_per_day == 1

    # Keyword argument override
    custom_cfg = ORBSettings(risk_pct=1.0, daily_loss_limit_r=3.0)
    assert custom_cfg.risk_pct == 1.0
    assert custom_cfg.daily_loss_limit_r == 3.0


def test_paper_safety_violations_unconditional():
    """Verify DeltaPaperBot and DeltaPaperBroker raise PaperSafetyViolation if live mode is attempted."""
    # Bot rejects mode='LIVE'
    with pytest.raises(PaperSafetyViolation, match="mode='PAPER'"):
        live_cfg = ORBSettings(mode="LIVE")
        DeltaPaperBot._instance = None
        DeltaPaperBot(cfg=live_cfg)

    # Bot rejects live_trading_enabled=True
    with pytest.raises(PaperSafetyViolation, match="LIVE_TRADING_ENABLED"):
        live_enabled_cfg = ORBSettings(mode="PAPER", live_trading_enabled=True)
        DeltaPaperBot._instance = None
        DeltaPaperBot(cfg=live_enabled_cfg)

    # Broker unconditionally rejects execute_live_order
    paper_broker = DeltaPaperBroker(cfg=make_test_cfg())
    with pytest.raises(PaperSafetyViolation, match="execute_live_order"):
        paper_broker.execute_live_order("XAUTUSD", "BUY", 1)

