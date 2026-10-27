"""NewsAPI Client for financial headlines and market sentiment."""

import logging
from typing import List, Dict, Any, Optional
import requests

from config.settings import settings

logger = logging.getLogger(__name__)


class NewsProvider:
    """Retrieves financial news headlines and computes sentiment summaries using NewsAPI."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key if api_key is not None else settings.newsapi_key
        self.base_url = "https://newsapi.org/v2"

    def get_market_news(self, symbol: str, page_size: int = 5) -> List[Dict[str, Any]]:
        """Fetch latest financial news articles for a given symbol."""
        if not self.api_key:
            return []

        # Strip market suffix for cleaner search (e.g. TCS.NS -> TCS)
        search_term = symbol.split(".")[0]
        params = {
            "q": f"{search_term} stock OR shares OR market",
            "sortBy": "publishedAt",
            "pageSize": page_size,
            "language": "en",
            "apiKey": self.api_key,
        }

        try:
            res = requests.get(f"{self.base_url}/everything", params=params, timeout=6)
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

    def get_sentiment_summary(self, symbol: str) -> Dict[str, Any]:
        """Compute headline sentiment and summary for Market Research Agent."""
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
