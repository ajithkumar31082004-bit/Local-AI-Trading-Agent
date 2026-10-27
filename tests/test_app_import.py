"""Test application loading and page rendering imports."""

import pytest


def test_app_import():
    import app
    assert hasattr(app, "main")
    assert hasattr(app, "get_shared_resources")


def test_supervisor_wiring():
    from agents.trading_supervisor import TradingSupervisor
    from market_data.mock_provider import MockMarketDataProvider
    from database.repository import DatabaseRepository

    mock_provider = MockMarketDataProvider()
    supervisor = TradingSupervisor(data_provider=mock_provider)
    res = supervisor.run_full_analysis("AAPL", "Moving Average Crossover")
    assert res["success"] is True
    assert "market_research" in res
    assert "technical_analysis" in res
    assert "strategy" in res
    assert "risk_audit" in res
