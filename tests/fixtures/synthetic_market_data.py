"""Reusable synthetic OHLCV market data fixtures for strategy and regression testing.

Consolidates duplicate candle generation logic across multi-strategy unit tests,
ensuring consistent market regime simulation, boundary test precision, and zero-drift test assertions.
"""

from typing import Optional
import pandas as pd


def create_synthetic_bearish_continuation_df(
    start: str = "2026-10-30 07:00",
    periods: int = 160,
    freq: str = "5min",
    tz: str = "Asia/Kolkata",
    base_close: float = 4100.0,
    slope: float = 0.8,
    base_vol: float = 100000.0,
    continuation_vol: float = 150000.0,
) -> pd.DataFrame:
    """Generate a clean 15m bearish trend with 5m pullback test and bearish rejection continuation."""
    idx = pd.date_range(start, periods=periods, freq=freq, tz=tz)
    closes = [base_close - (i * slope) for i in range(periods)]
    opens = [c + 0.5 for c in closes]
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    vols = [base_vol] * periods

    # Near the end (bars 155-157), price rallies to test 5m EMA20 (pullback)
    closes[156] = closes[155] + 5.0
    highs[156] = closes[156] + 4.0

    # Bar 159 (current bar): Bearish continuation candle with upper rejection wick
    opens[159] = closes[158] + 1.0
    highs[159] = opens[159] + 3.0
    closes[159] = opens[159] - 4.0
    lows[159] = closes[159] - 0.5
    vols[159] = continuation_vol

    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


def create_synthetic_bullish_continuation_df(
    start: str = "2026-10-30 07:00",
    periods: int = 160,
    freq: str = "5min",
    tz: str = "Asia/Kolkata",
    base_close: float = 3800.0,
    slope: float = 0.8,
    base_vol: float = 100000.0,
    continuation_vol: float = 150000.0,
) -> pd.DataFrame:
    """Generate a clean 15m bullish trend with 5m pullback test and bullish rejection continuation."""
    idx = pd.date_range(start, periods=periods, freq=freq, tz=tz)
    closes = [base_close + (i * slope) for i in range(periods)]
    opens = [c - 0.5 for c in closes]
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    vols = [base_vol] * periods

    # Near the end (bars 155-157), price dips to test 5m EMA20 (pullback)
    closes[156] = closes[155] - 5.0
    lows[156] = closes[156] - 4.0

    # Bar 159 (current bar): Bullish continuation candle with lower rejection wick
    opens[159] = closes[158] - 1.0
    lows[159] = opens[159] - 3.0
    closes[159] = opens[159] + 4.0
    highs[159] = closes[159] + 0.5
    vols[159] = continuation_vol

    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )

