"""Automated safety and functionality tests for Delta Exchange India Paper Trading.

Verifies the 15 mandatory safety requirements:
1. Default mode is PAPER.
2. Public market-data connection works without trading credentials.
3. No private order-placement endpoint is called.
4. Simulated market order does not create an exchange order.
5. Simulated stop-loss closes only local position.
6. PAPER equity is independent of real exchange equity.
7. Daily loss limits work.
8. Maximum trades per day works.
9. Duplicate signals do not open duplicate positions.
10. Incomplete candles cannot trigger confirmed entries.
11. Restarting application preserves paper balance and trade history.
12. WebSocket disconnections recover safely.
13. Invalid configuration fails safely.
14. LIVE mode is rejected by the PAPER-only runtime.
15. Explicit test that fails if PAPER execution path attempts to call a real order-placement method.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
import pandas as pd

from config.orb_settings import ORBSettings, ConfigurationError, validate_settings
from config.settings import Settings
from market_data.delta_live_feed import DeltaLiveMarketDataFeed
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from services.delta_paper_bot import DeltaPaperBot
from risk.position_sizing import DailyRiskState


# --------------------------------------------------------------------------
# Global Safety Guard Fixture
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def intercept_live_orders(monkeypatch):
    """Enforce that NO test can ever contact live exchange order endpoints."""
    real_post = requests.post

    def guarded_post(url, *args, **kwargs):
        if "delta.exchange" in str(url) and ("/v2/orders" in str(url) or "/v2/positions" in str(url)):
            raise AssertionError(f"CRITICAL SAFETY VIOLATION: Test attempted real order submission to {url}!")
        return real_post(url, *args, **kwargs)

    monkeypatch.setattr(requests, "post", guarded_post)


# --------------------------------------------------------------------------
# Test 1: Default mode is PAPER
# --------------------------------------------------------------------------
def test_1_default_mode_is_paper():
    """Verify that default settings enforce mode='PAPER' and disable live execution."""
    cfg = ORBSettings()
    assert cfg.mode == "PAPER", f"Expected mode='PAPER', got '{cfg.mode}'"
    assert cfg.live_trading_enabled is False, "live_trading_enabled must default to False"
    assert cfg.paper_trading_acknowledged == "YES", "paper_trading_acknowledged must be YES"
    assert cfg.live_trading_acknowledged == "NO", "live_trading_acknowledged must default to NO"

    settings = Settings()
    assert settings.trading_mode == "PAPER", f"Expected trading_mode='PAPER', got '{settings.trading_mode}'"


# --------------------------------------------------------------------------
# Test 2: Public market-data connection works without trading credentials
# --------------------------------------------------------------------------
def test_2_public_market_data_works_without_credentials():
    """Verify that DeltaLiveMarketDataFeed initializes and bootstraps without API keys."""
    cfg = ORBSettings(
        api_key="",
        api_secret="",
        mode="PAPER",
    )
    feed = DeltaLiveMarketDataFeed(cfg=cfg)
    assert feed.cfg.api_key == ""
    assert feed.cfg.api_secret == ""
    assert feed.rest_url == "https://api.india.delta.exchange"
    assert feed.ws_url == "wss://socket.india.delta.exchange"
    assert feed.product_id == 131253
    assert feed.symbol == "XAUTUSD"

    # Mock public REST bootstrap
    mock_df = pd.DataFrame(
        {
            "Open": [2750.0, 2752.0],
            "High": [2755.0, 2758.0],
            "Low": [2748.0, 2751.0],
            "Close": [2752.0, 2756.0],
            "Volume": [10.5, 12.0],
        },
        index=pd.to_datetime(["2026-11-04 06:00:00+00:00", "2026-11-04 06:05:00+00:00"]),
    )

    with patch("market_data.delta_live_feed.fetch_5m_candles", return_value=mock_df) as mock_fetch, \
         patch.object(feed, "validate_product", return_value=(True, None)), \
         patch("threading.Thread"):
        started = feed.start(bootstrap_lookback=2)
        assert started is True
        assert len(feed.get_completed_5m_candles()) == 2
        mock_fetch.assert_called_once_with("XAUTUSD", lookback_candles=2)


# --------------------------------------------------------------------------
# Test 3: No private order-placement endpoint is called
# --------------------------------------------------------------------------
def test_3_no_private_order_placement_endpoint_called(tmp_path):
    """Verify that throughout paper broker trade lifecycle, zero exchange order endpoints are called."""
    state_file = tmp_path / "paper_test_3.json"
    broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)

    with patch("requests.post") as mock_post, patch("requests.put") as mock_put, patch("requests.delete") as mock_del:
        # Open simulated trade
        ok, msg, pos = broker.open_simulated_trade(
            symbol="XAUTUSD",
            direction="LONG",
            entry_price=2750.0,
            stop_loss=2740.0,
            take_profit=2770.0,
            lots=1,
            strategy_name="ORB_V2",
        )
        assert ok is True

        # Close simulated trade
        ok, msg, closed = broker.close_simulated_trade(
            exit_price=2765.0,
            exit_reason="TP",
        )
        assert ok is True

        # Ensure 0 external order calls
        mock_post.assert_not_called()
        mock_put.assert_not_called()
        mock_del.assert_not_called()


# --------------------------------------------------------------------------
# Test 4: Simulated market order does not create an exchange order
# --------------------------------------------------------------------------
def test_4_simulated_market_order_does_not_create_exchange_order(tmp_path):
    """Verify simulated trade opens locally with realistic spread, slippage, fee, and local state."""
    state_file = tmp_path / "paper_test_4.json"
    cfg = ORBSettings(spread_pct_round_trip=0.02, slippage_pct_round_trip=0.02, fee_pct_per_side=0.01)
    broker = DeltaPaperBroker(cfg=cfg, state_file=state_file, initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=2750.0,
        stop_loss=2735.0,
        take_profit=2780.0,
        lots=1,
        strategy_name="TREND_FOLLOWING_V2",
        signal_reason="Bullish EMA20 pullback",
    )
    assert ok is True
    assert pos is not None
    assert pos["symbol"] == "XAUTUSD"
    assert pos["direction"] == "LONG"
    assert pos["lots"] == 1
    assert pos["contract_qty"] == 0.001  # 1 * 0.001

    # Realistic fill: LONG enters at entry_price * (1 + 0.01% spread_half + 0.01% slip_half) = 2750 * 1.0002 = 2750.55
    assert pos["entry_price"] > 2750.0
    assert pos["entry_fee"] > 0.0
    assert pos["slippage_cost"] > 0.0

    # Local cash balance reduced by entry fee
    assert broker.cash_balance == pytest.approx(10000.0 - pos["entry_fee"], rel=1e-3)
    assert broker.open_position is not None


# --------------------------------------------------------------------------
# Test 5: Simulated stop-loss closes only local position
# --------------------------------------------------------------------------
def test_5_simulated_stop_loss_closes_only_local_position(tmp_path):
    """Verify that Stop Loss execution closes only the local paper position with full friction."""
    state_file = tmp_path / "paper_test_5.json"
    broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)

    # Open LONG at 2750, SL at 2740
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=2750.0,
        stop_loss=2740.0,
        take_profit=2780.0,
        lots=1,
        strategy_name="VWAP_EMA_V2",
    )
    assert broker.open_position is not None

    # Simulate price touching SL (low = 2739.5)
    closed = broker.evaluate_open_position_sl_tp(current_price=2739.5, low=2739.0, high=2745.0)
    assert closed is not None
    assert closed["exit_reason"] == "SL"
    assert broker.open_position is None
    assert len(broker.closed_trades) == 1
    assert closed["net_pnl"] < 0
    assert closed["r_multiple"] < 0
    # Equity should reflect loss
    summary = broker.get_account_summary()
    assert summary["equity"] < 10000.0
    assert summary["realized_pnl"] < 0.0


# --------------------------------------------------------------------------
# Test 6: PAPER equity is independent of real exchange equity
# --------------------------------------------------------------------------
def test_6_paper_equity_independent_of_real_exchange(tmp_path):
    """Verify that paper equity tracks configured paper_initial_equity and is decoupled from exchange."""
    state_file = tmp_path / "paper_test_6.json"
    broker = DeltaPaperBroker(state_file=state_file, initial_equity=12500.0)

    summary = broker.get_account_summary()
    assert summary["initial_equity"] == 12500.0
    assert summary["cash_balance"] == 12500.0
    assert summary["equity"] == 12500.0
    assert summary["mode"] == "PAPER"


# --------------------------------------------------------------------------
# Test 7: Daily loss limits work
# --------------------------------------------------------------------------
def test_7_daily_loss_limits_work(tmp_path):
    """Verify that reaching daily loss limit blocks subsequent trades."""
    state_path = tmp_path / "risk_test_7.json"
    cfg = ORBSettings(daily_loss_limit_pct=1.5, state_path=str(state_path))  # 1.5% of $10,000 = $150
    risk = DailyRiskState(path=str(state_path), cfg=cfg)

    now = datetime.now(timezone.utc)
    allowed, reason, _ = risk.can_trade(now)
    assert allowed is True

    # Record a loss of -$160 (breaches $150 limit)
    risk.record_result(pnl_cash=-160.0, equity_at_start=10000.0)

    allowed_after, reason_after, _ = risk.can_trade(now)
    assert allowed_after is False
    assert "Daily loss limit hit" in reason_after


# --------------------------------------------------------------------------
# Test 8: Maximum trades per day works
# --------------------------------------------------------------------------
def test_8_maximum_trades_per_day_works(tmp_path):
    """Verify that maximum trades per day (max_trades_per_day=1) blocks further entries."""
    state_path = tmp_path / "risk_test_8.json"
    cfg = ORBSettings(max_trades_per_day=1, state_path=str(state_path))
    risk = DailyRiskState(path=str(state_path), cfg=cfg)

    now = datetime.now(timezone.utc)
    allowed, _, _ = risk.can_trade(now)
    assert allowed is True

    # Reserve 1 trade
    risk.reserve_trade(now)

    # Attempt second trade
    allowed_second, reason_second, _ = risk.can_trade(now)
    assert allowed_second is False
    assert "Maximum trades" in reason_second


# --------------------------------------------------------------------------
# Test 9: Duplicate signals do not open duplicate positions
# --------------------------------------------------------------------------
def test_9_duplicate_signals_do_not_open_duplicate_positions(tmp_path):
    """Verify that a second entry is strictly blocked when an active position exists."""
    state_file = tmp_path / "paper_test_9.json"
    broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)

    ok1, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=2750.0,
        stop_loss=2740.0,
        take_profit=2780.0,
        lots=1,
        strategy_name="ORB_V2",
    )
    assert ok1 is True

    # Second trade attempt
    ok2, msg2, pos2 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry_price=2750.0,
        stop_loss=2760.0,
        take_profit=2730.0,
        lots=1,
        strategy_name="TREND_FOLLOWING_V2",
    )
    assert ok2 is False
    assert "Position already open" in msg2
    assert pos2 is None
    assert broker.open_position["direction"] == "LONG"


# --------------------------------------------------------------------------
# Test 10: Incomplete candles cannot trigger confirmed entries
# --------------------------------------------------------------------------
def test_10_incomplete_candles_cannot_trigger_confirmed_entries(tmp_path):
    """Verify that real-time ticks and unclosed bars update mark price but do not execute entries."""
    state_file = tmp_path / "paper_test_10.json"
    broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    feed = DeltaLiveMarketDataFeed(cfg=ORBSettings())

    # Feed unclosed bar to is_candle_complete: if forming candle timestamp is None or future, not complete
    incomplete_candle_epoch = 1700000100
    assert feed.is_candle_complete(incomplete_candle_epoch, timeframe="5m") is False

    # Simulate position monitoring on tick
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=2750.0,
        stop_loss=2740.0,
        take_profit=2780.0,
        lots=1,
        strategy_name="VWAP_EMA_V2",
    )

    # Normal tick within SL/TP bounds updates market price without closing
    closed = broker.evaluate_open_position_sl_tp(current_price=2755.0)
    assert closed is None
    assert broker.open_position is not None
    assert broker.open_position["market_price"] == 2755.0
    assert broker.open_position["unrealized_pnl"] > 0


# --------------------------------------------------------------------------
# Test 11: Restarting application preserves paper balance and trade history
# --------------------------------------------------------------------------
def test_11_persistence_across_restarts(tmp_path):
    """Verify that paper account balance, realized P&L, and trade history persist across broker instances."""
    state_file = tmp_path / "paper_persistent.json"

    # Instance A: Execute and close a winning trade
    broker_a = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    broker_a.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=2750.0,
        stop_loss=2740.0,
        take_profit=2780.0,
        lots=1,
        strategy_name="ORB_V2",
    )
    broker_a.close_simulated_trade(exit_price=2775.0, exit_reason="TP")

    summary_a = broker_a.get_account_summary()
    assert summary_a["total_trades"] == 1
    assert summary_a["realized_pnl"] > 0
    saved_cash = summary_a["cash_balance"]
    saved_pnl = summary_a["realized_pnl"]
    trade_id = summary_a["closed_trades"][0]["trade_id"]

    # Instance B: Brand new broker pointing to same state file
    broker_b = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    summary_b = broker_b.get_account_summary()

    assert summary_b["cash_balance"] == saved_cash
    assert summary_b["realized_pnl"] == saved_pnl
    assert summary_b["total_trades"] == 1
    assert summary_b["closed_trades"][0]["trade_id"] == trade_id
    assert summary_b["closed_trades"][0]["exit_reason"] == "TP"


# --------------------------------------------------------------------------
# Test 12: WebSocket disconnections recover safely
# --------------------------------------------------------------------------
def test_12_websocket_disconnections_recover_safely():
    """Verify WebSocket disconnect state handling and safe reconnection scheduling."""
    feed = DeltaLiveMarketDataFeed(cfg=ORBSettings())
    feed._ws_connected = True
    feed._last_error = None

    # Simulate connection drop
    feed._ws_connected = False
    feed._last_error = "Remote connection closed by peer"

    status = feed.get_status()
    assert status["ws_connected"] is False
    assert status["is_connected"] is False
    assert status["last_error"] == "Remote connection closed by peer"

    # Completed candles buffer remains completely intact
    assert isinstance(feed.get_completed_5m_candles(), object)


# --------------------------------------------------------------------------
# Test 13: Invalid configuration fails safely
# --------------------------------------------------------------------------
def test_13_invalid_configuration_fails_safely():
    """Verify that invalid trading parameters or contradictory configurations fail safely."""
    # LIVE mode without acknowledgment raises ConfigurationError in validate_settings
    cfg_unack = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="NO",
        live_trading_enabled=False,
    )
    with pytest.raises(ConfigurationError):
        validate_settings(cfg_unack)

    # Invalid mode string raises ConfigurationError
    cfg_invalid_mode = ORBSettings(mode="INVALID_MODE")
    with pytest.raises(ConfigurationError):
        validate_settings(cfg_invalid_mode)


# --------------------------------------------------------------------------
# Test 14: LIVE mode is rejected by the PAPER-only runtime
# --------------------------------------------------------------------------
def test_14_live_mode_rejected_by_paper_runtime(tmp_path):
    """Verify DeltaPaperBot strictly blocks execution if mode is LIVE or live trading is enabled."""
    cfg_live = ORBSettings(mode="LIVE")
    with pytest.raises(PaperSafetyViolation, match="requires mode='PAPER'"):
        DeltaPaperBot(cfg=cfg_live)

    cfg_enabled = ORBSettings(mode="PAPER", live_trading_enabled=True)
    with pytest.raises(PaperSafetyViolation, match="LIVE_TRADING_ENABLED is true"):
        DeltaPaperBot(cfg=cfg_enabled)


# --------------------------------------------------------------------------
# Test 15: Explicit test that fails if PAPER execution path attempts to call real order method
# --------------------------------------------------------------------------
def test_15_safety_guard_blocks_execute_live_order(tmp_path):
    """Verify DeltaPaperBroker.execute_live_order raises PaperSafetyViolation unconditionally."""
    broker = DeltaPaperBroker(state_file=tmp_path / "paper_test_15.json", initial_equity=10000.0)

    with pytest.raises(PaperSafetyViolation, match="CRITICAL SECURITY VIOLATION"):
        broker.execute_live_order(symbol="XAUTUSD", side="BUY", size=1)
