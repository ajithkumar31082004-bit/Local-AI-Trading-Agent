"""Unit tests for Paper Broker and SQLite portfolio accounting."""

import pytest
from pathlib import Path
from database.repository import DatabaseRepository
from services.paper_broker import PaperBroker
from services.portfolio_service import PortfolioService


@pytest.fixture
def test_repo(tmp_path: Path):
    db_file = tmp_path / "test_trading.db"
    repo = DatabaseRepository(db_path=db_file)
    repo.reset_account(starting_balance=100000.0)
    return repo


def test_buy_and_sell_cycle(test_repo):
    broker = PaperBroker(repo=test_repo)
    port_service = PortfolioService(repo=test_repo)

    # 1. Buy 50 shares of AAPL @ $150
    ok, msg, trade = broker.execute_order(
        symbol="AAPL",
        side="BUY",
        quantity=50.0,
        current_price=150.0,
        strategy_name="TEST",
    )
    assert ok is True
    assert trade is not None
    assert trade.fee > 0

    account = test_repo.get_account_state()
    assert account["cash_balance"] < 100000.0 - (50 * 150)
    pos = test_repo.get_position("AAPL")
    assert pos is not None
    assert pos.quantity == 50.0

    # 2. Sell 50 shares of AAPL @ $160
    ok, msg, sell_trade = broker.execute_order(
        symbol="AAPL",
        side="SELL",
        quantity=50.0,
        current_price=160.0,
        strategy_name="TEST",
    )
    assert ok is True
    assert sell_trade is not None

    account_after = test_repo.get_account_state()
    # Realized P&L should be positive (~$500 gross minus slippage & fees)
    assert account_after["realized_pnl"] > 400.0

    pos_after = test_repo.get_position("AAPL")
    assert pos_after is None or pos_after.quantity == 0.0


def test_cannot_sell_unowned_stock(test_repo):
    broker = PaperBroker(repo=test_repo)
    ok, msg, trade = broker.execute_order(
        symbol="GOOGL",
        side="SELL",
        quantity=10.0,
        current_price=100.0,
    )
    assert ok is False
    assert "no open position" in msg.lower()


def test_account_reset(test_repo):
    broker = PaperBroker(repo=test_repo)
    broker.execute_order(symbol="MSFT", side="BUY", quantity=10.0, current_price=200.0)
    test_repo.reset_account(starting_balance=50000.0)

    account = test_repo.get_account_state()
    assert account["cash_balance"] == 50000.0
    assert len(test_repo.get_positions()) == 0
    assert len(test_repo.get_trades()) == 0
