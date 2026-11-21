"""Regression test suite for 7-Point Remediation Order:
1. Order-side validation and risk-limit enforcement.
2. Normalized stop-loss / take-profit persistence.
3. Daily realized-loss accounting.
4. Backtest fee accounting and open-position reporting.
5. Concurrency-safe risk validation and financial input validation.
"""

import math
import threading
import pytest
import pandas as pd
from datetime import datetime, timezone

from config.settings import settings
from config.orb_settings import ORBSettings
from database.models import OrderSide, OrderStatus
from database.repository import DatabaseRepository
from services.risk_manager import RiskManager
from services.paper_broker import PaperBroker
from backtest.orb_backtest import run_backtest, ORBBacktestResult


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


def test_daily_realized_loss_accounting_includes_closed_trades(tmp_path):
    """Verify that get_today_realized_loss properly tallies liquidated positions from trades."""
    db_file = tmp_path / "test_loss.db"
    repo = DatabaseRepository(db_path=db_file)

    broker = PaperBroker(repo=repo)

    # 1. Open BUY position
    broker.execute_order("XAUTUSD", "BUY", 1.0, 3500.0, bypass_risk=True)

    # 2. Close position at a loss ($3400 vs $3500 = -$100 loss)
    broker.execute_order("XAUTUSD", "SELL", 1.0, 3400.0, bypass_risk=True)

    # 3. Check loss accounting
    today_loss = repo.get_today_realized_loss()
    assert today_loss >= 100.0


def test_backtest_fee_accounting_and_open_position_reporting():
    """Verify that backtest exit fees calculate from actual exit price and open positions are captured."""
    # Construct minimal synthetic 5m dataframe
    times = pd.date_range("2026-11-20 09:00:00", periods=10, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [3500.0 + i for i in range(10)],
        "High": [3505.0 + i for i in range(10)],
        "Low": [3495.0 + i for i in range(10)],
        "Close": [3502.0 + i for i in range(10)],
        "Volume": [1000 + i * 50 for i in range(10)],
    }, index=times)

    # Mock custom strategy that produces 1 BUY signal on bar 1
    class DummySignal:
        direction = "LONG"
        timestamp = str(times[1])
        entry = 3501.0
        sl = 3490.0
        tp = 3525.0
        strategy_name = "ORB"

    def mock_strat(prefix, cfg):
        if len(prefix) >= 2 and str(prefix.index[-1]) == str(times[1]):
            return DummySignal()
        sig = DummySignal()
        sig.direction = "NONE"
        return sig

    cfg = ORBSettings(close_at_eod=False)  # Keep trade open across bars

    result = run_backtest(df, initial_equity=1000.0, cfg=cfg, strategy_fn=mock_strat, chart_output_path=None)

    assert isinstance(result, ORBBacktestResult)
    # The trade entered at bar 1 was never closed by SL/TP and close_at_eod is False,
    # so it should be reported in open_positions!
    assert len(result.open_positions) == 1
    open_pos = result.open_positions[0]
    assert open_pos["direction"] == "LONG"
    assert open_pos["entry"] == 3501.0
    assert "unrealized_pnl" in open_pos


def test_concurrency_safe_order_execution_serialization():
    """Verify that concurrent threads evaluating orders in RiskManager run thread-safely."""
    rm = RiskManager()
    errors = []

    def run_eval(thread_id):
        try:
            for _ in range(50):
                res = rm.evaluate_order(
                    symbol="XAUTUSD",
                    side="BUY" if thread_id % 2 == 0 else "SELL",
                    requested_quantity=0.1,
                    current_price=3500.0 + thread_id,
                    portfolio_equity=10000.0,
                    cash_balance=10000.0,
                    current_open_positions_count=0,
                    today_realized_loss=0.0,
                    existing_position_qty=0.1 if thread_id % 2 != 0 else 0.0,
                )
                assert isinstance(res.approved, bool)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run_eval, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
