"""Alpha Vantage Market Data Provider."""

import logging
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
import pandas as pd
import requests

from config.settings import settings
from market_data.base import (
    BaseMarketDataProvider,
    MarketDataResult,
    MarketDataMeta,
)

logger = logging.getLogger(__name__)


class AlphaVantageProvider(BaseMarketDataProvider):
    """Retrieves and formats OHLCV data using the Alpha Vantage REST API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key if api_key is not None else settings.alpha_vantage_api_key
        self.base_url = "https://www.alphavantage.co/query"

    @property
    def provider_name(self) -> str:
        return "Alpha Vantage (REST Data Feed)"

    def get_historical_ohlcv(
        self,
        symbol: str,
        period: str = "6mo",
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> MarketDataResult:
        clean_symbol = symbol.strip().upper()
        if not self.api_key:
            return MarketDataResult(
                df=pd.DataFrame(),
                meta=MarketDataMeta(symbol=clean_symbol, provider=self.provider_name, interval=interval),
                error="Alpha Vantage API key is not configured.",
            )

        # Alpha Vantage free tier requires outputsize=compact (returns latest 100 trading days)
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": clean_symbol,
            "outputsize": "compact",
            "datatype": "json",
            "apikey": self.api_key,
        }

        try:
            res = requests.get(self.base_url, params=params, timeout=10)
            if res.status_code != 200:
                return MarketDataResult(
                    df=pd.DataFrame(),
                    meta=MarketDataMeta(symbol=clean_symbol, provider=self.provider_name, interval=interval),
                    error=f"Alpha Vantage HTTP error {res.status_code}",
                )

            data = res.json()
            time_series = data.get("Time Series (Daily)")
            if not time_series:
                msg = data.get("Note") or data.get("Information") or data.get("Error Message") or "No daily series returned."
                return MarketDataResult(
                    df=pd.DataFrame(),
                    meta=MarketDataMeta(symbol=clean_symbol, provider=self.provider_name, interval=interval),
                    error=f"Alpha Vantage: {msg}",
                )

            # Parse JSON dictionary into DataFrame
            records = []
            for date_str, values in time_series.items():
                records.append({
                    "Date": pd.to_datetime(date_str),
                    "Open": float(values["1. open"]),
                    "High": float(values["2. high"]),
                    "Low": float(values["3. low"]),
                    "Close": float(values["4. close"]),
                    "Volume": float(values["5. volume"]),
                })

            df = pd.DataFrame(records).set_index("Date").sort_index()

            # Filter period
            if period == "1mo":
                df = df.iloc[-22:]
            elif period == "3mo":
                df = df.iloc[-65:]
            elif period == "6mo":
                df = df.iloc[-130:]
            elif period == "1y":
                df = df.iloc[-252:]

            meta = MarketDataMeta(
                symbol=clean_symbol,
                provider=self.provider_name,
                interval=interval,
                latest_timestamp=str(df.index[-1]),
                is_delayed=True,
                row_count=len(df),
            )
            return MarketDataResult(df=df, meta=meta)

        except Exception as e:
            logger.error("Alpha Vantage fetch error for %s: %s", clean_symbol, e)
            return MarketDataResult(
                df=pd.DataFrame(),
                meta=MarketDataMeta(symbol=clean_symbol, provider=self.provider_name, interval=interval),
                error=str(e),
            )

    def get_latest_price(self, symbol: str) -> Optional[float]:
        res = self.get_historical_ohlcv(symbol, period="1mo")
        if res.is_valid:
            return float(res.df["Close"].iloc[-1])
        return None
