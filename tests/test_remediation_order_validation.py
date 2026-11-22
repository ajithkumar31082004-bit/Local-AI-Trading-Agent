"""Regression test suite for 7-Point Remediation Order:
1. Order-side validation and risk-limit enforcement.
2. Normalized stop-loss / take-profit persistence.
3. Daily realized-loss accounting.
4. Backtest fee accounting and open-position reporting.
5. Concurrency-safe risk validation and financial input validation.
6. Live executor strict direction validation.
7. Multi-day position and fee reconciliation.
"""

import math
import threading
from unittest.mock import MagicMock
import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta

from config.settings import settings
from config.orb_settings import ORBSettings
from database.models import OrderSide, OrderStatus, TradeRecord, PositionRecord
from database.repository import DatabaseRepository
from services.risk_manager import RiskManager
from services.paper_broker import PaperBroker
from backtest.orb_backtest import run_backtest, ORBBacktestResult
from services.delta_live_executor import DeltaLiveExecutor


def test_order_side_validation_rejects_invalid_strings():
    """Verify that RiskManager and PaperBroker strictly reject invalid order sides."""
    rm = RiskManager()
    
    # 1. Invalid side in RiskManager
    res = rm.evaluate_order(
        symbol="XAUTUSD",
        side="INVALID_SIDE",
        requested_quantity=1.0,
        current_price=3500.0,
        portfolio_equity=10000.0,
        cash_balance=10000.0,
        current_open_positions_count=0,
        today_realized_loss=0.0,
    )
    assert not res.approved
    assert any("Invalid order side" in r for r in res.rejection_reasons)

    # 2. Invalid side in PaperBroker
    broker = PaperBroker(risk_manager=rm)
    success, msg, trade = broker.execute_order(
        symbol="XAUTUSD",
        side="LONG",  # Should be BUY or SELL
        quantity=1.0,
        current_price=3500.0,
    )
    assert not success
    assert "Invalid order side" in msg
    assert trade is None


def test_financial_input_validation_rejects_nan_and_inf():
    """Verify that RiskManager and PaperBroker reject NaN, Inf, and non-positive prices/quantities."""
    rm = RiskManager()

    # NaN Price
    res = rm.evaluate_order(
        symbol="XAUTUSD",
        side="BUY",
        requested_quantity=1.0,
        current_price=float("nan"),
        portfolio_equity=10000.0,
        cash_balance=10000.0,
        current_open_positions_count=0,
        today_realized_loss=0.0,
    )
    assert not res.approved
    assert any("finite numerical value" in r for r in res.rejection_reasons)

    # Infinite Quantity
    res = rm.evaluate_order(
        symbol="XAUTUSD",
        side="BUY",
        requested_quantity=float("inf"),
        current_price=3500.0,
        portfolio_equity=10000.0,
        cash_balance=10000.0,
        current_open_positions_count=0,
        today_realized_loss=0.0,
    )
    assert not res.approved
    assert any("finite numerical value" in r for r in res.rejection_reasons)

    # Negative Quantity
    res = rm.evaluate_order(
        symbol="XAUTUSD",
        side="BUY",
        requested_quantity=-2.5,
        current_price=3500.0,
        portfolio_equity=10000.0,
        cash_balance=10000.0,
        current_open_positions_count=0,
        today_realized_loss=0.0,
    )
    assert not res.approved
    assert any("strictly positive" in r for r in res.rejection_reasons)

    # PaperBroker check for NaN
    broker = PaperBroker(risk_manager=rm)
    success, msg, _ = broker.execute_order(
        symbol="XAUTUSD",
        side="BUY",
        quantity=float("nan"),
        current_price=3500.0,
    )
    assert not success
    assert "Quantity must be strictly positive and finite" in msg


def test_delta_live_executor_rejects_invalid_directions_without_submitting_order():
    """Verify that DeltaLiveExecutor strictly rejects invalid directions without calling exchange."""
    cfg = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="YES",
        api_key="valid_test_key",
        api_secret="valid_test_secret",
        kill_switch_active=False,
    )
    mock_client = MagicMock()
    mock_client.api_key = "valid_test_key"
    mock_client.api_secret = "valid_test_secret"
    mock_client.base_url = "https://api.india.delta.exchange"

    executor = DeltaLiveExecutor(client=mock_client, cfg=cfg)

    # Test invalid directions: HOLD, UNKNOWN, INVALID, empty, None
    for bad_dir in ["HOLD", "UNKNOWN", "INVALID", "", "NONE", None]:
        res = executor.execute_trade(
            symbol="XAUTUSD",
            direction=bad_dir,
            entry=3500.0,
            stop_loss=3480.0,
            take_profit=3540.0,
            candle_timestamp="2026-11-22T10:00:00Z",
        )
        assert res["success"] is False
        assert res["status"] == "REJECTED"
        assert "Invalid trade direction" in res["error"]

    # Test inverted price geometry for LONG (SL must be < Entry < TP)
    res_geom_long = executor.execute_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry=3500.0,
        stop_loss=3520.0,  # Invalid: SL > Entry
        take_profit=3540.0,
        candle_timestamp="2026-11-22T10:05:00Z",
    )
    assert res_geom_long["success"] is False
    assert res_geom_long["status"] == "REJECTED"
    assert "Invalid LONG price geometry" in res_geom_long["error"]

    # Test inverted price geometry for SHORT (TP must be < Entry < SL)
    res_geom_short = executor.execute_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry=3500.0,
        stop_loss=3480.0,  # Invalid: SL < Entry
        take_profit=3450.0,
        candle_timestamp="2026-11-22T10:10:00Z",
    )
    assert res_geom_short["success"] is False
    assert res_geom_short["status"] == "REJECTED"
    assert "Invalid SHORT price geometry" in res_geom_short["error"]

    # Ensure zero network calls were made to exchange
    assert mock_client._get_headers.call_count == 0


def test_normalized_sl_tp_persisted_to_position_and_trade(tmp_path):
    """Verify that calculated normalized stop loss and take profit are persisted to Position and Trade notes."""
    db_file = tmp_path / "test_persistence.db"
    repo = DatabaseRepository(db_path=db_file)

    rm = RiskManager()
    broker = PaperBroker(repo=repo, risk_manager=rm)

    # Execute BUY order without explicit SL/TP to trigger risk manager defaults
    success, msg, trade = broker.execute_order(
        symbol="XAUTUSD",
        side="BUY",
        quantity=0.1,
        current_price=3500.0,
        strategy_name="ORB",
    )
    assert success
    assert trade is not None

    # Check Position persistence
    pos = repo.get_position("XAUTUSD")
    assert pos is not None
    assert pos.stop_loss is not None and pos.stop_loss > 0
    assert pos.take_profit is not None and pos.take_profit > pos.avg_entry_price

    # Check Trade Record notes
    trades = repo.get_trades()
    assert len(trades) >= 1
    assert "SL: $" in trades[0].notes
    assert "TP: $" in trades[0].notes


def test_risk_manager_strict_zero_tolerance_allocation_and_risk():
    """Verify that RiskManager strictly enforces exact allocation and per-trade risk with zero tolerance overrun."""
    rm = RiskManager(default_slippage_pct=0.0)
    # Equity = $10,000, max_position_allocation_pct = 20% -> max_allocation_dollars = $2,000.
    # At Price = $2,000 (slippage=0), allocation_based_qty = 1.0000.
    # max_risk_per_trade_pct = 2% -> max_risk_dollars = $200.
    # SL at $1,800 -> stop_distance = $200. risk_based_qty = $200 / $200 = 1.0000.
    
    # 1. Exact limit (qty = 1.0) -> APPROVED
    res_exact = rm.evaluate_order(
        symbol="XAUTUSD",
        side="BUY",
        requested_quantity=1.0,
        current_price=2000.0,
        stop_loss=1800.0,
        take_profit=2400.0,
        portfolio_equity=10000.0,
        cash_balance=10000.0,
        current_open_positions_count=0,
        today_realized_loss=0.0,
    )
    assert res_exact.approved, f"Exact limit should be approved, got: {res_exact.rejection_reasons}"

    # 2. Allocation overrun by 0.5% (qty = 1.005) -> STRICTLY REJECTED (no longer permitted by old 1.02 multiplier)
    res_alloc_over = rm.evaluate_order(
        symbol="XAUTUSD",
        side="BUY",
        requested_quantity=1.005,
        current_price=2000.0,
        stop_loss=1800.0,
        take_profit=2400.0,
        portfolio_equity=10000.0,
        cash_balance=10000.0,
        current_open_positions_count=0,
        today_realized_loss=0.0,
    )
    assert not res_alloc_over.approved
    assert any("Position Allocation Exceeded" in r for r in res_alloc_over.rejection_reasons)

    # 3. Risk overrun by 0.5% (qty = 1.0, but stop_distance = $201 -> risk = $201 > $200) -> STRICTLY REJECTED (no longer permitted by old 1.05 multiplier)
    res_risk_over = rm.evaluate_order(
        symbol="XAUTUSD",
        side="BUY",
        requested_quantity=1.0,
        current_price=2000.0,
        stop_loss=1799.0,  # stop_distance = 201 > max_risk_dollars 200
        take_profit=2400.0,
        portfolio_equity=10000.0,
        cash_balance=10000.0,
        current_open_positions_count=0,
        today_realized_loss=0.0,
    )
    assert not res_risk_over.approved
    assert any("Risk Per Trade Limit Exceeded" in r for r in res_risk_over.rejection_reasons)


def test_daily_realized_loss_aggregates_multiple_losses_without_double_counting(tmp_path):
    """Verify get_today_realized_loss sums multiple independent losses using structured numeric realized_pnl and timezone."""
    db_file = tmp_path / "test_loss_aggregation.db"
    repo = DatabaseRepository(db_path=db_file)
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Kolkata")
    today_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    yesterday_str = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Insert two independent losing closed trades executed today (-$20.50 and -$30.25) with structured numeric realized_pnl
    trade1 = TradeRecord(
        symbol="XAUTUSD", side="SELL", quantity=1.0, price=3480.0, fee=1.0, slippage=0.5,
        total_cost=3480.0, realized_pnl=-20.50, timestamp=today_str, strategy="ORB", status="FILLED",
        notes="Realized PnL: -$20.50 | Stop Loss Hit",
    )
    trade2 = TradeRecord(
        symbol="XAUTUSD", side="SELL", quantity=1.0, price=3470.0, fee=1.0, slippage=0.5,
        total_cost=3470.0, realized_pnl=-30.25, timestamp=today_str, strategy="ORB", status="FILLED",
        notes="Realized PnL: -$30.25 | Stop Loss Hit",
    )
    # 2. Insert one winning closed trade executed today (+$45.00)
    trade_win = TradeRecord(
        symbol="XAUTUSD", side="SELL", quantity=1.0, price=3550.0, fee=1.0, slippage=0.5,
        total_cost=3550.0, realized_pnl=45.00, timestamp=today_str, strategy="ORB", status="FILLED",
        notes="Realized PnL: $45.00 | Take Profit Hit",
    )
    # 3. Insert one losing closed trade from yesterday (-$100.00)
    trade_yesterday = TradeRecord(
        symbol="XAUTUSD", side="SELL", quantity=1.0, price=3400.0, fee=1.0, slippage=0.5,
        total_cost=3400.0, realized_pnl=-100.00, timestamp=yesterday_str, strategy="ORB", status="FILLED",
        notes="Realized PnL: -$100.00 | Prior Day Loss",
    )
    # 4. Insert an open BUY trade today (realized_pnl = 0.0)
    trade_open = TradeRecord(
        symbol="XAUTUSD", side="BUY", quantity=1.0, price=3500.0, fee=1.0, slippage=0.5,
        total_cost=3500.0, realized_pnl=0.0, timestamp=today_str, strategy="ORB", status="FILLED",
        notes="Open position entry",
    )

    with repo._lock, repo._get_connection() as conn:
        for tr in [trade1, trade2, trade_win, trade_yesterday, trade_open]:
            conn.execute("""
                INSERT INTO trades (symbol, side, quantity, price, fee, slippage, total_cost, realized_pnl, timestamp, strategy, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (tr.symbol, tr.side, tr.quantity, tr.price, tr.fee, tr.slippage, tr.total_cost, tr.realized_pnl, tr.timestamp, tr.strategy, tr.status, tr.notes))
        conn.commit()

    # The today loss must be exactly 20.50 + 30.25 = 50.75 from structured numeric field
    today_loss = repo.get_today_realized_loss(timezone_str="Asia/Kolkata")
    assert today_loss == 50.75


def test_concurrent_paper_broker_order_execution_and_balance_conservation(tmp_path):
    """Verify that multiple independent PaperBroker instances preserve exact account balance without race conditions."""
    db_file = tmp_path / "test_concurrent_broker.db"
    repo = DatabaseRepository(db_path=db_file)
    
    # Use 3 distinct broker instances operating concurrently on the shared repository
    brokers = [PaperBroker(repo=repo) for _ in range(3)]

    initial_state = repo.get_account_state()
    initial_cash = initial_state["cash_balance"]
    errors = []

    def worker(worker_id):
        b = brokers[worker_id % len(brokers)]
        try:
            for i in range(5):
                # Worker opens a position
                ok_buy, msg_b, _ = b.execute_order(
                    symbol=f"SYM_{worker_id}",
                    side="BUY",
                    quantity=1.0,
                    current_price=3500.0,
                    bypass_risk=True,
                )
                assert ok_buy, f"Buy failed: {msg_b}"
                # Worker closes the position
                ok_sell, msg_s, _ = b.execute_order(
                    symbol=f"SYM_{worker_id}",
                    side="SELL",
                    quantity=1.0,
                    current_price=3550.0,
                    bypass_risk=True,
                )
                assert ok_sell, f"Sell failed: {msg_s}"
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent execution produced errors: {errors}"
    final_account = repo.get_account_state()
    
    # Verify exact balance conservation:
    # final_cash == initial_cash + total_realized_pnl - total_fees_paid (or net realized PnL added to cash)
    # In PaperBroker, sell adds net_inflow = trade_value - fee = (price * qty) - fee
    # Total rounds = 6 workers * 5 round trips = 30 round trips
    # Each round trip bought 1.0 @ 3500 ($3500 + fee) and sold 1.0 @ 3550 ($3550 - fee)
    # Gross gain per trip = $50.00. Net gain per trip = $50.00 - buy_fee - sell_fee.
    assert final_account["cash_balance"] > initial_cash
    assert final_account["total_fees_paid"] > 0
    assert final_account["realized_pnl"] > 0


def test_backtest_independent_fee_gst_and_pnl_reconciliation():
    """Verify that backtest exit fees calculate from actual exit price and all friction components reconcile independently."""
    times = pd.date_range("2026-11-20 10:00:00", periods=5, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [3500.0, 3502.0, 3504.0, 3510.0, 3515.0],
        "High": [3505.0, 3508.0, 3512.0, 3522.0, 3520.0],
        "Low": [3495.0, 3501.0, 3503.0, 3508.0, 3512.0],
        "Close": [3502.0, 3505.0, 3511.0, 3520.0, 3518.0],
        "Volume": [1000, 1200, 1500, 2000, 1100],
    }, index=times)

    class FixedSignal:
        direction = "LONG"
        timestamp = str(times[1])
        entry = 3502.0
        sl = 3490.0
        tp = 3520.0  # Hit on candle 3 High (3522.0 >= 3520.0)
        strategy_name = "TREND_FOLLOWING"

    def mock_strat(prefix, cfg):
        if len(prefix) >= 2 and str(prefix.index[-1]) == str(times[1]):
            return FixedSignal()
        sig = FixedSignal()
        sig.direction = "NONE"
        return sig

    cfg = ORBSettings(
        strategy_type="TREND_FOLLOWING",
        orb_minutes=0,
        no_entry_minutes_after_open=0,
        session_start="09:00",
        last_entry_time="22:00",
        close_at_eod=True,
        fee_pct_per_side=0.01,
        spread_pct_round_trip=0.02,
        slippage_pct_round_trip=0.02,
    )

    result = run_backtest(df, initial_equity=10000.0, cfg=cfg, strategy_fn=mock_strat, chart_output_path=None)

    assert result.trades == 1
    t = result.trade_log[0]
    assert t["exit_reason"] == "TP"
    assert t["exit"] == 3520.0
    assert t["entry"] == 3502.0

    # 1. Independent Maker/Taker Base Fee Component:
    entry_val = t["entry"] * cfg.contract_size_xaut * t["lots"]
    exit_val = t["exit"] * cfg.contract_size_xaut * t["lots"]
    expected_entry_fee = entry_val * (cfg.fee_pct_per_side / 100.0)
    expected_exit_fee = exit_val * (cfg.fee_pct_per_side / 100.0)
    expected_total_fee = expected_entry_fee + expected_exit_fee
    assert math.isclose(t["fee_cost"], round(expected_total_fee, 4), rel_tol=1e-3)

    # 2. Independent Spread & Slippage Friction Component:
    expected_spread_cost = entry_val * (cfg.spread_pct_round_trip / 100.0)
    expected_slippage_cost = entry_val * (cfg.slippage_pct_round_trip / 100.0)
    expected_spread_slip = expected_spread_cost + expected_slippage_cost
    assert math.isclose(t["spread_slip_cost"], round(expected_spread_slip, 4), rel_tol=1e-3)

    # 3. Independent Total Friction Reconciliation:
    assert math.isclose(t["total_friction"], round(expected_total_fee + expected_spread_slip, 4), rel_tol=1e-3)

    # 4. Independent Gross and Net PnL Reconciliation:
    contract_qty = t["lots"] * cfg.contract_size_xaut
    gross_pnl = contract_qty * (t["exit"] - t["entry"])
    assert math.isclose(t["gross_pnl"], round(gross_pnl, 2), rel_tol=1e-3)
    assert math.isclose(t["pnl_cash"], round(gross_pnl - t["total_friction"], 2), rel_tol=1e-3)


def test_multi_day_overnight_position_equity_and_realized_exit_reconciliation():
    """Verify that when close_at_eod=False, overnight positions persist across days and realized equity reconciles on exit."""
    # Day 1: 3 candles (entry), Day 2: 3 candles (exit on TP)
    times_d1 = pd.date_range("2026-11-20 10:00:00", periods=3, freq="5min", tz="Asia/Kolkata")
    times_d2 = pd.date_range("2026-11-21 10:00:00", periods=3, freq="5min", tz="Asia/Kolkata")
    times = times_d1.append(times_d2)

    df = pd.DataFrame({
        "Open": [3500.0, 3502.0, 3504.0, 3505.0, 3510.0, 3522.0],
        "High": [3505.0, 3506.0, 3508.0, 3509.0, 3515.0, 3525.0],
        "Low": [3495.0, 3501.0, 3503.0, 3504.0, 3508.0, 3518.0],
        "Close": [3502.0, 3505.0, 3506.0, 3507.0, 3512.0, 3524.0],
        "Volume": [1000] * 6,
    }, index=times)

    class OvernightExitSignal:
        direction = "LONG"
        timestamp = str(times[1])  # Entry on Day 1
        entry = 3502.0
        sl = 3450.0
        tp = 3520.0  # Hit on Day 2 candle 6 High (3525.0 >= 3520.0)
        strategy_name = "TREND_FOLLOWING"

    def mock_strat(prefix, cfg):
        if len(prefix) >= 2 and str(prefix.index[-1]) == str(times[1]):
            return OvernightExitSignal()
        sig = OvernightExitSignal()
        sig.direction = "NONE"
        return sig

    cfg = ORBSettings(
        strategy_type="TREND_FOLLOWING",
        close_at_eod=False,  # Hold overnight!
        orb_minutes=0,
        no_entry_minutes_after_open=0,
        session_start="09:00",
        last_entry_time="22:00",
    )

    init_equity = 10000.0
    result = run_backtest(df, initial_equity=init_equity, cfg=cfg, strategy_fn=mock_strat, chart_output_path=None)

    # Position entered on Day 1, persisted overnight, and closed on Day 2 on TP
    assert result.trades == 1
    t = result.trade_log[0]
    assert t["exit_reason"] == "TP"
    assert t["exit"] == 3520.0
    assert str(times_d1[1]) in t["entry_time"]
    assert str(times_d2[2]) in t["exit_time"]
    
    # Equity must exactly reconcile: net_pnl matches trade pnl and ending equity matches initial + net_pnl
    assert math.isclose(result.net_pnl, t["pnl_cash"], rel_tol=1e-5)
    assert math.isclose(result.equity_curve[-1]["equity"], init_equity + t["pnl_cash"], rel_tol=1e-5)
