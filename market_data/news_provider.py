"""NewsAPI Client for gold & macro market headlines and high-impact event blackout filter."""

from datetime import datetime, timezone, timedelta
import logging
from typing import List, Dict, Any, Optional, Tuple
import requests

from config.settings import settings

logger = logging.getLogger(__name__)

GOLD_SYMBOLS = {"XAUUSD", "XAUTUSD", "XAU", "XAUT", "GC=F", "GOLD"}
GOLD_MACRO_QUERY = "gold price OR Federal Reserve OR CPI OR dollar index OR FOMC"


def is_news_blackout(
    current_time: Optional[datetime] = None,
    blackout_windows: Optional[List[str]] = None,
    buffer_minutes: int = 30,
) -> Tuple[bool, str]:
    """Check if current time falls within ±buffer_minutes of any configured high-impact event (CPI, NFP, FOMC).
    
    blackout_windows contains ISO-formatted timestamps of scheduled events.
    Returns (is_blacked_out: bool, reason: str).
    """
    if not blackout_windows:
        return False, "No blackout windows configured."

    now_dt = current_time or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_utc = now_dt.astimezone(timezone.utc)

    buffer_delta = timedelta(minutes=buffer_minutes)

    for event_str in blackout_windows:
        try:
            event_dt = datetime.fromisoformat(event_str)
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
            event_utc = event_dt.astimezone(timezone.utc)

            start_window = event_utc - buffer_delta
            end_window = event_utc + buffer_delta

            if start_window <= now_utc <= end_window:
                remaining = (end_window - now_utc).total_seconds() / 60.0
                return True, (
                    f"NO TRADE: Trading blacked out within ±{buffer_minutes}m of high-impact macro event "
                    f"at {event_utc.strftime('%Y-%m-%d %H:%M UTC')} ({remaining:.1f}m remaining)."
                )
        except Exception as exc:
            logger.warning("Could not parse blackout window timestamp '%s': %s", event_str, exc)
            continue

    return False, "No active macro news blackout."


class NewsProvider:
    """Retrieves financial and gold headlines and computes sentiment summaries using NewsAPI.
    
    Security: Always transmits API key via HTTP headers (X-Api-Key), never in URL parameters.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key if api_key is not None else settings.newsapi_key
        self.base_url = "https://newsapi.org/v2"

    def get_market_news(self, symbol: str = "XAUTUSD", page_size: int = 5) -> List[Dict[str, Any]]:
        """Fetch latest financial news articles with specialized macro queries for Gold."""
        if not self.api_key:
            return []

        clean_symbol = symbol.upper().strip()
        if clean_symbol in GOLD_SYMBOLS or "XAU" in clean_symbol:
            query = GOLD_MACRO_QUERY
        else:
            search_term = symbol.split(".")[0]
            query = f"{search_term} stock OR shares OR market"

        params = {
            "q": query,
            "sortBy": "publishedAt",
            "pageSize": page_size,
            "language": "en",
        }
        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "LocalAITradingAgent/1.0",
        }

        try:
            res = requests.get(f"{self.base_url}/everything", params=params, headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                articles = data.get("articles", [])
                cleaned = []
                for a in articles:
                    cleaned.append({
                        "title": a.get("title", ""),
                        "description": a.get("description", "") or "",
                        "source": a.get("source", {}).get("name", "News"),
                        "published_at": a.get("publishedAt", "")[:10],
                        "url": a.get("url", ""),
                    })
                return cleaned
            logger.warning("NewsAPI error HTTP %s: %s", res.status_code, res.text[:100])
            return []
        except Exception as e:
            logger.warning("Error fetching news for %s: %s", symbol, e)
            return []

    def get_sentiment_summary(self, symbol: str = "XAUTUSD") -> Dict[str, Any]:
        """Compute headline sentiment and summary for info panels."""
        articles = self.get_market_news(symbol, page_size=5)
        if not articles:
            return {"symbol": symbol, "sentiment": "Neutral", "score": 0.0, "articles": []}

        # Rule-based sentiment analysis on headlines
        bullish_words = {"gain", "jump", "surge", "record", "growth", "buy", "profit", "bull", "upgrade", "rise", "soar", "high"}
        bearish_words = {"drop", "fall", "slide", "crash", "loss", "sell", "bear", "downgrade", "decline", "plunge", "low", "risk"}

        score = 0
        for a in articles:
            text = (a["title"] + " " + a["description"]).lower()
            bull_hits = sum(1 for w in bullish_words if w in text)
            bear_hits = sum(1 for w in bearish_words if w in text)
            score += (bull_hits - bear_hits)

        sentiment = "Bullish" if score > 1 else "Bearish" if score < -1 else "Neutral"
        return {
            "symbol": symbol,
            "sentiment": sentiment,
            "score": score,
            "article_count": len(articles),
            "articles": articles,
        }
