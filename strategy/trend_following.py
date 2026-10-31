"""Deterministic Multi-Timeframe Trend-Following Strategy for XAUTUSD on Delta Exchange India.

Rules:
1. Higher Timeframe (15m): EMA20 vs EMA50 alignment for macro trend direction.
   - Bullish Trend: 15m Close > 15m EMA20 > 15m EMA50.
   - Bearish Trend: 15m Close < 15m EMA20 < 15m EMA50.
2. Execution Timeframe (5m):
   - Bullish Setup: 5m price pulls back to 5m EMA20 or structure, holds, and confirms with a bullish rejection/continuation candle.
   - Bearish Setup: 5m price rallies back to 5m EMA20 or structure, holds, and confirms with a bearish rejection/continuation candle.
3. Risk & Structure:
   - Stop Loss placed beyond pullback swing extreme + ATR buffer.
   - Take Profit calculated for >= 2.0R net after exchange fees, spread, and slippage.
   - Forbids chasing extended prices (> 2.0 * 5m ATR from EMA20).
4. Trading Hours:
   - Operates throughout active market window (e.g., 09:00 - 23:30 Asia/Kolkata).
"""
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, time as time_of_day
from typing import Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import add_orb_indicators, ema, atr, adx
from strategy.orb_ema_volume import Signal, calculate_cost_adjusted_target, estimate_lot_risk

logger = logging.getLogger(__name__)


def resample_to_15m(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Resample completed 5m OHLCV bars into strictly completed 15m OHLCV bars.
    
    Excludes any incomplete current 15m bucket with fewer than 3 completed 5m bars
    to strictly eliminate look-ahead and partial-bar bias.
    """
    if df_5m.empty:
        return pd.DataFrame()
    ohlc = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }
    grouped = df_5m.resample("15min", closed="left", label="left")
    counts = grouped["Close"].count()
    df_15m = grouped.agg(ohlc).dropna()
    # Retain strictly closed 15m buckets containing all 3 completed 5m bars
    df_15m = df_15m[counts == 3]
    return df_15m


def evaluate_trend_following(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate completed 5m candle using multi-timeframe 15m trend + 5m pullback rules."""
    checklist = {
        "trend_15m_aligned": False,
        "pullback_confirmed": False,
        "continuation_candle": False,
        "volume_confirmed": False,
        "not_overextended": False,
        "rr_ok": False,
        "risk_ok": None,
    }

    def _no_trade(reason: str, code: str, custom_reasons: Optional[Dict[str, str]] = None) -> Signal:
        reasons = {k: "PASSED" if checklist.get(k) else "FAILED" for k in checklist.keys()}
        if custom_reasons:
            reasons.update(custom_reasons)
        return Signal(
            direction="NO TRADE",
            entry=None,
            sl=None,
            tp=None,
            r_multiple=0.0,
            reason=reason,
            checklist=checklist,
            timestamp=str(frame.index[-1]) if not frame.empty else None,
            diagnostic_code=code,
            checklist_reasons=reasons,
        )

    if frame.empty:
        return _no_trade("NO TRADE: no candle data available.", "NO_CANDLE_DATA")

    # 1. Clean and timezone-align dataframe
    out = frame.copy()
    out.columns = [str(c).strip().title() if str(c).lower() in {"open", "high", "low", "close", "volume"} else c for c in out.columns]
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is None:
        idx = idx.tz_localize(cfg.timezone)
    else:
        idx = idx.tz_convert(cfg.timezone)
    out.index = idx
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    df_5m = out.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    # Need at least 50 bars on 15m -> ~150 bars on 5m
    if len(df_5m) < 60:
        return _no_trade("NO TRADE: insufficient candle history for trend and indicator calculation.", "INSUFFICIENT_CANDLES")

    # 2. Check active trading session hours
    now = df_5m.index[-1]
    start_t = time_of_day.fromisoformat(cfg.session_start)
    end_t = time_of_day.fromisoformat(getattr(cfg, "session_end", "23:30"))
    now_t = now.time()

    if now_t < start_t or now_t > end_t:
        return _no_trade(
            f"NO TRADE: outside active trading window ({cfg.session_start} - {getattr(cfg, 'session_end', '23:30')} {cfg.timezone}).",
            "OUTSIDE_SESSION_HOURS",
            {"trend_15m_aligned": "OUTSIDE_SESSION_HOURS"}
        )

    # 2a. Session-open filter (block entries during first N minutes after open)
    no_entry_mins = getattr(cfg, "no_entry_minutes_after_open", 45)
    if no_entry_mins > 0:
        start_dt = datetime.combine(now.date(), start_t)
        open_cutoff_t = (start_dt + timedelta(minutes=no_entry_mins)).time()
        if now_t <= open_cutoff_t:
            return _no_trade(
                f"NO TRADE: within session-open filter ({no_entry_mins}m buffer after open; cutoff {open_cutoff_t.strftime('%H:%M')} {cfg.timezone}).",
                "SESSION_OPEN_FILTER",
                {"trend_15m_aligned": "SESSION_OPEN_FILTER"}
            )

    # 2b. Late-entry cutoff (no new positions at or after last_entry_time)
    last_entry_str = getattr(cfg, "last_entry_time", "22:00")
    if last_entry_str:
        last_entry_t = time_of_day.fromisoformat(last_entry_str)
        if now_t >= last_entry_t:
            return _no_trade(
                f"NO TRADE: at or after late-entry cutoff ({last_entry_str} {cfg.timezone}; session closes at {getattr(cfg, 'session_end', '23:30')}).",
                "LATE_ENTRY_FILTER",
                {"trend_15m_aligned": "LATE_ENTRY_FILTER"}
            )

    # 3. Compute 15m Multi-Timeframe Trend (EMA20 vs EMA50)
    # Use trailing lookback window (up to 300 5m bars = 100 15m bars) for indicator warm-up & performance
    calc_window = df_5m.iloc[-300:].copy() if len(df_5m) > 300 else df_5m.copy()
    df_15m = resample_to_15m(calc_window)
    if len(df_15m) < 50:
        return _no_trade("NO TRADE: insufficient 15m resampled candles for EMA50 baseline.", "INSUFFICIENT_15M_BARS")

    df_15m["EMA20"] = ema(df_15m["Close"], 20)
    df_15m["EMA50"] = ema(df_15m["Close"], 50)

    last_15m = df_15m.iloc[-1]
    close_15m = float(last_15m["Close"])
    ema20_15m = float(last_15m["EMA20"])
    ema50_15m = float(last_15m["EMA50"])

    is_bullish_15m = (close_15m > ema20_15m) and (ema20_15m > ema50_15m)
    is_bearish_15m = (close_15m < ema20_15m) and (ema20_15m < ema50_15m)

    if not is_bullish_15m and not is_bearish_15m:
        return _no_trade(
            f"NO TRADE: 15m trend neutral or conflicting (15m Close: {close_15m:.2f}, EMA20: {ema20_15m:.2f}, EMA50: {ema50_15m:.2f}).",
            "TREND_15M_NEUTRAL",
            {"trend_15m_aligned": "TREND_15M_NEUTRAL"}
        )

    macro_trend = "LONG" if is_bullish_15m else "SHORT"
    checklist["trend_15m_aligned"] = True

    # 4. Compute 5m Indicators (EMA20, 20-bar Volume Avg, ATR14)
    df_5m_ind = add_orb_indicators(calc_window, ema_period=20, volume_period=20, atr_period=14)
    curr_bar = df_5m_ind.iloc[-1]
    prev_bar = df_5m_ind.iloc[-2]

    curr_close = float(curr_bar["Close"])
    curr_open = float(curr_bar["Open"])
    curr_high = float(curr_bar["High"])
    curr_low = float(curr_bar["Low"])
    curr_vol = float(curr_bar["Volume"])
    curr_ema20 = float(curr_bar["EMA20"])
    curr_atr = float(curr_bar["ATR"]) if pd.notna(curr_bar["ATR"]) else 2.0
    vol_avg = float(curr_bar["VolumeAvg20Prior"]) if pd.notna(curr_bar["VolumeAvg20Prior"]) else 0.0

    # 5. Overextension Filter: Do not enter if price is stretched > 2.5 * ATR from EMA20
    dist_from_ema = abs(curr_close - curr_ema20)
    max_extension = 2.5 * curr_atr
    if dist_from_ema > max_extension:
        return _no_trade(
            f"NO TRADE: price too extended from 5m EMA20 ({dist_from_ema:.2f} > {max_extension:.2f}); avoid chasing.",
            "PRICE_OVEREXTENDED",
            {"not_overextended": "PRICE_OVEREXTENDED"}
        )
    checklist["not_overextended"] = True

    # 6. Pullback and Continuation Confirmation
    recent_bars = df_5m_ind.iloc[-5:-1]
    tolerance = 0.5 * curr_atr

    if macro_trend == "SHORT":
        # Pullback: In the last 4 bars, did high rally back toward or above EMA20?
        pulled_back = any(float(row["High"]) >= (float(row["EMA20"]) - tolerance) for _, row in recent_bars.iterrows())
        if not pulled_back and curr_high < (curr_ema20 - tolerance):
            return _no_trade("NO TRADE: waiting for bearish rally pullback toward 5m EMA20.", "NO_PULLBACK_DETECTED")
        checklist["pullback_confirmed"] = True

        # Continuation Rejection Candle: Multi-factor rejection check
        # Must have:
        # 1. Bearish close: close < open and close < EMA20
        # 2. Upper rejection wick: upper wick >= 25% of total candle range or >= 50% of candle body
        # 3. Directional close: close must settle in the lower 45% of the candle's total high-low range
        total_range = max(curr_high - curr_low, 1e-6)
        close_position = (curr_close - curr_low) / total_range
        is_bearish_rejection = (curr_close < curr_open) and (curr_close < curr_ema20)
        upper_wick = curr_high - max(curr_open, curr_close)
        body = max(abs(curr_close - curr_open), 1e-6)
        has_rejection_wick = (upper_wick >= 0.25 * total_range) or (upper_wick >= 0.5 * body)
        closes_in_lower_portion = close_position <= 0.45

        if not (is_bearish_rejection and has_rejection_wick and closes_in_lower_portion):
            return _no_trade("NO TRADE: waiting for confirmed bearish rejection candle closing in lower 45% of range below 5m EMA20.", "REJECTION_NOT_CONFIRMED")
        checklist["continuation_candle"] = True

        # Volume Confirmation: Current bar volume >= 0.8 * 20-bar average
        if curr_vol < (0.8 * vol_avg):
            return _no_trade(f"NO TRADE: continuation volume ({curr_vol:,.0f}) below 80% of average ({vol_avg:,.0f}).", "VOLUME_BELOW_THRESHOLD")
        checklist["volume_confirmed"] = True

        # Stop loss: Swing high of recent 5 bars + buffer
        recent_high = float(df_5m_ind["High"].iloc[-5:].max())
        stop_loss = recent_high + (0.5 * curr_atr)

    else:  # LONG
        pulled_back = any(float(row["Low"]) <= (float(row["EMA20"]) + tolerance) for _, row in recent_bars.iterrows())
        if not pulled_back and curr_low > (curr_ema20 + tolerance):
            return _no_trade("NO TRADE: waiting for bullish dip pullback toward 5m EMA20.", "NO_PULLBACK_DETECTED")
        checklist["pullback_confirmed"] = True

        # Bullish Rejection Candle:
        # 1. Bullish close: close > open and close > EMA20
        # 2. Lower rejection wick: lower wick >= 25% of total candle range or >= 50% of candle body
        # 3. Directional close: close must settle in the upper 45% of the candle's total high-low range
        total_range = max(curr_high - curr_low, 1e-6)
        close_position = (curr_high - curr_close) / total_range
        is_bullish_rejection = (curr_close > curr_open) and (curr_close > curr_ema20)
        lower_wick = min(curr_open, curr_close) - curr_low
        body = max(abs(curr_close - curr_open), 1e-6)
        has_rejection_wick = (lower_wick >= 0.25 * total_range) or (lower_wick >= 0.5 * body)
        closes_in_upper_portion = close_position <= 0.45

        if not (is_bullish_rejection and has_rejection_wick and closes_in_upper_portion):
            return _no_trade("NO TRADE: waiting for confirmed bullish rejection candle closing in upper 45% of range above 5m EMA20.", "REJECTION_NOT_CONFIRMED")
        checklist["continuation_candle"] = True

        if curr_vol < (0.8 * vol_avg):
            return _no_trade(f"NO TRADE: continuation volume ({curr_vol:,.0f}) below 80% of average ({vol_avg:,.0f}).", "VOLUME_BELOW_THRESHOLD")
        checklist["volume_confirmed"] = True

        recent_low = float(df_5m_ind["Low"].iloc[-5:].min())
        stop_loss = recent_low - (0.5 * curr_atr)

    # 7. Reward-to-Risk Calculation (Cost-Adjusted 2R)
    target_price, net_r = calculate_cost_adjusted_target(
        direction=macro_trend,
        entry=curr_close,
        sl=stop_loss,
        cfg=cfg,
        target_r=max(cfg.target_r, 2.0),
    )

    if net_r < 2.0:
        return _no_trade(f"NO TRADE: reward-to-risk ratio ({net_r:.2f}R) after fees and slippage is below 1:2.", "RISK_REWARD_BELOW_MINIMUM")
    checklist["rr_ok"] = True

    return Signal(
        direction=macro_trend,
        entry=round(curr_close, 4),
        sl=round(stop_loss, 4),
        tp=round(target_price, 4),
        r_multiple=net_r,
        reason=f"Confirmed {macro_trend} 15m trend alignment + 5m EMA20 pullback + rejection continuation.",
        checklist=checklist.copy(),
        timestamp=str(now),
        diagnostic_code="SIGNAL_CONFIRMED",
        checklist_reasons={k: "PASSED" for k in checklist.keys() if k != "risk_ok"},
    )

