"""Yahoo Finance Market Data Provider with validation and caching."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    yf = None

from market_data.base import (
    BaseMarketDataProvider,
    MarketDataResult,
    MarketDataMeta,
)

logger = logging.getLogger(__name__)


class YFinanceProvider(BaseMarketDataProvider):
    """Retrieves and validates market data using Yahoo Finance."""

    def __init__(self, cache_ttl_minutes: int = 5):
        self._cache: Dict[str, Tuple[datetime, MarketDataResult]] = {}
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)

    @property
    def provider_name(self) -> str:
        return "Yahoo Finance (Free Public Feed - Delayed ~15m)"

    def _validate_and_clean_df(self, raw_df: pd.DataFrame, symbol: str) -> Tuple[pd.DataFrame, Optional[str]]:
        """Validate columns, drop duplicates, handle timezone and missing candles."""
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(), f"No market data returned for symbol '{symbol}'."

        df = raw_df.copy()

        # Handle MultiIndex columns if returned by newer yfinance versions
        if isinstance(df.columns, pd.MultiIndex):
            # Flatten or select the first level
            try:
                df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
            except Exception:
                pass

        required_cols = ["Open", "High", "Low", "Close", "Volume"]
        for col in required_cols:
            if col not in df.columns:
                return pd.DataFrame(), f"Missing required price column '{col}' in provider response."

        # Ensure index is datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df.index = pd.to_datetime(df.index)
            except Exception as e:
                return pd.DataFrame(), f"Invalid date index format: {e}"

        # Sort index & remove duplicate timestamps
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()

        # Convert numeric columns safely
        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Forward fill and then drop any residual NaNs
        df = df.ffill().dropna(subset=["Close", "Open", "High", "Low"])

        if df.empty or len(df) < 5:
            return pd.DataFrame(), f"Insufficient valid trading candles for '{symbol}' ({len(df)} candles found)."

        # Ensure high is at least max(open, close) and low is min(open, close)
        df["High"] = np.maximum(df["High"], np.maximum(df["Open"], df["Close"]))
        df["Low"] = np.minimum(df["Low"], np.minimum(df["Open"], df["Close"]))
        df["Volume"] = df["Volume"].fillna(0).clip(lower=0)

        return df, None

    def get_historical_ohlcv(
        self,
        symbol: str,
        period: str = "6mo",
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> MarketDataResult:
        """Fetch historical candles with local in-memory caching and strict validation."""
        clean_symbol = symbol.strip().upper()
        cache_key = f"{clean_symbol}_{period}_{interval}_{start}_{end}"

        # Check cache
        if cache_key in self._cache:
            cached_time, cached_res = self._cache[cache_key]
            if datetime.now() - cached_time < self.cache_ttl:
                return cached_res

        if yf is None:
            return MarketDataResult(
                df=pd.DataFrame(),
                meta=MarketDataMeta(symbol=clean_symbol, provider=self.provider_name, interval=interval),
                error="yfinance package is not installed."
            )

        try:
            ticker = yf.Ticker(clean_symbol)
            if start and end:
                raw_df = ticker.history(start=start, end=end, interval=interval, auto_adjust=True)
            else:
                raw_df = ticker.history(period=period, interval=interval, auto_adjust=True)

            cleaned_df, error_msg = self._validate_and_clean_df(raw_df, clean_symbol)

            if error_msg or cleaned_df.empty:
                return MarketDataResult(
                    df=pd.DataFrame(),
                    meta=MarketDataMeta(
                        symbol=clean_symbol,
                        provider=self.provider_name,
                        interval=interval,
                        warning=error_msg,
                    ),
                    error=error_msg or "Failed to retrieve valid OHLCV candles.",
                )

            latest_ts = str(cleaned_df.index[-1])
            meta = MarketDataMeta(
                symbol=clean_symbol,
                provider=self.provider_name,
                interval=interval,
                latest_timestamp=latest_ts,
                is_delayed=True,
                delay_minutes=15,
                is_stale=False,
                warning="Market data from public feeds is typically delayed by 15-20 minutes. Do not treat as real-time tick data.",
                row_count=len(cleaned_df),
            )

            result = MarketDataResult(df=cleaned_df, meta=meta)
            self._cache[cache_key] = (datetime.now(), result)
            return result

        except Exception as exc:
            logger.error("Error fetching market data for %s: %s", clean_symbol, exc)
            return MarketDataResult(
                df=pd.DataFrame(),
                meta=MarketDataMeta(
                    symbol=clean_symbol,
                    provider=self.provider_name,
                    interval=interval,
                    warning=str(exc),
                ),
                error=f"Error contacting market data provider: {exc}",
            )

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Fetch the most recent observed close/last price."""
        res = self.get_historical_ohlcv(symbol=symbol, period="5d", interval="1d")
        if res.is_valid:
            return float(res.df["Close"].iloc[-1])
        return None
