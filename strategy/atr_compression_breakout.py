"""Deterministic ATR Volatility Compression Breakout Strategy for XAUTUSD on Delta Exchange India.

Rules:
1. Low-Volatility Consolidation Detection:
   - Identifies narrow range / compression over preceding 15 bars where ATR <= 0.85 * 20-bar ATR average or Bandwidth is compressed.
   - Consolidation boundaries: High_15 and Low_15.
2. Volatility Expansion Breakout:
   - Confirmed completed candle close outside consolidation boundary:
     - Long: Close > High_15 with bullish candle body and expanding volume.
     - Short: Close < Low_15 with bearish candle body and expanding volume.
3. False Breakout & Overextension Rejections:
   - Rejects wick-only breakouts (candle must close outside range).
   - Rejects overextended breakouts (> 2.0 ATR beyond breakout level).
4. Risk & Target:
   - Stop Loss placed at consolidation midpoint or boundary (min 1.0 ATR distance).
   - Target calculated for >= 2.0R net after exchange costs.
"""
from datetime import datetime, timedelta, time as time_of_day
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import ema, atr, bollinger_bands, prior_volume_average
from strategy.orb_ema_volume import Signal, calculate_cost_adjusted_target
from risk.position_sizing import estimate_lot_risk


def evaluate_atr_compression_breakout(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate completed 5m candle using ATR Volatility Compression Breakout rules."""
    checklist = {
        "session_open_filter": False,
        "compression_detected": False,
        "confirmed_breakout_close": False,
        "expansion_volume_confirmed": False,
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
            strategy_name="ATR_COMPRESSION_BREAKOUT",
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

    # Indicators: ATR14, 20-bar ATR SMA, Volume Avg20, Bollinger Bandwidth
    df_5m["ATR14"] = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)
    df_5m["ATR_SMA20"] = df_5m["ATR14"].rolling(window=20, min_periods=10).mean()
    df_5m["Vol_Avg20"] = prior_volume_average(df_5m["Volume"], 20)
    _, _, _, bb_bw = bollinger_bands(df_5m["Close"], 20, 2.0)
    df_5m["BB_BW"] = bb_bw

    curr_bar = df_5m.iloc[-1]
    curr_close = float(curr_bar["Close"])
    curr_open = float(curr_bar["Open"])
    curr_high = float(curr_bar["High"])
    curr_low = float(curr_bar["Low"])
    curr_vol = float(curr_bar["Volume"])
    curr_atr = float(curr_bar["ATR14"]) if pd.notna(curr_bar["ATR14"]) else 2.0
    vol_avg = float(curr_bar["Vol_Avg20"]) if pd.notna(curr_bar["Vol_Avg20"]) else 1.0

    lookback = getattr(cfg, "atr_compression_lookback", 15)
    prior_bars = df_5m.iloc[-(lookback + 1):-1]
    if len(prior_bars) < lookback:
        return _no_trade("NO TRADE: insufficient consolidation lookback.", "INSUFFICIENT_LOOKBACK")

    # Consolidation Detection:
    # 1. Past ATR ratio was <= atr_compression_ratio (e.g. 0.85) or Bandwidth was compressed
    prior_atr_mean = float(prior_bars["ATR14"].mean())
    prior_atr_sma = float(prior_bars["ATR_SMA20"].mean()) if pd.notna(prior_bars["ATR_SMA20"].mean()) else prior_atr_mean
    compression_ratio = prior_atr_mean / max(prior_atr_sma, 1e-6)
    threshold_ratio = getattr(cfg, "atr_compression_ratio", 0.90)

    cons_high = float(prior_bars["High"].max())
    cons_low = float(prior_bars["Low"].min())
    cons_width = cons_high - cons_low

    is_compressed = (compression_ratio <= threshold_ratio) or (cons_width <= 2.5 * curr_atr)
    if not is_compressed:
        return _no_trade(f"NO TRADE: volatility compression not detected (ratio {compression_ratio:.2f} > {threshold_ratio:.2f}).", "NO_VOLATILITY_COMPRESSION")
    checklist["compression_detected"] = True

    # Breakout Confirmation:
    # Bullish: Close > cons_high, Close > Open
    # Bearish: Close < cons_low, Close < Open
    is_bullish_breakout = (curr_close > cons_high) and (curr_close > curr_open)
    is_bearish_breakout = (curr_close < cons_low) and (curr_close < curr_open)

    # Wick only check
    if (curr_high > cons_high and curr_close <= cons_high) or (curr_low < cons_low and curr_close >= cons_low):
        return _no_trade("NO TRADE: false breakout (wick penetrated range but candle closed inside).", "FALSE_BREAKOUT_WICK_ONLY")

    if not is_bullish_breakout and not is_bearish_breakout:
        return _no_trade("NO TRADE: price remains within consolidation boundaries.", "INSIDE_CONSOLIDATION_RANGE")
    checklist["confirmed_breakout_close"] = True

    direction = "LONG" if is_bullish_breakout else "SHORT"

    # Overextension Check: breakout close must not be > 2.0 ATR beyond breakout level
    breakout_extension = (curr_close - cons_high) if direction == "LONG" else (cons_low - curr_close)
    if breakout_extension > 2.0 * curr_atr:
        return _no_trade(f"NO TRADE: price excessively extended beyond breakout level ({breakout_extension:.2f} > {2.0 * curr_atr:.2f}).", "PRICE_OVEREXTENDED")
    checklist["not_overextended"] = True

    # Volatility / Volume Expansion Confirmation:
    candle_range = curr_high - curr_low
    expansion_confirmed = (candle_range >= 1.1 * curr_atr) or (curr_vol >= 1.1 * vol_avg)
    if not expansion_confirmed:
        return _no_trade("NO TRADE: breakout lacks volatility or volume expansion confirmation.", "EXPANSION_NOT_CONFIRMED")
    checklist["expansion_volume_confirmed"] = True

    # Stop Loss & Target:
    # Placed at consolidation midpoint or opposite boundary, enforcing min stop distance
    cons_mid = (cons_high + cons_low) / 2.0
    min_stop_dist = getattr(cfg, "min_stop_distance_atr", 1.0) * curr_atr
    if direction == "LONG":
        stop_loss = min(cons_mid, curr_close - min_stop_dist)
    else:
        stop_loss = max(cons_mid, curr_close + min_stop_dist)

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
    vol_score = min(1.0, curr_vol / max(vol_avg, 1.0)) * 0.30
    compression_score = max(0.0, 1.0 - compression_ratio) * 0.35
    rr_score = min(1.0, net_r / 3.0) * 0.35
    quality_score = round(vol_score + compression_score + rr_score, 2)

    return Signal(
        direction=direction,
        entry=round(curr_close, 4),
        sl=round(stop_loss, 4),
        tp=round(target_price, 4),
        r_multiple=net_r,
        reason=f"Confirmed {direction} ATR compression breakout outside consolidation [{cons_low:.2f} - {cons_high:.2f}].",
        checklist=checklist.copy(),
        timestamp=str(now),
        diagnostic_code="SIGNAL_CONFIRMED",
        checklist_reasons={k: "PASSED" for k in checklist.keys()},
        strategy_name="ATR_COMPRESSION_BREAKOUT",
        quality_score=quality_score,
    )

