"""Mock and synthetic market data provider for offline testing and deterministic simulation."""

from datetime import datetime, timedelta
from typing import Optional
import numpy as np
import pandas as pd

from market_data.base import (
    BaseMarketDataProvider,
    MarketDataResult,
    MarketDataMeta,
)


class MockMarketDataProvider(BaseMarketDataProvider):
    """Generates synthetic, deterministic OHLCV candles for unit testing and offline demo."""

    def __init__(self, initial_price: float = 100.0, trend: float = 0.001, volatility: float = 0.015):
        self.initial_price = initial_price
        self.trend = trend
        self.volatility = volatility

    @property
    def provider_name(self) -> str:
        return "Deterministic Mock Provider (Offline Synthetic Series)"

    def get_historical_ohlcv(
        self,
        symbol: str,
        period: str = "6mo",
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> MarketDataResult:
        np.random.seed(42)  # Deterministic seed for reproducible tests
        days = 180
        if period == "1mo":
            days = 30
        elif period == "1y":
            days = 365

        dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(days)]
        prices = [self.initial_price]
        for _ in range(1, days):
            ret = self.trend + np.random.normal(0, self.volatility)
            prices.append(max(1.0, prices[-1] * (1 + ret)))

        prices = np.array(prices)
        opens = prices * (1 + np.random.normal(0, 0.002, days))
        highs = np.maximum(prices, opens) * (1 + abs(np.random.normal(0, 0.005, days)))
        lows = np.minimum(prices, opens) * (1 - abs(np.random.normal(0, 0.005, days)))
        closes = prices
        volumes = np.random.randint(500000, 2000000, days)

        df = pd.DataFrame(
            {
                "Open": opens,
                "High": highs,
                "Low": lows,
                "Close": closes,
                "Volume": volumes,
            },
            index=pd.DatetimeIndex(dates),
        )

        meta = MarketDataMeta(
            symbol=symbol.upper(),
            provider=self.provider_name,
            interval=interval,
            latest_timestamp=str(df.index[-1]),
            is_delayed=False,
            delay_minutes=0,
            is_stale=False,
            warning="Offline Synthetic Data - Not live market quotes.",
            row_count=len(df),
        )
        return MarketDataResult(df=df, meta=meta)

    def get_latest_price(self, symbol: str) -> Optional[float]:
        res = self.get_historical_ohlcv(symbol)
        if res.is_valid:
            return float(res.df["Close"].iloc[-1])
        return self.initial_price
