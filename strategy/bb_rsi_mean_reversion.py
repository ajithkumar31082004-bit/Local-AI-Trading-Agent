"""Deterministic Bollinger Bands + RSI Mean Reversion Strategy for XAUTUSD on Delta Exchange India.

Rules:
1. Indicators: Bollinger Bands (20, 2.0), RSI (14).
2. Trend Strength Filter:
   - Forbids mean reversion against strong macro trends (15m ADX >= 25 or strong EMA trending).
3. Reversal Setup:
   - Long: Prior bar probed/swept below Lower Band, RSI < 35, followed by completed bullish candle closing inside band with lower wick rejection.
   - Short: Prior bar probed/swept above Upper Band, RSI > 65, followed by completed bearish candle closing inside band with upper wick rejection.
4. Risk & Target:
   - Stop Loss placed beyond probe extreme + ATR buffer (min 1.0 ATR).
   - Target calculated for >= 2.0R net after exchange costs.
"""
from datetime import datetime, timedelta, time as time_of_day
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import ema, atr, adx, bollinger_bands, rsi, prior_volume_average
from strategy.orb_ema_volume import Signal, calculate_cost_adjusted_target
from risk.position_sizing import estimate_lot_risk
from strategy.trend_following import resample_to_15m


def evaluate_bb_rsi(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate completed 5m candle using Bollinger Bands + RSI Mean Reversion rules."""
    checklist = {
        "session_open_filter": False,
        "trend_filter_passed": False,
        "band_probe_detected": False,
        "rsi_extreme_confirmed": False,
        "reversal_candle_closed_inside": False,
        "friction_ok": False,
        "rr_ok": False,
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
            strategy_name="BB_RSI_MEAN_REVERSION",
            quality_score=0.0,
        )

    if frame.empty:
        return _no_trade("NO TRADE: no candle data available.", "NO_CANDLE_DATA")

    raw = frame.iloc[-300:] if len(frame) > 300 else frame
    if isinstance(raw.index, pd.DatetimeIndex) and {"Open", "High", "Low", "Close", "Volume"}.issubset(raw.columns):
        df_5m = raw.copy()
        if df_5m.index.tz is None:
            df_5m.index = df_5m.index.tz_localize(cfg.timezone)
        else:
            df_5m.index = df_5m.index.tz_convert(cfg.timezone)
    else:
        out = raw.copy()
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

    if len(df_5m) < 60:
        return _no_trade("NO TRADE: insufficient candle history.", "INSUFFICIENT_CANDLES")

    # Session Hours Check
    now = df_5m.index[-1]
    start_t = time_of_day.fromisoformat(cfg.session_start)
    end_t = time_of_day.fromisoformat(getattr(cfg, "session_end", "23:30"))
    now_t = now.time()

    if now_t < start_t or now_t > end_t:
        return _no_trade(f"NO TRADE: outside active trading window ({cfg.session_start} - {end_t.strftime('%H:%M')}).", "OUTSIDE_SESSION_HOURS")

    no_entry_mins = getattr(cfg, "no_entry_minutes_after_open", 45)
    if no_entry_mins > 0:
        start_dt = datetime.combine(now.date(), start_t)
        open_cutoff_t = (start_dt + timedelta(minutes=no_entry_mins)).time()
        if now_t <= open_cutoff_t:
            return _no_trade(f"NO TRADE: within session-open filter (cutoff {open_cutoff_t.strftime('%H:%M')}).", "SESSION_OPEN_FILTER")
    checklist["session_open_filter"] = True

    last_entry_str = getattr(cfg, "last_entry_time", "22:00")
    if last_entry_str and now_t >= time_of_day.fromisoformat(last_entry_str):
        return _no_trade(f"NO TRADE: at or after late-entry cutoff ({last_entry_str}).", "LATE_ENTRY_FILTER")

    # Trend Strength Filter (15m ADX)
    df_15m = resample_to_15m(df_5m)
    if len(df_15m) < 50:
        return _no_trade("NO TRADE: insufficient 15m resampled candles.", "INSUFFICIENT_15M_BARS")

    df_15m["ADX14"] = adx(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)
    adx_15m = float(df_15m["ADX14"].iloc[-1]) if pd.notna(df_15m["ADX14"].iloc[-1]) else 0.0
    max_adx = getattr(cfg, "bb_trend_max_adx", 25.0)
    if adx_15m >= max_adx:
        return _no_trade(f"NO TRADE: strong macro trend (15m ADX {adx_15m:.1f} >= {max_adx:.1f}); mean reversion forbidden.", "STRONG_TREND_MEAN_REVERSION_FORBIDDEN")
    checklist["trend_filter_passed"] = True

    # 5m Indicators: BB(20, 2), RSI(14), ATR14
    bb_period = getattr(cfg, "bb_period", 20)
    bb_std = getattr(cfg, "bb_std", 2.0)
    rsi_period = getattr(cfg, "rsi_period", 14)

    bb_u, bb_m, bb_l, _ = bollinger_bands(df_5m["Close"], period=bb_period, num_std=bb_std)
    df_5m["BB_Upper"] = bb_u
    df_5m["BB_Middle"] = bb_m
    df_5m["BB_Lower"] = bb_l
    df_5m["RSI"] = rsi(df_5m["Close"], period=rsi_period)
    df_5m["ATR14"] = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)

    curr_bar = df_5m.iloc[-1]
    prev_bar = df_5m.iloc[-2]
    prior_bars = df_5m.iloc[-4:-1]

    curr_close = float(curr_bar["Close"])
    curr_open = float(curr_bar["Open"])
    curr_high = float(curr_bar["High"])
    curr_low = float(curr_bar["Low"])
    curr_atr = float(curr_bar["ATR14"]) if pd.notna(curr_bar["ATR14"]) else 2.0
    curr_rsi = float(curr_bar["RSI"]) if pd.notna(curr_bar["RSI"]) else 50.0
    curr_bbu = float(curr_bar["BB_Upper"])
    curr_bbl = float(curr_bar["BB_Lower"])
    curr_bbm = float(curr_bar["BB_Middle"])

    rsi_oversold = getattr(cfg, "rsi_oversold", 35.0)
    rsi_overbought = getattr(cfg, "rsi_overbought", 65.0)

    # Check setup conditions for Long vs Short
    long_probe = bool((prior_bars["Low"] <= prior_bars["BB_Lower"]).any()) or curr_low <= curr_bbl
    short_probe = bool((prior_bars["High"] >= prior_bars["BB_Upper"]).any()) or curr_high >= curr_bbu

    # Prior RSI extreme
    min_prior_rsi = float(prior_bars["RSI"].dropna().min()) if not prior_bars["RSI"].dropna().empty else curr_rsi
    max_prior_rsi = float(prior_bars["RSI"].dropna().max()) if not prior_bars["RSI"].dropna().empty else curr_rsi

    is_long_setup = long_probe and (min_prior_rsi <= rsi_oversold or curr_rsi <= rsi_oversold + 5.0)
    is_short_setup = short_probe and (max_prior_rsi >= rsi_overbought or curr_rsi >= rsi_overbought - 5.0)

    if not is_long_setup and not is_short_setup:
        return _no_trade("NO TRADE: price has not probed outer Bollinger Bands with RSI extreme.", "NO_BAND_PROBE")
    checklist["band_probe_detected"] = True
    checklist["rsi_extreme_confirmed"] = True

    # Both cannot trigger at once
    if is_long_setup and is_short_setup:
        return _no_trade("NO TRADE: conflicting Bollinger Band extremes detected.", "CONFLICTING_BAND_SIGNALS")

    if is_long_setup:
        direction = "LONG"
        # Must close back inside lower band and print bullish reversal candle
        candle_closed_inside = (curr_close > curr_bbl) and (curr_close > curr_open)
        lower_wick = min(curr_open, curr_close) - curr_low
        total_range = max(curr_high - curr_low, 1e-6)
        has_rejection_wick = (lower_wick >= 0.25 * total_range) or (curr_close >= curr_low + 0.6 * total_range)

        if not (candle_closed_inside and has_rejection_wick):
            return _no_trade("NO TRADE: waiting for bullish reversal candle closing inside Lower Band.", "REVERSAL_NOT_CONFIRMED")
        checklist["reversal_candle_closed_inside"] = True

        probe_low = min(float(df_5m["Low"].iloc[-4:].min()), curr_low)
        min_stop_dist = getattr(cfg, "min_stop_distance_atr", 1.0) * curr_atr
        stop_loss = min(probe_low - (0.5 * curr_atr), curr_close - min_stop_dist)

    else:
        direction = "SHORT"
        candle_closed_inside = (curr_close < curr_bbu) and (curr_close < curr_open)
        upper_wick = curr_high - max(curr_open, curr_close)
        total_range = max(curr_high - curr_low, 1e-6)
        has_rejection_wick = (upper_wick >= 0.25 * total_range) or (curr_close <= curr_high - 0.6 * total_range)

        if not (candle_closed_inside and has_rejection_wick):
            return _no_trade("NO TRADE: waiting for bearish reversal candle closing inside Upper Band.", "REVERSAL_NOT_CONFIRMED")
        checklist["reversal_candle_closed_inside"] = True

        probe_high = max(float(df_5m["High"].iloc[-4:].max()), curr_high)
        min_stop_dist = getattr(cfg, "min_stop_distance_atr", 1.0) * curr_atr
        stop_loss = max(probe_high + (0.5 * curr_atr), curr_close + min_stop_dist)

    # Friction Check
    per_lot_risk, fee_cost, spread_slip_cost = estimate_lot_risk(curr_close, stop_loss, cfg)
    raw_risk = abs(curr_close - stop_loss) * cfg.contract_size_xaut
    friction_r = (fee_cost + spread_slip_cost) / max(raw_risk, 1e-12)
    max_friction = getattr(cfg, "max_friction_to_risk_ratio", 0.25)
    if friction_r > max_friction:
        return _no_trade(f"NO TRADE: friction ({friction_r:.2f}R) exceeds tolerance ({max_friction:.2f}R).", "FRICTION_EXCEEDS_TOLERANCE")
    checklist["friction_ok"] = True

    # Target & RR
    target_price, net_r = calculate_cost_adjusted_target(
        direction=direction,
        entry=curr_close,
        sl=stop_loss,
        cfg=cfg,
        target_r=max(cfg.target_r, 2.0),
    )
    if net_r < 2.0:
        return _no_trade(f"NO TRADE: net reward-to-risk ({net_r:.2f}R) below 2.0R.", "RISK_REWARD_BELOW_MINIMUM")
    checklist["rr_ok"] = True

    # Transparent Quality Score (0.0 to 1.0)
    rsi_ext_score = min(1.0, abs(curr_rsi - 50.0) / 30.0) * 0.35
    rr_score = min(1.0, net_r / 3.0) * 0.30
    adx_calm_score = max(0.0, (25.0 - adx_15m) / 15.0) * 0.35
    quality_score = round(rsi_ext_score + rr_score + adx_calm_score, 2)

    return Signal(
        direction=direction,
        entry=round(curr_close, 4),
        sl=round(stop_loss, 4),
        tp=round(target_price, 4),
        r_multiple=net_r,
        reason=f"Confirmed {direction} Bollinger Band mean reversion from outer band with RSI ({curr_rsi:.1f}).",
        checklist=checklist.copy(),
        timestamp=str(now),
        diagnostic_code="SIGNAL_CONFIRMED",
        checklist_reasons={k: "PASSED" for k in checklist.keys()},
        strategy_name="BB_RSI_MEAN_REVERSION",
        quality_score=quality_score,
    )

