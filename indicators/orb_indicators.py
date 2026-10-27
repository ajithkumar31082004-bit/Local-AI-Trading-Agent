"""Pure deterministic EMA20, prior-volume average, ATR and EMA chop indicators."""
import numpy as np
import pandas as pd

def ema(series: pd.Series, period: int = 20) -> pd.Series:
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.astype(float).ewm(span=period, adjust=False, min_periods=period).mean()

def prior_volume_average(volume: pd.Series, period: int = 20) -> pd.Series:
    """Mean of the previous N bars, explicitly excluding the current bar."""
    if period < 1:
        raise ValueError("period must be >= 1")
    return volume.astype(float).shift(1).rolling(period, min_periods=period).mean()

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()

def ema_cross_count(close: pd.Series, ema_line: pd.Series, lookback: int = 10) -> int:
    delta = (close.astype(float) - ema_line.astype(float)).tail(lookback).dropna()
    signs = np.sign(delta.to_numpy())
    signs = signs[signs != 0]
    if len(signs) < 2:
        return 0
    return int(np.count_nonzero(signs[1:] != signs[:-1]))

def add_orb_indicators(frame: pd.DataFrame, ema_period: int = 20,
                       volume_period: int = 20, atr_period: int = 14) -> pd.DataFrame:
    out = frame.copy()
    out["EMA20"] = ema(out["Close"], ema_period)
    out["VolumeAvg20Prior"] = prior_volume_average(out["Volume"], volume_period)
    out["ATR"] = atr(out["High"], out["Low"], out["Close"], atr_period)
    return out
