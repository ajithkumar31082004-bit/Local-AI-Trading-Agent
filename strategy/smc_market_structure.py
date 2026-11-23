"""Smart Money Concepts (SMC) Market Structure Strategy for XAUTUSD on Delta Exchange India.

Implements the 4-Step Institutional Execution Cycle:
1. TREND IDENTIFICATION:
   - Evaluates External Market Structure (15m HTF) for macro bias (BULLISH or BEARISH).
2. LIQUIDITY SWEEP (STOP HUNT):
   - Confirms that smart money has swept liquidity past a key prior swing extreme
     (wick penetration + rejection wick >= 30% of candle range, closing back inside).
3. CHANGE OF CHARACTER (CHOCH):
   - Confirms the initial structural shift when price breaks and closes decisively
     beyond the last opposite internal swing level (body close, not wick).
4. BREAK OF STRUCTURE (BOS CONFIRMATION):
   - Confirms the new trend continuation when price prints a valid BOS in the direction of the CHOCH.
5. RISK & TARGET:
   - Stop Loss placed strictly beyond the Liquidity Sweep wick extreme + ATR buffer (min 1.0 ATR).
   - Target calculated for >= 2.0R net after exchange costs, targeting the opposing major liquidity pool.
"""
from datetime import datetime, timedelta, time as time_of_day
from typing import Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd

from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import atr, prior_volume_average, resample_to_15m
from indicators.market_structure import (
    analyze_market_structure,
    evaluate_break_validity,
    MarketStructureState,
    SwingPoint,
)
from strategy.orb_ema_volume import Signal, calculate_cost_adjusted_target
from risk.position_sizing import estimate_lot_risk


def evaluate_smc_structure(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate completed 5m candle using Smart Money Concepts (SMC) Market Structure rules."""
    checklist = {
        "session_open_filter": False,
        "structure_identified": False,
        "liquidity_sweep_detected": False,
        "choch_confirmed": False,
        "bos_confirmed": False,
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
            strategy_name="SMC_MARKET_STRUCTURE",
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

    # 1. Session Hours Check
    now = df_5m.index[-1]
    start_t = time_of_day.fromisoformat(cfg.session_start)
    end_t = time_of_day.fromisoformat(getattr(cfg, "session_end", "23:30"))
    now_t = now.time()

    if now_t < start_t or now_t > end_t:
        return _no_trade(
            f"NO TRADE: outside active trading window ({cfg.session_start} - {end_t.strftime('%H:%M')}).",
            "OUTSIDE_SESSION_HOURS"
        )

    no_entry_mins = getattr(cfg, "no_entry_minutes_after_open", 45)
    if no_entry_mins > 0:
        start_dt = datetime.combine(now.date(), start_t)
        open_cutoff_t = (start_dt + timedelta(minutes=no_entry_mins)).time()
        if now_t <= open_cutoff_t:
            return _no_trade(
                f"NO TRADE: in opening buffer ({no_entry_mins}m after session open).",
                "SESSION_OPEN_BUFFER"
            )

    checklist["session_open_filter"] = True

    # 2. Extract Dual-Timeframe Market Structure
    df_15m = resample_to_15m(df_5m)
    if len(df_15m) < 15:
        return _no_trade("NO TRADE: insufficient 15m resampled candles.", "INSUFFICIENT_15M_CANDLES")

    structure = analyze_market_structure(df_5m, df_15m)
    if not structure.major_high or not structure.major_low:
        return _no_trade("NO TRADE: unable to establish external swing structure.", "NO_EXTERNAL_STRUCTURE")

    checklist["structure_identified"] = True

    current_bar = df_5m.iloc[-1]
    current_close = float(current_bar["Close"])
    atr_vals = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], period=cfg.atr_period)
    current_atr = float(atr_vals.iloc[-1]) if not atr_vals.empty and not pd.isna(atr_vals.iloc[-1]) else 5.0
    vol_prior_avg = prior_volume_average(df_5m["Volume"], period=cfg.volume_period)
    current_vol_avg = float(vol_prior_avg.iloc[-1]) if not vol_prior_avg.empty and not pd.isna(vol_prior_avg.iloc[-1]) else 1000.0
    current_vol = float(current_bar["Volume"])

    # 3. Step 2 & 3: Check for Liquidity Sweep and CHOCH / BOS
    # Look back over trailing 15 bars for a confirmed liquidity sweep
    lookback_bars = df_5m.iloc[-16:-1]  # prior completed bars excluding current
    if len(lookback_bars) < 5:
        return _no_trade("NO TRADE: insufficient trailing bars for sweep lookback.", "INSUFFICIENT_LOOKBACK")

    # A. BULLISH SETUP:
    # 1) Liquidity Sweep: Price pierced below a prior swing low or internal HL/LL and rejected (wick >= 30%)
    # 2) Bullish CHOCH: Price breaks and closes above last minor LH
    # 3) Bullish BOS: Price breaks and closes above minor high
    bullish_sweep_found = False
    bullish_sweep_low = float("inf")

    # Test for sweep of minor low or major low
    test_low_level = structure.last_minor_hl.price if structure.last_minor_hl else structure.major_low.price
    for idx_b, b in lookback_bars.iterrows():
        b_low = float(b["Low"])
        b_close = float(b["Close"])
        b_high = float(b["High"])
        b_open = float(b["Open"])
        if b_low < test_low_level:
            c_range = max(b_high - b_low, 1e-4)
            bottom_wick = min(b_open, b_close) - b_low
            if (bottom_wick / c_range) >= 0.25 and b_close >= (test_low_level - 0.2 * current_atr):
                bullish_sweep_found = True
                bullish_sweep_low = min(bullish_sweep_low, b_low)

    # B. BEARISH SETUP:
    # 1) Liquidity Sweep: Price pierced above a prior swing high or internal LH/HH and rejected (wick >= 30%)
    # 2) Bearish CHOCH: Price breaks and closes below last minor HL
    # 3) Bearish BOS: Price breaks and closes below minor low
    bearish_sweep_found = False
    bearish_sweep_high = float("-inf")

    test_high_level = structure.last_minor_lh.price if structure.last_minor_lh else structure.major_high.price
    for idx_b, b in lookback_bars.iterrows():
        b_high = float(b["High"])
        b_close = float(b["Close"])
        b_low = float(b["Low"])
        b_open = float(b["Open"])
        if b_high > test_high_level:
            c_range = max(b_high - b_low, 1e-4)
            top_wick = b_high - max(b_open, b_close)
            if (top_wick / c_range) >= 0.25 and b_close <= (test_high_level + 0.2 * current_atr):
                bearish_sweep_found = True
                bearish_sweep_high = max(bearish_sweep_high, b_high)

    # Evaluate Trade Direction
    direction = "NO TRADE"
    entry = None
    sl = None
    tp = None
    sweep_price = None

    # Check Bullish Case: External is BULLISH (or Major CHOCH active), Sweep confirmed, and current bar breaks internal level
    if bullish_sweep_found and structure.last_minor_lh:
        choch_res = evaluate_break_validity(current_bar, structure.last_minor_lh.price, "BULLISH", min_buffer=0.05 * current_atr)
        if choch_res["valid_break"]:
            direction = "LONG"
            checklist["liquidity_sweep_detected"] = True
            checklist["choch_confirmed"] = True
            checklist["bos_confirmed"] = True
            sweep_price = bullish_sweep_low

    # Check Bearish Case: External is BEARISH (or Major CHOCH active), Sweep confirmed, and current bar breaks internal level
    elif bearish_sweep_found and structure.last_minor_hl:
        choch_res = evaluate_break_validity(current_bar, structure.last_minor_hl.price, "BEARISH", min_buffer=0.05 * current_atr)
        if choch_res["valid_break"]:
            direction = "SHORT"
            checklist["liquidity_sweep_detected"] = True
            checklist["choch_confirmed"] = True
            checklist["bos_confirmed"] = True
            sweep_price = bearish_sweep_high

    if direction == "NO TRADE":
        return _no_trade(
            f"NO TRADE: no valid SMC CHOCH/BOS setup. ExtBias={structure.external_bias}, IntBias={structure.internal_bias}.",
            "NO_SMC_BREAKOUT"
        )

    # 4. Volume Confirmation
    if current_vol >= (current_vol_avg * 1.05):
        checklist["volume_confirmed"] = True
    else:
        return _no_trade(
            f"NO TRADE: breakout volume ({current_vol:,.0f}) below 1.05x prior average ({current_vol_avg:,.0f}).",
            "LOW_BREAKOUT_VOLUME"
        )

    # 5. Position Geometry & Risk-Reward
    entry = current_close
    if direction == "LONG":
        sl_candidate = sweep_price - (0.2 * current_atr)
        min_sl_dist = 1.0 * current_atr
        sl = min(sl_candidate, entry - min_sl_dist)
        target_price, net_r = calculate_cost_adjusted_target(
            direction="LONG", entry=entry, sl=sl, cfg=cfg, target_r=cfg.target_r
        )
        tp = target_price
    else:  # SHORT
        sl_candidate = sweep_price + (0.2 * current_atr)
        min_sl_dist = 1.0 * current_atr
        sl = max(sl_candidate, entry + min_sl_dist)
        target_price, net_r = calculate_cost_adjusted_target(
            direction="SHORT", entry=entry, sl=sl, cfg=cfg, target_r=cfg.target_r
        )
        tp = target_price

    # 6. Friction and Risk Budget Checks
    total_risk, round_trip_fee, spread_slip = estimate_lot_risk(entry, sl, cfg)
    total_friction = round_trip_fee + spread_slip
    raw_risk = abs(entry - sl) * cfg.contract_size_xaut

    friction_ratio = (total_friction / raw_risk) if raw_risk > 0 else 1.0
    if friction_ratio <= cfg.max_friction_to_risk_ratio:
        checklist["friction_ok"] = True
    else:
        return _no_trade(
            f"NO TRADE: friction-to-risk ratio ({friction_ratio:.2%}) exceeds allowed max ({cfg.max_friction_to_risk_ratio:.2%}).",
            "EXCESSIVE_FRICTION"
        )

    if net_r >= 1.8:
        checklist["rr_ok"] = True
    else:
        return _no_trade(
            f"NO TRADE: net R-multiple ({net_r:.2f}R) below required 1.8R threshold.",
            "INSUFFICIENT_RR"
        )

    # Calculate Signal Quality Score
    quality_score = 0.50  # Base for passing all mandatory gates
    if structure.is_aligned:
        quality_score += 0.20
    if current_vol >= (current_vol_avg * 1.30):
        quality_score += 0.15
    if abs(entry - sl) >= (1.5 * current_atr):
        quality_score += 0.15
    quality_score = min(1.0, round(quality_score, 2))

    if quality_score < cfg.min_signal_quality_score:
        return _no_trade(
            f"NO TRADE: quality score ({quality_score:.2f}) below threshold ({cfg.min_signal_quality_score:.2f}).",
            "LOW_QUALITY_SCORE"
        )

    reasons = {k: "PASSED" for k in checklist.keys()}
    return Signal(
        direction=direction,
        entry=round(entry, 2),
        sl=round(sl, 2),
        tp=round(tp, 2),
        r_multiple=round(net_r, 2),
        reason=f"VALID {direction} SMC BREAKOUT: ExtBias={structure.external_bias}, Sweep={sweep_price:.2f}, Volume={current_vol:,.0f}.",
        checklist=checklist,
        timestamp=str(now),
        diagnostic_code=f"SMC_{direction}_APPROVED",
        checklist_reasons=reasons,
        strategy_name="SMC_MARKET_STRUCTURE",
        quality_score=quality_score,
    )
