"""Pure deterministic EMA20, EMA slope, prior-volume average, ATR and EMA chop indicators."""
from typing import Optional
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int = 20) -> pd.Series:
    """Calculate Exponential Moving Average (EMA).
    
    Includes the current candle close.
    The first (period - 1) bars return NaN.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if series.empty:
        return pd.Series(dtype=float, index=series.index)
    return series.astype(float).ewm(span=period, adjust=False, min_periods=period).mean()


def ema_slope(series: pd.Series, period: int = 1) -> pd.Series:
    """Calculate the deterministic slope (first difference) of an EMA series.
    
    Slope = EMA[i] - EMA[i - period].
    Positive value indicates upward slope, negative indicates downward slope.
    Includes the current candle; avoids look-ahead bias.
    Returns NaN for bars where historical data is insufficient or series contains NaN.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if series.empty:
        return pd.Series(dtype=float, index=series.index)
    return series.astype(float).diff(periods=period)


def prior_volume_average(volume: pd.Series, period: int = 20) -> pd.Series:
    """Calculate the rolling mean of volume for previous N bars, strictly EXCLUDING the current bar.
    
    Uses volume.shift(1).rolling(period, min_periods=period).mean().
    The current candle is never included in the volume baseline.
    Returns NaN if there are fewer than `period` prior completed bars.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if volume.empty:
        return pd.Series(dtype=float, index=volume.index)
    return volume.astype(float).shift(1).rolling(period, min_periods=period).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR) as volatility measure for the extension filter.
    
    True Range = max(High - Low, abs(High - Close_prev), abs(Low - Close_prev)).
    Includes current candle high and low, comparing against previous close.
    First (period - 1) bars return NaN.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if high.empty or low.empty or close.empty:
        return pd.Series(dtype=float)
    prev_close = close.astype(float).shift(1)
    tr1 = high.astype(float) - low.astype(float)
    tr2 = (high.astype(float) - prev_close).abs()
    tr3 = (low.astype(float) - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def ema_cross_count(close: pd.Series, ema_line: pd.Series, lookback: int = 10) -> int:
    """Count how many times price crossed the EMA line within the last `lookback` completed bars.
    
    A cross occurs when sign(close[i] - ema[i]) != sign(close[i-1] - ema[i-1]).
    Used as an explicit chop filter (e.g. >= 3 crosses indicates indecision/range bound).
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if close.empty or ema_line.empty:
        return 0
    delta = (close.astype(float) - ema_line.astype(float)).tail(lookback).dropna()
    signs = np.sign(delta.to_numpy())
    signs = signs[signs != 0]
    if len(signs) < 2:
        return 0
    return int(np.count_nonzero(signs[1:] != signs[:-1]))


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Welles Wilder's Average Directional Index (ADX) without lookahead bias.
    
    Returns NaN for bars before min_periods is satisfied.
    ADX >= 22-25 indicates trending market regime; ADX < 20 indicates ranging/choppy regime.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if high.empty or low.empty or close.empty:
        return pd.Series(dtype=float)
    
    prev_close = close.astype(float).shift(1)
    tr1 = high.astype(float) - low.astype(float)
    tr2 = (high.astype(float) - prev_close).abs()
    tr3 = (low.astype(float) - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    up_move = high.astype(float) - high.astype(float).shift(1)
    down_move = low.astype(float).shift(1) - low.astype(float)
    
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    alpha = 1.0 / period
    tr_smooth = pd.Series(tr).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    pos_dm_smooth = pd.Series(pos_dm, index=close.index).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    neg_dm_smooth = pd.Series(neg_dm, index=close.index).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    
    pos_di = 100.0 * (pos_dm_smooth / (tr_smooth + 1e-12))
    neg_di = 100.0 * (neg_dm_smooth / (tr_smooth + 1e-12))
    
    dx = 100.0 * (pos_di - neg_di).abs() / (pos_di + neg_di + 1e-12)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def add_orb_indicators(frame: pd.DataFrame, ema_period: int = 20,
                       volume_period: int = 20, atr_period: int = 14) -> pd.DataFrame:
    """Enrich OHLCV DataFrame with deterministic ORB indicators without lookahead bias."""
    out = frame.copy()
    out["EMA20"] = ema(out["Close"], ema_period)
    out["EMA20_Slope"] = ema_slope(out["EMA20"], period=1)
    out["VolumeAvg20Prior"] = prior_volume_average(out["Volume"], volume_period)
    out["ATR"] = atr(out["High"], out["Low"], out["Close"], atr_period)
    return out
