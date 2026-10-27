"""Unit tests for NewsProvider and AlphaVantageProvider."""

import pytest
from market_data.news_provider import NewsProvider
from market_data.alpha_vantage_provider import AlphaVantageProvider


def test_news_provider_without_key():
    np = NewsProvider(api_key="")
    assert np.get_market_news("AAPL") == []
    summary = np.get_sentiment_summary("AAPL")
    assert summary["sentiment"] == "Neutral"


def test_alpha_vantage_without_key():
    av = AlphaVantageProvider(api_key="")
    res = av.get_historical_ohlcv("AAPL")
    assert res.is_valid is False
    assert "not configured" in (res.error or "")
