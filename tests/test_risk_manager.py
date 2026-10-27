"""Unit tests for Risk Management rules and sizing calculations."""

import pytest
from services.risk_manager import RiskManager


def test_position_sizing_and_risk_budget():
    rm = RiskManager(
        max_risk_per_trade_pct=2.0,
        max_position_allocation_pct=20.0,
        transaction_fee_pct=0.1,
        default_slippage_pct=0.05,
    )

    portfolio_equity = 100000.0
    cash_balance = 50000.0
    current_price = 100.0
    stop_loss = 95.0  # Stop distance is $5.00

    # Risk budget = 2% of 100k = $2,000.
    # Risk-based size = 2000 / 5 = 400 shares.
    # Allocation limit = 20% of 100k = $20,000 / $100 = 200 shares.
    # Therefore max allowed quantity is bounded by allocation: ~200 shares.

    res = rm.evaluate_order(
        symbol="AAPL",
        side="BUY",
        requested_quantity=150.0,
        current_price=current_price,
        portfolio_equity=portfolio_equity,
        cash_balance=cash_balance,
        current_open_positions_count=1,
        today_realized_loss=0.0,
        stop_loss=stop_loss,
    )

    assert res.approved is True
    assert res.effective_price > current_price  # Slippage applied
    assert res.estimated_fee > 0


def test_insufficient_cash_rejection():
    rm = RiskManager()
    res = rm.evaluate_order(
        symbol="AAPL",
        side="BUY",
        requested_quantity=100.0,
        current_price=150.0,  # Value $15,000
        portfolio_equity=100000.0,
        cash_balance=500.0,   # Only $500 available
        current_open_positions_count=1,
        today_realized_loss=0.0,
    )
    assert res.approved is False
    assert any("Insufficient Cash" in r for r in res.rejection_reasons)


def test_max_daily_loss_enforcement():
    rm = RiskManager(max_daily_loss_pct=5.0)
    res = rm.evaluate_order(
        symbol="AAPL",
        side="BUY",
        requested_quantity=10.0,
        current_price=100.0,
        portfolio_equity=100000.0,
        cash_balance=50000.0,
        current_open_positions_count=1,
        today_realized_loss=6000.0,  # 6k exceeds 5k limit (5%)
    )
    assert res.approved is False
    assert any("Daily Loss Limit Reached" in r for r in res.rejection_reasons)


def test_max_open_positions_enforcement():
    rm = RiskManager(max_open_positions=3)
    res = rm.evaluate_order(
        symbol="NEW_STOCK",
        side="BUY",
        requested_quantity=10.0,
        current_price=100.0,
        portfolio_equity=100000.0,
        cash_balance=50000.0,
        current_open_positions_count=3,  # Already at limit
        today_realized_loss=0.0,
    )
    assert res.approved is False
    assert any("Max Open Positions" in r for r in res.rejection_reasons)
