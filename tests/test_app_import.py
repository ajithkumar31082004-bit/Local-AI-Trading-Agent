"""Test application loading and page rendering imports."""

import pytest


def test_app_import():
    import app
    assert hasattr(app, "main")
    assert hasattr(app, "get_shared_resources")


def test_supervisor_wiring(monkeypatch):
    from agents.trading_supervisor import TradingSupervisor
    from market_data.mock_provider import MockMarketDataProvider
    from agents.ollama_client import OllamaClient

    # Fast offline mock for local LLM so test runs in milliseconds
    monkeypatch.setattr(OllamaClient, "generate_response", lambda self, *args, **kwargs: "Mock quantitative summary.")

    mock_provider = MockMarketDataProvider()
    supervisor = TradingSupervisor(data_provider=mock_provider)
    res = supervisor.run_full_analysis("AAPL", "Moving Average Crossover")
    assert res["success"] is True
    assert "market_research" in res
    assert "technical_analysis" in res
    assert "strategy" in res
    assert "risk_audit" in res
