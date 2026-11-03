"""Deterministic VWAP + EMA Confirmation Strategy for XAUTUSD on Delta Exchange India.

Rules:
1. Directional Alignment:
   - Bullish: 15m Close > 15m EMA20 > 15m EMA50, 5m Close > 5m VWAP, 5m EMA20 > 5m EMA50.
   - Bearish: 15m Close < 15m EMA20 < 15m EMA50, 5m Close < 5m VWAP, 5m EMA20 < 5m EMA50.
2. Completed-Candle Confirmation:
   - Bullish: Candle pulls back to test VWAP/EMA20 support, then prints a bullish close (Close > Open and Close > EMA20).
   - Bearish: Candle rallies to test VWAP/EMA20 resistance, then prints a bearish close (Close < Open and Close < EMA20).
3. Overextension Filter:
   - Avoid entries if price is stretched > 2.0 * ATR from 5m EMA20.
4. Risk & Target:
   - Stop Loss placed beyond recent swing extreme + ATR buffer (min 1.0 ATR).
   - Target calculated for >= 2.0R net after exchange costs.
"""
from datetime import datetime, timedelta, time as time_of_day
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import ema, atr, vwap, prior_volume_average, ema_slope
from strategy.orb_ema_volume import Signal, calculate_cost_adjusted_target
from risk.position_sizing import estimate_lot_risk
from strategy.trend_following import resample_to_15m


def evaluate_vwap_ema(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate completed 5m candle using VWAP + EMA confirmation rules."""
    checklist = {
        "session_open_filter": False,
        "vwap_ema_aligned": False,
        "pullback_confirmed": False,
        "continuation_candle": False,
        "volume_confirmed": False,
        "not_overextended": False,
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
            strategy_name="VWAP_EMA",
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

    # 15m Macro Trend Check
    df_15m = resample_to_15m(df_5m)
    if len(df_15m) < 50:
        return _no_trade("NO TRADE: insufficient 15m resampled candles.", "INSUFFICIENT_15M_BARS")

    df_15m["EMA20"] = ema(df_15m["Close"], 20)
    df_15m["EMA50"] = ema(df_15m["Close"], 50)
    last_15m = df_15m.iloc[-1]
    c_15m = float(last_15m["Close"])
    ema20_15m = float(last_15m["EMA20"])
    ema50_15m = float(last_15m["EMA50"])

    is_bullish_15m = (c_15m > ema20_15m > ema50_15m)
    is_bearish_15m = (c_15m < ema20_15m < ema50_15m)
    if not is_bullish_15m and not is_bearish_15m:
        return _no_trade("NO TRADE: 15m trend not aligned.", "TREND_15M_NEUTRAL")

    # 5m Indicators (VWAP, EMA20, EMA50, ATR14)
    df_5m["EMA20"] = ema(df_5m["Close"], 20)
    df_5m["EMA50"] = ema(df_5m["Close"], 50)
    df_5m["ATR14"] = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)
    df_5m["VWAP"] = vwap(df_5m["High"], df_5m["Low"], df_5m["Close"], df_5m["Volume"])
    df_5m["Vol_Avg20"] = prior_volume_average(df_5m["Volume"], 20)

    curr_bar = df_5m.iloc[-1]
    curr_close = float(curr_bar["Close"])
    curr_open = float(curr_bar["Open"])
    curr_high = float(curr_bar["High"])
    curr_low = float(curr_bar["Low"])
    curr_vol = float(curr_bar["Volume"])
    vol_avg = float(curr_bar["Vol_Avg20"]) if pd.notna(curr_bar["Vol_Avg20"]) else 1.0
    curr_ema20 = float(curr_bar["EMA20"])
    curr_ema50 = float(curr_bar["EMA50"])
    curr_vwap = float(curr_bar["VWAP"]) if pd.notna(curr_bar["VWAP"]) else curr_close
    curr_atr = float(curr_bar["ATR14"]) if pd.notna(curr_bar["ATR14"]) else 2.0

    # Alignment Check
    if is_bullish_15m:
        if not (curr_close > curr_vwap and curr_ema20 > curr_ema50):
            return _no_trade("NO TRADE: price or 5m EMAs below VWAP.", "VWAP_ALIGNMENT_FAILED")
        macro_trend = "LONG"
    else:
        if not (curr_close < curr_vwap and curr_ema20 < curr_ema50):
            return _no_trade("NO TRADE: price or 5m EMAs above VWAP.", "VWAP_ALIGNMENT_FAILED")
        macro_trend = "SHORT"
    checklist["vwap_ema_aligned"] = True

    # Overextension check (< 2.0 ATR from 5m EMA20)
    dist_ema = abs(curr_close - curr_ema20)
    if dist_ema > 2.0 * curr_atr:
        return _no_trade(f"NO TRADE: price extended from EMA20 ({dist_ema:.2f} > {2.0 * curr_atr:.2f}).", "PRICE_OVEREXTENDED")
    checklist["not_overextended"] = True

    # Pullback and Confirmation
    recent_bars = df_5m.iloc[-5:-1]
    tolerance = 0.5 * curr_atr

    if macro_trend == "SHORT":
        # Tested VWAP or EMA20 from below
        pulled_back = any(
            float(row["High"]) >= min(float(row["EMA20"]), float(row["VWAP"])) - tolerance
            for _, row in recent_bars.iterrows()
        )
        if not pulled_back and curr_high < (min(curr_ema20, curr_vwap) - tolerance):
            return _no_trade("NO TRADE: waiting for bearish rally pullback to VWAP/EMA20.", "NO_PULLBACK_DETECTED")
        checklist["pullback_confirmed"] = True

        # Bearish completed candle confirmation
        if not (curr_close < curr_open and curr_close < curr_ema20 and curr_close < curr_vwap):
            return _no_trade("NO TRADE: waiting for bearish confirmation candle closing below EMA20 and VWAP.", "REJECTION_NOT_CONFIRMED")
        checklist["continuation_candle"] = True

        recent_high = float(df_5m["High"].iloc[-5:].max())
        min_stop_dist = getattr(cfg, "min_stop_distance_atr", 1.0) * curr_atr
        stop_loss = max(recent_high + (0.5 * curr_atr), curr_close + min_stop_dist)

    else:  # LONG
        pulled_back = any(
            float(row["Low"]) <= max(float(row["EMA20"]), float(row["VWAP"])) + tolerance
            for _, row in recent_bars.iterrows()
        )
        if not pulled_back and curr_low > (max(curr_ema20, curr_vwap) + tolerance):
            return _no_trade("NO TRADE: waiting for bullish dip pullback to VWAP/EMA20.", "NO_PULLBACK_DETECTED")
        checklist["pullback_confirmed"] = True

        if not (curr_close > curr_open and curr_close > curr_ema20 and curr_close > curr_vwap):
            return _no_trade("NO TRADE: waiting for bullish confirmation candle closing above EMA20 and VWAP.", "REJECTION_NOT_CONFIRMED")
        checklist["continuation_candle"] = True

        recent_low = float(df_5m["Low"].iloc[-5:].min())
        min_stop_dist = getattr(cfg, "min_stop_distance_atr", 1.0) * curr_atr
        stop_loss = min(recent_low - (0.5 * curr_atr), curr_close - min_stop_dist)

    # Volume Confirmation
    if curr_vol < (0.8 * vol_avg):
        return _no_trade(f"NO TRADE: volume ({curr_vol:,.0f}) below 80% of average ({vol_avg:,.0f}).", "VOLUME_BELOW_THRESHOLD")
    checklist["volume_confirmed"] = True

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
        direction=macro_trend,
        entry=curr_close,
        sl=stop_loss,
        cfg=cfg,
        target_r=max(cfg.target_r, 2.0),
    )
    if net_r < 2.0:
        return _no_trade(f"NO TRADE: net reward-to-risk ({net_r:.2f}R) below 2.0R.", "RISK_REWARD_BELOW_MINIMUM")
    checklist["rr_ok"] = True

    # Transparent Quality Score Calculation (0.0 to 1.0)
    vol_score = min(1.0, curr_vol / max(vol_avg, 1.0)) * 0.25
    rr_score = min(1.0, net_r / 3.0) * 0.25
    trend_score = 0.30 if is_bullish_15m or is_bearish_15m else 0.10
    vwap_proximity_score = max(0.0, 1.0 - (abs(curr_close - curr_vwap) / (2.0 * curr_atr))) * 0.20
    quality_score = round(vol_score + rr_score + trend_score + vwap_proximity_score, 2)

    return Signal(
        direction=macro_trend,
        entry=round(curr_close, 4),
        sl=round(stop_loss, 4),
        tp=round(target_price, 4),
        r_multiple=net_r,
        reason=f"Confirmed {macro_trend} VWAP + EMA confluence with pullback and continuation rejection.",
        checklist=checklist.copy(),
        timestamp=str(now),
        diagnostic_code="SIGNAL_CONFIRMED",
        checklist_reasons={k: "PASSED" for k in checklist.keys()},
        strategy_name="VWAP_EMA",
        quality_score=quality_score,
    )

