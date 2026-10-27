"""Deterministic technical analysis indicators computed in pure Python using pandas and numpy."""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class IndicatorSnapshot:
    """Latest computed indicator values for strategy evaluation and reporting."""
    price: float
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    atr_14: Optional[float] = None
    volume: float = 0.0
    volume_sma_20: Optional[float] = None
    volume_ratio: Optional[float] = None
    support: Optional[float] = None
    resistance: Optional[float] = None
    trend_description: str = "Neutral"


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    if len(series) < period:
        return pd.Series(index=series.index, dtype=float)
    return series.rolling(window=period, min_periods=period).mean()


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    if len(series) < period:
        return pd.Series(index=series.index, dtype=float)
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's RSI)."""
    if len(series) < period + 1:
        return pd.Series(index=series.index, dtype=float)

    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's exponential smoothing
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Where loss is 0 and gain > 0, RSI is 100
    rsi = rsi.fillna(100.0 * (avg_gain > 0))
    return rsi


def compute_macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Moving Average Convergence Divergence (MACD, Signal, Histogram)."""
    fast_ema = compute_ema(series, fast_period)
    slow_ema = compute_ema(series, slow_period)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    macd_hist = macd_line - signal_line
    return macd_line, signal_line, macd_hist


def compute_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands (Upper, Middle, Lower)."""
    middle = compute_sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = middle + (std * num_std)
    lower = middle - (std * num_std)
    return upper, middle, lower


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range (ATR)."""
    if len(close) < period + 1:
        return pd.Series(index=close.index, dtype=float)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr


def compute_support_resistance(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 20,
) -> Tuple[float, float]:
    """Calculate support and resistance from recent rolling swing points."""
    if len(close) < window:
        return float(low.min()), float(high.max())

    recent_highs = high.iloc[-window:]
    recent_lows = low.iloc[-window:]
    support = float(recent_lows.min())
    resistance = float(recent_highs.max())
    return support, resistance


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Enrich an OHLCV DataFrame with all standard technical indicators."""
    if df.empty or len(df) < 5:
        return df

    out = df.copy()
    close = out["Close"]
    high = out["High"]
    low = out["Low"]
    volume = out["Volume"]

    # Moving Averages
    out["SMA_20"] = compute_sma(close, 20)
    out["SMA_50"] = compute_sma(close, 50)
    out["EMA_12"] = compute_ema(close, 12)
    out["EMA_26"] = compute_ema(close, 26)

    # Momentum
    out["RSI_14"] = compute_rsi(close, 14)
    macd, signal, hist = compute_macd(close, 12, 26, 9)
    out["MACD"] = macd
    out["MACD_Signal"] = signal
    out["MACD_Hist"] = hist

    # Volatility
    bb_upper, bb_middle, bb_lower = compute_bollinger_bands(close, 20, 2.0)
    out["BB_Upper"] = bb_upper
    out["BB_Middle"] = bb_middle
    out["BB_Lower"] = bb_lower
    out["ATR_14"] = compute_atr(high, low, close, 14)

    # Volume
    out["Volume_SMA_20"] = compute_sma(volume, 20)
    out["Volume_Ratio"] = (volume / out["Volume_SMA_20"].replace(0, np.nan)).fillna(1.0)

    return out


def get_latest_indicators(df: pd.DataFrame) -> Optional[IndicatorSnapshot]:
    """Extract the most recent computed indicator snapshot from an enriched DataFrame."""
    if df.empty or len(df) < 5:
        return None

    enriched = add_all_indicators(df)
    last = enriched.iloc[-1]
    last_price = float(last["Close"])

    # Support / Resistance from last 20 candles
    support, resistance = compute_support_resistance(
        enriched["High"], enriched["Low"], enriched["Close"], window=20
    )

    # Trend determination
    ema_12 = float(last["EMA_12"]) if pd.notna(last.get("EMA_12")) else None
    ema_26 = float(last["EMA_26"]) if pd.notna(last.get("EMA_26")) else None
    trend_desc = "Neutral"
    if ema_12 and ema_26:
        if ema_12 > ema_26 * 1.002:
            trend_desc = "Bullish Uptrend (EMA12 > EMA26)"
        elif ema_12 < ema_26 * 0.998:
            trend_desc = "Bearish Downtrend (EMA12 < EMA26)"

    return IndicatorSnapshot(
        price=last_price,
        sma_20=float(last["SMA_20"]) if pd.notna(last.get("SMA_20")) else None,
        sma_50=float(last["SMA_50"]) if pd.notna(last.get("SMA_50")) else None,
        ema_12=ema_12,
        ema_26=ema_26,
        rsi_14=float(last["RSI_14"]) if pd.notna(last.get("RSI_14")) else None,
        macd=float(last["MACD"]) if pd.notna(last.get("MACD")) else None,
        macd_signal=float(last["MACD_Signal"]) if pd.notna(last.get("MACD_Signal")) else None,
        macd_hist=float(last["MACD_Hist"]) if pd.notna(last.get("MACD_Hist")) else None,
        bb_upper=float(last["BB_Upper"]) if pd.notna(last.get("BB_Upper")) else None,
        bb_middle=float(last["BB_Middle"]) if pd.notna(last.get("BB_Middle")) else None,
        bb_lower=float(last["BB_Lower"]) if pd.notna(last.get("BB_Lower")) else None,
        atr_14=float(last["ATR_14"]) if pd.notna(last.get("ATR_14")) else None,
        volume=float(last["Volume"]),
        volume_sma_20=float(last["Volume_SMA_20"]) if pd.notna(last.get("Volume_SMA_20")) else None,
        volume_ratio=float(last["Volume_Ratio"]) if pd.notna(last.get("Volume_Ratio")) else None,
        support=support,
        resistance=resistance,
        trend_description=trend_desc,
    )
