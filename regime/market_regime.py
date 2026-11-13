"""Independent deterministic Market Regime Detection module for XAUTUSD on Delta Exchange India.

Regimes:
- TRENDING_BULLISH: Strong upward macro momentum (15m Close > EMA20 > EMA50, Price > VWAP, ADX >= 22)
- TRENDING_BEARISH: Strong downward macro momentum (15m Close < EMA20 < EMA50, Price < VWAP, ADX >= 22)
- RANGING: Low directional conviction, tight oscillation around VWAP, flat EMAs (ADX < 20, frequent EMA crosses)
- VOLATILE_BREAKOUT: Sudden volatility expansion and range breakout (ATR ratio > 1.35 or Bandwidth surge, high volume)
- UNCERTAIN: Conflicting signals, transition phase, or low confidence (< 0.60). Blocks all trading.
"""
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import (
    ema,
    atr,
    adx,
    vwap,
    bollinger_bands,
    ema_cross_count,
    ema_slope,
    prior_volume_average,
    resample_to_15m,
)
from regime.readiness import ReadinessState, SystemReadiness, evaluate_system_readiness


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    VOLATILE_BREAKOUT = "VOLATILE_BREAKOUT"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class RegimeDetectionResult:
    regime: MarketRegime
    confidence: float  # 0.0 to 1.0
    allowed_strategies: List[str]
    disallowed_strategies: List[str]
    rationale: str
    metrics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["regime"] = self.regime.value
        return d


def detect_market_regime(
    frame: pd.DataFrame,
    cfg: ORBSettings = settings,
) -> RegimeDetectionResult:
    """Deterministically classify market regime without lookahead bias using completed bars."""
    empty_result = RegimeDetectionResult(
        regime=MarketRegime.UNCERTAIN,
        confidence=0.0,
        allowed_strategies=[],
        disallowed_strategies=["ORB", "TREND_FOLLOWING", "VWAP_EMA", "BB_RSI_MEAN_REVERSION", "ATR_COMPRESSION_BREAKOUT", "LIQUIDITY_SWEEP"],
        rationale="Insufficient completed candle data for regime detection.",
        metrics={},
    )

    if frame is None or frame.empty:
        return empty_result

    # 1. Clean & prepare data (up to trailing lookback_cap bars for performance & warm-up)
    lookback_cap = max(450, getattr(cfg, "bootstrap_lookback_candles", 450))
    raw = frame.iloc[-lookback_cap:] if len(frame) > lookback_cap else frame
    if isinstance(raw.index, pd.DatetimeIndex) and {"Open", "High", "Low", "Close", "Volume"}.issubset(raw.columns):
        df_5m = raw.copy()
        if df_5m.index.tz is None:
            df_5m.index = df_5m.index.tz_localize(cfg.timezone)
        else:
            df_5m.index = df_5m.index.tz_convert(cfg.timezone)
    else:
        df = raw.copy()
        df.columns = [str(c).strip().title() if str(c).lower() in {"open", "high", "low", "close", "volume"} else c for c in df.columns]
        for req_col in ["Open", "High", "Low", "Close", "Volume"]:
            if req_col not in df.columns:
                return empty_result
        idx = pd.DatetimeIndex(pd.to_datetime(df.index))
        if idx.tz is None:
            idx = idx.tz_localize(cfg.timezone)
        else:
            idx = idx.tz_convert(cfg.timezone)
        df.index = idx
        df = df[~df.index.duplicated(keep="last")].sort_index()
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df_5m = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    # 2. Explicit System & Indicator Readiness Evaluation
    readiness = evaluate_system_readiness(df_5m, cfg=cfg)
    if not readiness.is_ready:
        missing_str = "; ".join(readiness.missing_requirements) if readiness.missing_requirements else "Data stabilizing"
        return RegimeDetectionResult(
            regime=MarketRegime.UNCERTAIN,
            confidence=0.0,
            allowed_strategies=[],
            disallowed_strategies=["ORB", "TREND_FOLLOWING", "VWAP_EMA", "BB_RSI_MEAN_REVERSION", "ATR_COMPRESSION_BREAKOUT", "LIQUIDITY_SWEEP"],
            rationale=f"WARMING_UP: {missing_str}",
            metrics={"readiness": readiness.to_dict()},
        )

    # 3. Multi-Timeframe 15m Indicators (guaranteed valid & converged)
    df_15m = resample_to_15m(df_5m)

    df_15m["EMA20"] = ema(df_15m["Close"], 20)
    df_15m["EMA50"] = ema(df_15m["Close"], 50)
    df_15m["ADX14"] = adx(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)
    df_15m["ATR14"] = atr(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)

    last_15m = df_15m.iloc[-1]
    c_15m = float(last_15m["Close"])
    ema20_15m = float(last_15m["EMA20"])
    ema50_15m = float(last_15m["EMA50"])
    adx_15m = float(last_15m["ADX14"]) if pd.notna(last_15m["ADX14"]) else 0.0
    atr_15m = float(last_15m["ATR14"]) if pd.notna(last_15m["ATR14"]) else 2.0
    ema20_slope_15m = float(ema_slope(df_15m["EMA20"], period=2).iloc[-1])

    # 3. 5m Microstructure & Indicators
    df_5m = df_5m.copy()
    df_5m["EMA20"] = ema(df_5m["Close"], 20)
    df_5m["EMA50"] = ema(df_5m["Close"], 50)
    df_5m["ATR14"] = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)
    df_5m["VWAP"] = vwap(df_5m["High"], df_5m["Low"], df_5m["Close"], df_5m["Volume"])
    bb_u, bb_m, bb_l, bb_bw = bollinger_bands(df_5m["Close"], period=20, num_std=2.0)
    df_5m["BB_BW"] = bb_bw
    df_5m["Vol_Avg20"] = prior_volume_average(df_5m["Volume"], 20)
    df_5m["ATR_SMA20"] = df_5m["ATR14"].rolling(window=20, min_periods=10).mean()

    curr_5m = df_5m.iloc[-1]
    c_5m = float(curr_5m["Close"])
    h_5m = float(curr_5m["High"])
    l_5m = float(curr_5m["Low"])
    vol_5m = float(curr_5m["Volume"])
    vol_avg_5m = float(curr_5m["Vol_Avg20"]) if pd.notna(curr_5m["Vol_Avg20"]) else 1.0
    atr_5m = float(curr_5m["ATR14"]) if pd.notna(curr_5m["ATR14"]) else 2.0
    atr_sma_5m = float(curr_5m["ATR_SMA20"]) if pd.notna(curr_5m["ATR_SMA20"]) and float(curr_5m["ATR_SMA20"]) > 0 else atr_5m
    atr_ratio = atr_5m / max(atr_sma_5m, 1e-6)
    vwap_5m = float(curr_5m["VWAP"]) if pd.notna(curr_5m["VWAP"]) else c_5m
    bb_bw_5m = float(curr_5m["BB_BW"]) if pd.notna(curr_5m["BB_BW"]) else 0.01
    bb_bw_mean = float(df_5m["BB_BW"].rolling(20, min_periods=10).mean().iloc[-1]) if pd.notna(df_5m["BB_BW"].iloc[-1]) else bb_bw_5m
    bw_ratio = bb_bw_5m / max(bb_bw_mean, 1e-6)

    # 4. Range and EMA Cross Dynamics
    crosses_20 = ema_cross_count(df_5m["Close"], df_5m["EMA20"], lookback=20)
    recent_15_high = float(df_5m["High"].iloc[-16:-1].max()) if len(df_5m) >= 16 else h_5m
    recent_15_low = float(df_5m["Low"].iloc[-16:-1].min()) if len(df_5m) >= 16 else l_5m
    is_breaking_high = c_5m > recent_15_high
    is_breaking_low = c_5m < recent_15_low

    metrics = {
        "adx_15m": round(adx_15m, 2),
        "atr_15m": round(atr_15m, 2),
        "atr_ratio_5m": round(atr_ratio, 2),
        "bw_ratio_5m": round(bw_ratio, 2),
        "vwap_diff_5m": round(c_5m - vwap_5m, 2),
        "crosses_20": crosses_20,
        "ema20_15m": round(ema20_15m, 2),
        "ema50_15m": round(ema50_15m, 2),
        "ema20_slope_15m": round(ema20_slope_15m, 4),
        "vol_ratio": round(vol_5m / max(vol_avg_5m, 1e-6), 2),
        "readiness": readiness.to_dict(),
    }

    # 5. REGIME CLASSIFICATION RULES

    # A. VOLATILE_BREAKOUT Check
    # Triggers on sharp volatility expansion (> 1.35x ATR SMA or > 1.4x Bandwidth), volume expansion, and range breakout
    if (atr_ratio >= 1.35 or bw_ratio >= 1.4) and (is_breaking_high or is_breaking_low) and (vol_5m >= 1.2 * vol_avg_5m):
        confidence = min(0.95, 0.65 + (0.15 * min(atr_ratio - 1.0, 1.0)) + (0.15 * min((vol_5m / max(vol_avg_5m, 1.0)) - 1.0, 1.0)))
        return RegimeDetectionResult(
            regime=MarketRegime.VOLATILE_BREAKOUT,
            confidence=round(confidence, 2),
            allowed_strategies=["ATR_COMPRESSION_BREAKOUT", "ORB"],
            disallowed_strategies=["BB_RSI_MEAN_REVERSION"],
            rationale=f"Volatile breakout detected: ATR expansion ({atr_ratio:.2f}x), Bandwidth expansion ({bw_ratio:.2f}x), Volume ({metrics['vol_ratio']:.1f}x), breakout beyond 15-bar range.",
            metrics=metrics,
        )

    # B. TRENDING_BULLISH Check
    # 15m Close > EMA20 > EMA50, positive slope, price above VWAP, ADX >= 22
    is_bullish_15m = (c_15m > ema20_15m > ema50_15m) and (ema20_slope_15m > 0)
    if is_bullish_15m and (c_5m >= vwap_5m) and (adx_15m >= 22.0):
        confidence = min(0.95, 0.60 + (0.20 * min((adx_15m - 20.0) / 25.0, 1.0)) + (0.15 * (1.0 if c_5m > ema20_15m else 0.5)))
        return RegimeDetectionResult(
            regime=MarketRegime.TRENDING_BULLISH,
            confidence=round(confidence, 2),
            allowed_strategies=["TREND_FOLLOWING", "VWAP_EMA", "ATR_COMPRESSION_BREAKOUT"],
            disallowed_strategies=["BB_RSI_MEAN_REVERSION"],
            rationale=f"Bullish trend confirmed: 15m Close ({c_15m:.2f}) > EMA20 ({ema20_15m:.2f}) > EMA50 ({ema50_15m:.2f}), Price > VWAP, 15m ADX={adx_15m:.1f}.",
            metrics=metrics,
        )

    # C. TRENDING_BEARISH Check
    # 15m Close < EMA20 < EMA50, negative slope, price below VWAP, ADX >= 22
    is_bearish_15m = (c_15m < ema20_15m < ema50_15m) and (ema20_slope_15m < 0)
    if is_bearish_15m and (c_5m <= vwap_5m) and (adx_15m >= 22.0):
        confidence = min(0.95, 0.60 + (0.20 * min((adx_15m - 20.0) / 25.0, 1.0)) + (0.15 * (1.0 if c_5m < ema20_15m else 0.5)))
        return RegimeDetectionResult(
            regime=MarketRegime.TRENDING_BEARISH,
            confidence=round(confidence, 2),
            allowed_strategies=["TREND_FOLLOWING", "VWAP_EMA", "ATR_COMPRESSION_BREAKOUT"],
            disallowed_strategies=["BB_RSI_MEAN_REVERSION"],
            rationale=f"Bearish trend confirmed: 15m Close ({c_15m:.2f}) < EMA20 ({ema20_15m:.2f}) < EMA50 ({ema50_15m:.2f}), Price < VWAP, 15m ADX={adx_15m:.1f}.",
            metrics=metrics,
        )

    # D. RANGING Check
    # ADX < 20, or (ADX < 23 and crosses_20 >= 2), tight oscillation around VWAP
    is_low_adx = adx_15m < 20.0 or (adx_15m < 23.0 and crosses_20 >= 2)
    vwap_proximity = abs(c_5m - vwap_5m) <= (1.2 * atr_5m)
    if is_low_adx and vwap_proximity:
        confidence = min(0.90, 0.60 + (0.20 * min((22.0 - adx_15m) / 10.0, 1.0)) + (0.10 * min(crosses_20 / 4.0, 1.0)))
        return RegimeDetectionResult(
            regime=MarketRegime.RANGING,
            confidence=round(confidence, 2),
            allowed_strategies=["BB_RSI_MEAN_REVERSION", "LIQUIDITY_SWEEP"],
            disallowed_strategies=["TREND_FOLLOWING", "VWAP_EMA"],
            rationale=f"Ranging/Choppy regime detected: 15m ADX={adx_15m:.1f} < 22, {crosses_20} EMA crosses in 20 bars, price hugging VWAP (diff: {c_5m - vwap_5m:+.2f}).",
            metrics=metrics,
        )

    # E. UNCERTAIN (Conflicting signals, transition phase)
    return RegimeDetectionResult(
        regime=MarketRegime.UNCERTAIN,
        confidence=0.45,
        allowed_strategies=[],
        disallowed_strategies=["ORB", "TREND_FOLLOWING", "VWAP_EMA", "BB_RSI_MEAN_REVERSION", "ATR_COMPRESSION_BREAKOUT", "LIQUIDITY_SWEEP"],
        rationale=f"Market regime uncertain: conflicting trend/momentum metrics (15m ADX={adx_15m:.1f}, crosses={crosses_20}, VWAP diff={c_5m - vwap_5m:+.2f}).",
        metrics=metrics,
    )

