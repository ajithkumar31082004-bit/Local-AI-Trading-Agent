"""Deterministic Market Regime Engine for Phase 4.7 Forward Trading Evaluation.

Classifies market dynamics into five mutually exclusive primary regimes:
- TRENDING_BULL: Bullish macro momentum (Close > EMA20 > EMA50, Price > VWAP, ADX >= 22).
- TRENDING_BEAR: Bearish macro momentum (Close < EMA20 < EMA50, Price < VWAP, ADX >= 22).
- RANGING_COMPRESSED: Low directional conviction, tight oscillation around VWAP, compressed volatility.
- VOLATILE_EXPANDING: Sudden volatility expansion and range breakout (ATR ratio > 1.35x or Bandwidth surge).
- SHOCK_CRASH: Extreme candle range (> 3.0x ATR) or rapid price collapse (> 1.5% in 15m). Absolute entry blocker.
- UNKNOWN: Insufficient data (< 50 bars), stale timestamps, or low confidence (< 0.60).
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


class MarketRegime(str, Enum):
    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING_COMPRESSED = "RANGING_COMPRESSED"
    VOLATILE_EXPANDING = "VOLATILE_EXPANDING"
    SHOCK_CRASH = "SHOCK_CRASH"
    UNKNOWN = "UNKNOWN"

    # Backward compatibility aliases
    TRENDING_BULLISH = "TRENDING_BULL"
    TRENDING_BEARISH = "TRENDING_BEAR"
    RANGING = "RANGING_COMPRESSED"
    VOLATILE_BREAKOUT = "VOLATILE_EXPANDING"
    UNCERTAIN = "UNKNOWN"

    @property
    def is_shock_or_crash(self) -> bool:
        return self == MarketRegime.SHOCK_CRASH

    @property
    def blocks_entries(self) -> bool:
        return self in {MarketRegime.SHOCK_CRASH, MarketRegime.UNKNOWN}


ALL_STRATEGIES = [
    "ORB",
    "ORB_EMA_VOLUME",
    "TREND_FOLLOWING",
    "VWAP_EMA",
    "BB_RSI_MEAN_REVERSION",
    "ATR_COMPRESSION_BREAKOUT",
    "LIQUIDITY_SWEEP",
]


@dataclass
class RegimeDetectionResult:
    regime: MarketRegime
    confidence: float
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
    cfg: Optional[ORBSettings] = None,
    lookback_window: int = 100,
    hysteresis_bars: int = 3,
) -> RegimeDetectionResult:
    """Deterministically classify market regime for Phase 4.7 without lookahead bias."""
    active_cfg = cfg or settings
    empty_result = RegimeDetectionResult(
        regime=MarketRegime.UNKNOWN,
        confidence=0.0,
        allowed_strategies=[],
        disallowed_strategies=list(ALL_STRATEGIES),
        rationale="Insufficient completed candle data (< 50 bars) for regime detection.",
        metrics={},
    )

    if frame is None or frame.empty or len(frame) < 50:
        return empty_result

    # 1. Clean & prepare data
    lookback_cap = max(450, getattr(active_cfg, "bootstrap_lookback_candles", 450))
    raw = frame.iloc[-lookback_cap:] if len(frame) > lookback_cap else frame

    if isinstance(raw.index, pd.DatetimeIndex) and {"Open", "High", "Low", "Close", "Volume"}.issubset(raw.columns):
        df_5m = raw.copy()
        if df_5m.index.tz is None:
            df_5m.index = df_5m.index.tz_localize(active_cfg.timezone)
        else:
            df_5m.index = df_5m.index.tz_convert(active_cfg.timezone)
    else:
        df = raw.copy()
        df.columns = [str(c).strip().title() if str(c).lower() in {"open", "high", "low", "close", "volume"} else c for c in df.columns]
        for req_col in ["Open", "High", "Low", "Close", "Volume"]:
            if req_col not in df.columns:
                return empty_result
        idx = pd.DatetimeIndex(pd.to_datetime(df.index))
        if idx.tz is None:
            idx = idx.tz_localize(active_cfg.timezone)
        else:
            idx = idx.tz_convert(active_cfg.timezone)
        df.index = idx
        df = df[~df.index.duplicated(keep="last")].sort_index()
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df_5m = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    if len(df_5m) < 50:
        return empty_result

    # 2. Resample to 15m and calculate 15m indicators
    df_15m = resample_to_15m(df_5m)
    if len(df_15m) < 15:
        # Not enough 15m bars to compute indicators reliably
        return RegimeDetectionResult(
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,
            allowed_strategies=[],
            disallowed_strategies=list(ALL_STRATEGIES),
            rationale="Insufficient 15m bars (< 15) for higher-timeframe confirmation.",
            metrics={"bars_5m": len(df_5m), "bars_15m": len(df_15m)},
        )

    df_15m["EMA20"] = ema(df_15m["Close"], 20)
    df_15m["EMA50"] = ema(df_15m["Close"], 50)
    df_15m["ADX14"] = adx(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)
    df_15m["ATR14"] = atr(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)

    last_15m = df_15m.iloc[-1]
    c_15m = float(last_15m["Close"])
    ema20_15m = float(last_15m["EMA20"]) if pd.notna(last_15m["EMA20"]) else c_15m
    ema50_15m = float(last_15m["EMA50"]) if pd.notna(last_15m["EMA50"]) else c_15m
    adx_15m = float(last_15m["ADX14"]) if pd.notna(last_15m["ADX14"]) else 0.0
    atr_15m = float(last_15m["ATR14"]) if pd.notna(last_15m["ATR14"]) else 2.0
    ema20_slope_15m = float(ema_slope(df_15m["EMA20"], period=2).iloc[-1]) if len(df_15m) >= 2 else 0.0

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
    o_5m = float(curr_5m["Open"])
    vol_5m = float(curr_5m["Volume"])
    vol_avg_5m = float(curr_5m["Vol_Avg20"]) if pd.notna(curr_5m["Vol_Avg20"]) else 1.0
    atr_5m = float(curr_5m["ATR14"]) if pd.notna(curr_5m["ATR14"]) else 2.0
    atr_sma_5m = float(curr_5m["ATR_SMA20"]) if pd.notna(curr_5m["ATR_SMA20"]) and float(curr_5m["ATR_SMA20"]) > 0 else atr_5m
    atr_ratio = atr_5m / max(atr_sma_5m, 1e-6)
    vwap_5m = float(curr_5m["VWAP"]) if pd.notna(curr_5m["VWAP"]) else c_5m
    bb_bw_5m = float(curr_5m["BB_BW"]) if pd.notna(curr_5m["BB_BW"]) else 0.01
    bb_bw_mean = float(df_5m["BB_BW"].rolling(20, min_periods=10).mean().iloc[-1]) if pd.notna(df_5m["BB_BW"].iloc[-1]) else bb_bw_5m
    bw_ratio = bb_bw_5m / max(bb_bw_mean, 1e-6)

    # Range and EMA Cross Dynamics
    crosses_20 = ema_cross_count(df_5m["Close"], df_5m["EMA20"], lookback=20)
    recent_15_high = float(df_5m["High"].iloc[-16:-1].max()) if len(df_5m) >= 16 else h_5m
    recent_15_low = float(df_5m["Low"].iloc[-16:-1].min()) if len(df_5m) >= 16 else l_5m
    is_breaking_high = c_5m > recent_15_high
    is_breaking_low = c_5m < recent_15_low

    # 15m window drop / move (last 3 5m candles)
    price_15m_ago = float(df_5m["Close"].iloc[-4]) if len(df_5m) >= 4 else float(df_5m["Close"].iloc[0])
    pct_change_15m = (c_5m - price_15m_ago) / max(price_15m_ago, 1e-6)
    candle_range_5m = h_5m - l_5m
    range_atr_ratio = candle_range_5m / max(atr_5m, 1e-6)

    metrics = {
        "adx_15m": round(adx_15m, 2),
        "atr_15m": round(atr_15m, 2),
        "atr_5m": round(atr_5m, 2),
        "atr_ratio_5m": round(atr_ratio, 2),
        "range_atr_ratio": round(range_atr_ratio, 2),
        "pct_change_15m": round(pct_change_15m * 100.0, 3),
        "bw_ratio_5m": round(bw_ratio, 2),
        "vwap_diff_5m": round(c_5m - vwap_5m, 2),
        "crosses_20": crosses_20,
        "ema20_15m": round(ema20_15m, 2),
        "ema50_15m": round(ema50_15m, 2),
        "ema20_slope_15m": round(ema20_slope_15m, 4),
        "vol_ratio": round(vol_5m / max(vol_avg_5m, 1e-6), 2),
    }

    # 4. CLASSIFICATION HIERARCHY

    # PRIORITY 1: SHOCK_CRASH
    # Triggered by extreme single-candle range (> 3.0x ATR) or rapid 15m drop (> 1.5%)
    is_shock_candle = range_atr_ratio >= 3.0
    is_rapid_crash = pct_change_15m <= -0.015  # drop > 1.5% in 15m

    if is_shock_candle or is_rapid_crash:
        crash_trigger = []
        if is_shock_candle:
            crash_trigger.append(f"candle range {candle_range_5m:.2f} >= 3.0x ATR ({range_atr_ratio:.2f}x)")
        if is_rapid_crash:
            crash_trigger.append(f"15m price drop {pct_change_15m * 100:.2f}% <= -1.5%")
        return RegimeDetectionResult(
            regime=MarketRegime.SHOCK_CRASH,
            confidence=0.99,
            allowed_strategies=[],
            disallowed_strategies=list(ALL_STRATEGIES),
            rationale=f"SHOCK_CRASH detected: {'; '.join(crash_trigger)}. All new order entries blocked for capital preservation.",
            metrics=metrics,
        )

    # PRIORITY 2: VOLATILE_EXPANDING
    # Volatility expansion: ATR ratio >= 1.35 or Bandwidth ratio >= 1.4, volume expansion, and range breakout
    if (atr_ratio >= 1.35 or bw_ratio >= 1.4) and (is_breaking_high or is_breaking_low) and (vol_5m >= 1.2 * vol_avg_5m):
        confidence = min(0.95, 0.65 + (0.15 * min(atr_ratio - 1.0, 1.0)) + (0.15 * min((vol_5m / max(vol_avg_5m, 1.0)) - 1.0, 1.0)))
        return RegimeDetectionResult(
            regime=MarketRegime.VOLATILE_EXPANDING,
            confidence=round(confidence, 2),
            allowed_strategies=["ATR_COMPRESSION_BREAKOUT", "ORB", "ORB_EMA_VOLUME"],
            disallowed_strategies=["BB_RSI_MEAN_REVERSION"],
            rationale=f"Volatile expanding breakout: ATR expansion ({atr_ratio:.2f}x), Bandwidth expansion ({bw_ratio:.2f}x), Volume ({metrics['vol_ratio']:.1f}x).",
            metrics=metrics,
        )

    # PRIORITY 3: TRENDING_BULL
    # 15m Close > EMA20 > EMA50, positive slope, 5m price above VWAP, 15m ADX >= 22
    is_bullish_15m = (c_15m > ema20_15m > ema50_15m) and (ema20_slope_15m > 0)
    if is_bullish_15m and (c_5m >= vwap_5m) and (adx_15m >= 22.0):
        confidence = min(0.95, 0.60 + (0.20 * min((adx_15m - 20.0) / 25.0, 1.0)) + (0.15 * (1.0 if c_5m > ema20_15m else 0.5)))
        return RegimeDetectionResult(
            regime=MarketRegime.TRENDING_BULL,
            confidence=round(confidence, 2),
            allowed_strategies=["TREND_FOLLOWING", "VWAP_EMA", "ATR_COMPRESSION_BREAKOUT", "ORB", "ORB_EMA_VOLUME"],
            disallowed_strategies=["BB_RSI_MEAN_REVERSION"],
            rationale=f"Bullish trend confirmed: 15m Close ({c_15m:.2f}) > EMA20 ({ema20_15m:.2f}) > EMA50 ({ema50_15m:.2f}), Price >= VWAP, ADX={adx_15m:.1f}.",
            metrics=metrics,
        )

    # PRIORITY 4: TRENDING_BEAR
    # 15m Close < EMA20 < EMA50, negative slope, 5m price below VWAP, 15m ADX >= 22
    is_bearish_15m = (c_15m < ema20_15m < ema50_15m) and (ema20_slope_15m < 0)
    if is_bearish_15m and (c_5m <= vwap_5m) and (adx_15m >= 22.0):
        confidence = min(0.95, 0.60 + (0.20 * min((adx_15m - 20.0) / 25.0, 1.0)) + (0.15 * (1.0 if c_5m < ema20_15m else 0.5)))
        return RegimeDetectionResult(
            regime=MarketRegime.TRENDING_BEAR,
            confidence=round(confidence, 2),
            allowed_strategies=["TREND_FOLLOWING", "VWAP_EMA", "ATR_COMPRESSION_BREAKOUT", "ORB", "ORB_EMA_VOLUME"],
            disallowed_strategies=["BB_RSI_MEAN_REVERSION"],
            rationale=f"Bearish trend confirmed: 15m Close ({c_15m:.2f}) < EMA20 ({ema20_15m:.2f}) < EMA50 ({ema50_15m:.2f}), Price <= VWAP, ADX={adx_15m:.1f}.",
            metrics=metrics,
        )

    # PRIORITY 5: RANGING_COMPRESSED
    # Low ADX (< 20 or < 23 with crosses >= 2), tight oscillation around VWAP
    is_low_adx = adx_15m < 20.0 or (adx_15m < 23.0 and crosses_20 >= 2)
    vwap_proximity = abs(c_5m - vwap_5m) <= (1.2 * atr_5m)
    if is_low_adx and vwap_proximity:
        confidence = min(0.90, 0.60 + (0.20 * min((22.0 - adx_15m) / 10.0, 1.0)) + (0.10 * min(crosses_20 / 4.0, 1.0)))
        return RegimeDetectionResult(
            regime=MarketRegime.RANGING_COMPRESSED,
            confidence=round(confidence, 2),
            allowed_strategies=["BB_RSI_MEAN_REVERSION", "LIQUIDITY_SWEEP"],
            disallowed_strategies=["TREND_FOLLOWING", "VWAP_EMA"],
            rationale=f"Ranging compressed regime: 15m ADX={adx_15m:.1f} < 22, {crosses_20} EMA crosses, price near VWAP (diff: {c_5m - vwap_5m:+.2f}).",
            metrics=metrics,
        )

    # PRIORITY 6: UNKNOWN (Conflicting signals, transition phase)
    return RegimeDetectionResult(
        regime=MarketRegime.UNKNOWN,
        confidence=0.45,
        allowed_strategies=[],
        disallowed_strategies=list(ALL_STRATEGIES),
        rationale=f"Market regime unknown / transitioning: ADX={adx_15m:.1f}, crosses={crosses_20}, VWAP diff={c_5m - vwap_5m:+.2f}.",
        metrics=metrics,
    )

