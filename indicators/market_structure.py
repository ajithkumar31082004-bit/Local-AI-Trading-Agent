"""Smart Money Concepts (SMC) Market Structure Indicators:
- Algorithmic Pivot Fractals (Swing Highs and Swing Lows)
- Trend Point Classification (HH, HL, LH, LL)
- External (HTF - 15m) vs Internal (LTF - 5m) Structure Mapping
- Break of Structure (BOS) and Change of Character (CHOCH / MCHoCH) Detection
- Valid Body-Close Breakout vs False Breakout (Liquidity Wick Sweep) Classification
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
import numpy as np
import pandas as pd

from indicators.orb_indicators import resample_to_15m, atr, prior_volume_average


@dataclass
class SwingPoint:
    index: pd.Timestamp
    price: float
    point_type: str  # "HIGH" or "LOW"
    label: str  # "HH", "HL", "LH", "LL", or "SWING"
    bar_offset: int  # how many bars ago it occurred


@dataclass
class MarketStructureState:
    external_bias: str  # "BULLISH", "BEARISH", "RANGING"
    internal_bias: str  # "BULLISH", "BEARISH", "RANGING"
    major_high: Optional[SwingPoint] = None
    major_low: Optional[SwingPoint] = None
    last_major_hl: Optional[SwingPoint] = None  # Key bullish invalidation (Bearish MCHoCH trigger)
    last_major_lh: Optional[SwingPoint] = None  # Key bearish invalidation (Bullish MCHoCH trigger)
    minor_high: Optional[SwingPoint] = None
    minor_low: Optional[SwingPoint] = None
    last_minor_hl: Optional[SwingPoint] = None  # Key internal bullish invalidation (Bearish CHOCH)
    last_minor_lh: Optional[SwingPoint] = None  # Key internal bearish invalidation (Bullish CHOCH)
    is_aligned: bool = False  # external_bias == internal_bias
    all_minor_highs: List[SwingPoint] = field(default_factory=list)
    all_minor_lows: List[SwingPoint] = field(default_factory=list)
    all_major_highs: List[SwingPoint] = field(default_factory=list)
    all_major_lows: List[SwingPoint] = field(default_factory=list)


def find_swing_points(
    df: pd.DataFrame,
    left_bars: int = 3,
    right_bars: int = 1,
) -> Tuple[List[SwingPoint], List[SwingPoint]]:
    """Identify confirmed fractal swing highs and swing lows without lookahead bias.
    
    A bar at index i is a confirmed swing high if its High is strictly greater than
    all left_bars before it and all right_bars after it.
    It is ONLY confirmed once the right_bars have completely closed.
    """
    if len(df) < left_bars + right_bars + 1:
        return [], []

    highs = df["High"].values
    lows = df["Low"].values
    times = df.index

    swing_highs: List[SwingPoint] = []
    swing_lows: List[SwingPoint] = []

    # Iterate up to len(df) - right_bars to ensure right_bars are completed
    n = len(df)
    for i in range(left_bars, n - right_bars):
        current_high = highs[i]
        current_low = lows[i]

        # Check Swing High
        is_swing_high = True
        for l in range(1, left_bars + 1):
            if highs[i - l] >= current_high:
                is_swing_high = False
                break
        if is_swing_high:
            for r in range(1, right_bars + 1):
                if highs[i + r] >= current_high:
                    is_swing_high = False
                    break

        if is_swing_high:
            label = "SWING"
            if swing_highs:
                label = "HH" if current_high > swing_highs[-1].price else "LH"
            swing_highs.append(SwingPoint(
                index=times[i],
                price=float(current_high),
                point_type="HIGH",
                label=label,
                bar_offset=n - 1 - i,
            ))

        # Check Swing Low
        is_swing_low = True
        for l in range(1, left_bars + 1):
            if lows[i - l] <= current_low:
                is_swing_low = False
                break
        if is_swing_low:
            for r in range(1, right_bars + 1):
                if lows[i + r] <= current_low:
                    is_swing_low = False
                    break

        if is_swing_low:
            label = "SWING"
            if swing_lows:
                label = "HL" if current_low > swing_lows[-1].price else "LL"
            swing_lows.append(SwingPoint(
                index=times[i],
                price=float(current_low),
                point_type="LOW",
                label=label,
                bar_offset=n - 1 - i,
            ))

    return swing_highs, swing_lows


def determine_bias_from_swings(
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
) -> Tuple[str, Optional[SwingPoint], Optional[SwingPoint]]:
    """Determine directional structure bias and key invalidation levels from swing sequence."""
    if not swing_highs or not swing_lows:
        return "RANGING", None, None

    last_high = swing_highs[-1]
    last_low = swing_lows[-1]

    # Bullish criteria: last high is HH or last low is HL
    if last_high.label == "HH" and last_low.label == "HL":
        # In an uptrend, key invalidation swing is the last HL
        key_invalidation = last_low
        return "BULLISH", last_high, key_invalidation
    
    # Bearish criteria: last low is LL and last high is LH
    if last_low.label == "LL" and last_high.label == "LH":
        # In a downtrend, key invalidation swing is the last LH
        key_invalidation = last_high
        return "BEARISH", key_invalidation, last_low

    # Emerging Bullish (HL formed after LL)
    if last_low.label == "HL":
        return "BULLISH", last_high, last_low

    # Emerging Bearish (LH formed after HH)
    if last_high.label == "LH":
        return "BEARISH", last_high, last_low

    return "RANGING", last_high, last_low


def analyze_market_structure(
    df_5m: pd.DataFrame,
    df_15m: Optional[pd.DataFrame] = None,
    ext_left: int = 4,
    ext_right: int = 2,
    int_left: int = 3,
    int_right: int = 1,
) -> MarketStructureState:
    """Analyze dual-timeframe market structure across External (15m HTF) and Internal (5m LTF)."""
    if df_15m is None or df_15m.empty:
        df_15m = resample_to_15m(df_5m)

    # 1. External Structure (15m HTF)
    ext_highs, ext_lows = find_swing_points(df_15m, left_bars=ext_left, right_bars=ext_right)
    ext_bias, ext_key_high, ext_key_low = determine_bias_from_swings(ext_highs, ext_lows)

    last_major_hl = None
    last_major_lh = None
    for s in reversed(ext_lows):
        if s.label == "HL":
            last_major_hl = s
            break
    for s in reversed(ext_highs):
        if s.label == "LH":
            last_major_lh = s
            break

    # 2. Internal Structure (5m LTF)
    int_highs, int_lows = find_swing_points(df_5m, left_bars=int_left, right_bars=int_right)
    int_bias, int_key_high, int_key_low = determine_bias_from_swings(int_highs, int_lows)

    last_minor_hl = None
    last_minor_lh = None
    for s in reversed(int_lows):
        if s.label == "HL":
            last_minor_hl = s
            break
    for s in reversed(int_highs):
        if s.label == "LH":
            last_minor_lh = s
            break

    return MarketStructureState(
        external_bias=ext_bias,
        internal_bias=int_bias,
        major_high=ext_highs[-1] if ext_highs else None,
        major_low=ext_lows[-1] if ext_lows else None,
        last_major_hl=last_major_hl or (ext_lows[-1] if ext_lows else None),
        last_major_lh=last_major_lh or (ext_highs[-1] if ext_highs else None),
        minor_high=int_highs[-1] if int_highs else None,
        minor_low=int_lows[-1] if int_lows else None,
        last_minor_hl=last_minor_hl or (int_lows[-1] if int_lows else None),
        last_minor_lh=last_minor_lh or (int_highs[-1] if int_highs else None),
        is_aligned=(ext_bias == int_bias and ext_bias in ("BULLISH", "BEARISH")),
        all_minor_highs=int_highs,
        all_minor_lows=int_lows,
        all_major_highs=ext_highs,
        all_major_lows=ext_lows,
    )


def evaluate_break_validity(
    candle: pd.Series,
    level: float,
    direction: str,  # "BULLISH" or "BEARISH"
    min_buffer: float = 0.0,
) -> Dict[str, Any]:
    """Strictly evaluate whether a price move is a Valid Body Close Breakout or a False Wick Breakout.
    
    - Valid Bullish Break: Close > level + min_buffer.
    - False Bullish Break (Trap / Sweep): High > level, but Close <= level.
    - Valid Bearish Break: Close < level - min_buffer.
    - False Bearish Break (Trap / Sweep): Low < level, but Close >= level.
    """
    close = float(candle["Close"])
    high = float(candle["High"])
    low = float(candle["Low"])
    open_p = float(candle["Open"])

    if direction == "BULLISH":
        pierced = high > level
        closed_beyond = close > (level + min_buffer)
        closed_inside = close <= level
        rejection_wick = high - max(open_p, close)
        candle_range = max(high - low, 1e-6)
        wick_ratio = rejection_wick / candle_range

        return {
            "valid_break": closed_beyond,
            "false_break": pierced and closed_inside,
            "pierced": pierced,
            "closed_beyond": closed_beyond,
            "wick_ratio": round(wick_ratio, 3),
            "break_type": "VALID_BOS" if closed_beyond else ("WICK_SWEEP" if pierced else "NO_BREAK"),
        }
    else:  # BEARISH
        pierced = low < level
        closed_beyond = close < (level - min_buffer)
        closed_inside = close >= level
        rejection_wick = min(open_p, close) - low
        candle_range = max(high - low, 1e-6)
        wick_ratio = rejection_wick / candle_range

        return {
            "valid_break": closed_beyond,
            "false_break": pierced and closed_inside,
            "pierced": pierced,
            "closed_beyond": closed_beyond,
            "wick_ratio": round(wick_ratio, 3),
            "break_type": "VALID_BOS" if closed_beyond else ("WICK_SWEEP" if pierced else "NO_BREAK"),
        }
