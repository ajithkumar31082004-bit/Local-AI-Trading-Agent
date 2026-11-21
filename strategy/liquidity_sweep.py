"""Deterministic Liquidity Sweep Reversal Strategy for XAUTUSD on Delta Exchange India.

Rules:
1. Key Liquidity Levels:
   - Identifies prominent prior swing high and low over trailing 20 bars (excluding current bar).
2. Sweep Detection:
   - Bearish Sweep: Current bar pierces above prior swing high by >= min_sweep_buffer (0.1 ATR),
     then prints a decisive rejection wick and closes strictly BACK BELOW the swing high.
   - Bullish Sweep: Current bar pierces below prior swing low by >= min_sweep_buffer (0.1 ATR),
     then prints a decisive rejection wick and closes strictly BACK ABOVE the swing low.
3. Rejection & Momentum:
   - Requires rejection wick >= 30% of total candle range and close in direction of reversal.
   - Avoids assuming every small wick is a sweep (requires true structural swing clearance).
4. Risk & Target:
   - Stop Loss placed beyond the sweep wick extreme + ATR buffer (min 1.0 ATR).
   - Target calculated for >= 2.0R net after exchange costs.
"""
from datetime import datetime, timedelta, time as time_of_day
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import ema, atr, prior_volume_average, rolling_swing_levels
from strategy.orb_ema_volume import Signal, calculate_cost_adjusted_target
from risk.position_sizing import estimate_lot_risk


def evaluate_liquidity_sweep(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate completed 5m candle using Liquidity Sweep Reversal rules."""
    checklist = {
        "session_open_filter": False,
        "swing_level_identified": False,
        "sweep_penetration_detected": False,
        "rejection_closed_inside": False,
        "volume_confirmed": False,
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
            strategy_name="LIQUIDITY_SWEEP",
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

    # Indicators: ATR14, Volume Avg, Prior Swing Highs/Lows
    lookback = getattr(cfg, "liquidity_sweep_lookback", 20)
    df_5m["ATR14"] = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)
    df_5m["Vol_Avg20"] = prior_volume_average(df_5m["Volume"], 20)

    curr_bar = df_5m.iloc[-1]
    curr_close = float(curr_bar["Close"])
    curr_open = float(curr_bar["Open"])
    curr_high = float(curr_bar["High"])
    curr_low = float(curr_bar["Low"])
    curr_vol = float(curr_bar["Volume"])
    curr_atr = float(curr_bar["ATR14"]) if pd.notna(curr_bar["ATR14"]) else 2.0
    vol_avg = float(curr_bar["Vol_Avg20"]) if pd.notna(curr_bar["Vol_Avg20"]) else 1.0

    prior_bars = df_5m.iloc[-(lookback + 1):-1]
    if len(prior_bars) < lookback:
        return _no_trade("NO TRADE: insufficient bars for swing level identification.", "INSUFFICIENT_LOOKBACK")

    prior_swing_high = float(prior_bars["High"].max())
    prior_swing_low = float(prior_bars["Low"].min())
    checklist["swing_level_identified"] = True

    # Sweep Buffer (at least 0.1 * ATR beyond swing level to ensure a real level sweep)
    min_sweep_buffer = getattr(cfg, "liquidity_sweep_min_buffer_atr", 0.1) * curr_atr

    # Bullish vs Bearish Sweep Detection
    bearish_sweep = (curr_high >= prior_swing_high + min_sweep_buffer) and (curr_close < prior_swing_high)
    bullish_sweep = (curr_low <= prior_swing_low - min_sweep_buffer) and (curr_close > prior_swing_low)

    if not bearish_sweep and not bullish_sweep:
        return _no_trade("NO TRADE: no liquidity sweep beyond prior swing extremes detected.", "NO_SWEEP_DETECTED")
    checklist["sweep_penetration_detected"] = True

    total_range = max(curr_high - curr_low, 1e-6)

    if bearish_sweep:
        direction = "SHORT"
        upper_wick = curr_high - max(curr_open, curr_close)
        # Rejection wick >= 30% of candle range or close in lower 45% of range
        rejection_confirmed = (upper_wick >= 0.30 * total_range) and (curr_close < curr_open)
        if not rejection_confirmed:
            return _no_trade("NO TRADE: sweep lacked decisive bearish rejection wick closing below open.", "REJECTION_NOT_CONFIRMED")
        checklist["rejection_closed_inside"] = True

        min_stop_dist = getattr(cfg, "min_stop_distance_atr", 1.0) * curr_atr
        stop_loss = max(curr_high + (0.5 * curr_atr), curr_close + min_stop_dist)

    else:
        direction = "LONG"
        lower_wick = min(curr_open, curr_close) - curr_low
        rejection_confirmed = (lower_wick >= 0.30 * total_range) and (curr_close > curr_open)
        if not rejection_confirmed:
            return _no_trade("NO TRADE: sweep lacked decisive bullish rejection wick closing above open.", "REJECTION_NOT_CONFIRMED")
        checklist["rejection_closed_inside"] = True

        min_stop_dist = getattr(cfg, "min_stop_distance_atr", 1.0) * curr_atr
        stop_loss = min(curr_low - (0.5 * curr_atr), curr_close - min_stop_dist)

    # Volume Confirmation (>= 80% of volume average)
    if curr_vol < (0.8 * vol_avg):
        return _no_trade(f"NO TRADE: volume ({curr_vol:,.0f}) below 80% average ({vol_avg:,.0f}).", "VOLUME_BELOW_THRESHOLD")
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
    wick_ratio = (upper_wick if direction == "SHORT" else lower_wick) / total_range
    wick_score = min(1.0, wick_ratio / 0.5) * 0.35
    vol_score = min(1.0, curr_vol / max(vol_avg, 1.0)) * 0.30
    rr_score = min(1.0, net_r / 3.0) * 0.35
    quality_score = round(wick_score + vol_score + rr_score, 2)

    level_swept = prior_swing_high if direction == "SHORT" else prior_swing_low

    return Signal(
        direction=direction,
        entry=round(curr_close, 4),
        sl=round(stop_loss, 4),
        tp=round(target_price, 4),
        r_multiple=net_r,
        reason=f"Confirmed {direction} liquidity sweep of level ({level_swept:.2f}) with rejection wick ({wick_ratio*100:.0f}%).",
        checklist=checklist.copy(),
        timestamp=str(now),
        diagnostic_code="SIGNAL_CONFIRMED",
        checklist_reasons={k: "PASSED" for k in checklist.keys()},
        strategy_name="LIQUIDITY_SWEEP",
        quality_score=quality_score,
    )

