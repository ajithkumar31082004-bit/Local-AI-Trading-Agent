"""Unit tests for NewsProvider, Gold Macro queries, Header Security, and News Blackout filters."""

from datetime import datetime, timezone, timedelta
import pytest
from market_data.news_provider import NewsProvider, is_news_blackout, GOLD_MACRO_QUERY
from market_data.alpha_vantage_provider import AlphaVantageProvider
from journal.orb_journal import send_telegram_alert, ORBJournal
from strategy.orb_ema_volume import Signal


class MockResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def test_news_provider_without_key():
    np = NewsProvider(api_key="")
    assert np.get_market_news("AAPL") == []
    summary = np.get_sentiment_summary("AAPL")
    assert summary["sentiment"] == "Neutral"


def test_news_provider_header_security_and_gold_query(monkeypatch):
    captured_requests = []

    def fake_get(url, params=None, headers=None, timeout=6):
        captured_requests.append({"url": url, "params": params, "headers": headers})
        return MockResponse({
            "status": "ok",
            "articles": [
                {
                    "title": "Gold surges on Fed rate cut expectations",
                    "description": "Bullish momentum in gold prices.",
                    "source": {"name": "Reuters"},
                    "publishedAt": "2026-03-01T10:00:00Z",
                    "url": "https://example.com/gold",
                }
            ]
        })

    monkeypatch.setattr("market_data.news_provider.requests.get", fake_get)

    np = NewsProvider(api_key="test_secret_key_12345")
    articles = np.get_market_news("XAUTUSD")

    assert len(captured_requests) == 1
    req = captured_requests[0]

    # Security check: API key must be in headers, NEVER in query params
    assert req["headers"]["X-Api-Key"] == "test_secret_key_12345"
    assert "apiKey" not in req["params"]

    # Query check: Gold/macro query must be used for XAUTUSD
    assert req["params"]["q"] == GOLD_MACRO_QUERY
    assert len(articles) == 1
    assert "surges" in articles[0]["title"]


def test_news_provider_stock_query(monkeypatch):
    captured = []
    def fake_get(url, params=None, headers=None, timeout=6):
        captured.append(params)
        return MockResponse({"status": "ok", "articles": []})

    monkeypatch.setattr("market_data.news_provider.requests.get", fake_get)
    np = NewsProvider(api_key="test_key")
    np.get_market_news("AAPL")

    assert len(captured) == 1
    assert "AAPL stock OR shares OR market" in captured[0]["q"]


def test_is_news_blackout_filter():
    fomc_time = "2026-03-15T18:00:00+00:00"
    windows = [fomc_time]

    # Exactly during event
    cur = datetime.fromisoformat(fomc_time)
    blocked, reason = is_news_blackout(current_time=cur, blackout_windows=windows, buffer_minutes=30)
    assert blocked is True
    assert "NO TRADE" in reason
    assert "blacked out" in reason

    # 15 minutes before event (within 30m buffer)
    cur_before = cur - timedelta(minutes=15)
    blocked, reason = is_news_blackout(current_time=cur_before, blackout_windows=windows, buffer_minutes=30)
    assert blocked is True

    # 45 minutes before event (outside 30m buffer)
    cur_outside = cur - timedelta(minutes=45)
    blocked, reason = is_news_blackout(current_time=cur_outside, blackout_windows=windows, buffer_minutes=30)
    assert blocked is False

    # Empty blackout list
    blocked, _ = is_news_blackout(current_time=cur, blackout_windows=[])
    assert blocked is False


def test_telegram_alert_mock(monkeypatch, tmp_path):
    sent = []
    def fake_post(url, json=None, timeout=5):
        sent.append({"url": url, "json": json})
        return MockResponse({"ok": True}, status_code=200)

    monkeypatch.setattr("journal.orb_journal.requests.post", fake_post)

    journal = ORBJournal(path=tmp_path / "test_j.sqlite3")
    sig = Signal(
        direction="LONG",
        entry=2650.0,
        sl=2642.0,
        tp=2668.0,
        r_multiple=2.25,
        reason="Breakout + Retest confirmed",
        checklist={"orb_set": True, "retest": True},
        timestamp=datetime.now(timezone.utc).isoformat(),
        orb_high=2645.0,
        orb_low=2630.0,
    )

    # When Telegram credentials are not configured, nothing sent
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    journal.log("signal", sig)
    assert len(sent) == 0

    # When Telegram credentials are provided, alert is sent
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    journal.log("signal", sig)
    assert len(sent) == 1
    assert "api.telegram.org/bot123456:ABC-DEF/sendMessage" in sent[0]["url"]
    assert "987654321" == sent[0]["json"]["chat_id"]
    assert "LONG" in sent[0]["json"]["text"]


def test_alpha_vantage_without_key():
    av = AlphaVantageProvider(api_key="")
    res = av.get_historical_ohlcv("AAPL")
    assert res.is_valid is False
    assert "not configured" in (res.error or "")
