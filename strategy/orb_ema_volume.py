"""Deterministic ORB + EMA20 + Volume strategy; no LLM trading logic."""
from dataclasses import dataclass, asdict
from datetime import time as time_of_day, datetime
from typing import Any, Optional, Dict
import pandas as pd
from zoneinfo import ZoneInfo
from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import add_orb_indicators, ema_cross_count, ema_slope


@dataclass
class Signal:
    direction: str  # "LONG", "SHORT", or "NO TRADE"
    entry: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    r_multiple: float
    reason: str
    checklist: Dict[str, Any]
    timestamp: Optional[str] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    diagnostic_code: Optional[str] = None
    checklist_reasons: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _no_trade(
    reason: str,
    checklist: Dict[str, Any],
    timestamp=None,
    high=None,
    low=None,
    diagnostic_code: Optional[str] = None,
    checklist_reasons: Optional[Dict[str, str]] = None,
) -> Signal:
    return Signal(
        direction="NO TRADE",
        entry=None,
        sl=None,
        tp=None,
        r_multiple=0.0,
        reason=reason,
        checklist=checklist,
        timestamp=str(timestamp) if timestamp is not None else None,
        orb_high=high,
        orb_low=low,
        diagnostic_code=diagnostic_code,
        checklist_reasons=checklist_reasons or {},
    )


def _prepare(frame: pd.DataFrame, cfg: ORBSettings) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out.columns = [str(c).strip().title() if str(c).lower() in {"open", "high", "low", "close", "volume"} else c for c in out.columns]
    missing = {"Open", "High", "Low", "Close", "Volume"} - set(out.columns)
    if missing:
        raise ValueError(f"Missing OHLCV columns: {sorted(missing)}")

    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is None:
        idx = idx.tz_localize(cfg.timezone, ambiguous="raise", nonexistent="raise")
    else:
        idx = idx.tz_convert(cfg.timezone)
    out.index = idx
    out = out[~out.index.duplicated(keep="last")].sort_index()

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return add_orb_indicators(out, cfg.ema_period, cfg.volume_period, cfg.atr_period)


def calculate_cost_adjusted_target(
    direction: str,
    entry: float,
    sl: float,
    cfg: ORBSettings,
    target_r: float = 2.0,
) -> tuple[float, float]:
    """Calculate target price such that net reward-to-risk ratio is at least target_r after all trading costs.
    
    Returns (target_price, net_r).
    """
    round_trip_cost_rate = (2.0 * cfg.fee_pct_per_side + cfg.spread_pct_round_trip + cfg.slippage_pct_round_trip) / 100.0
    cost_per_unit = entry * round_trip_cost_rate

    raw_risk = abs(entry - sl)
    effective_risk = raw_risk + cost_per_unit
    effective_reward_needed = target_r * effective_risk
    raw_reward = effective_reward_needed + cost_per_unit

    if direction == "LONG":
        target = entry + raw_reward
        net_reward = (target - entry) - cost_per_unit
        net_r = net_reward / max(effective_risk, 1e-12)
    else:
        target = entry - raw_reward
        net_reward = (entry - target) - cost_per_unit
        net_r = net_reward / max(effective_risk, 1e-12)

    return round(target, 4), round(net_r, 4)


def evaluate_orb(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate the latest completed 5m candle strictly using deterministic ORB + EMA20 + Volume rules."""
    base_check = {
        "orb_set": False,
        "breakout_close": False,
        "ema_ok": False,
        "volume_ok": False,
        "retest": False,
        "rejection": False,
        "rr_ok": False,
        "risk_ok": None,
    }

    checklist = dict(base_check)

    def _make_reasons(code: str, custom_reasons: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        r = {
            "orb_set": "PASSED" if checklist.get("orb_set") else code,
            "breakout_close": "PASSED" if checklist.get("breakout_close") else ("WAITING_FOR_ORB" if not checklist.get("orb_set") else "NO_BREAKOUT_CLOSE"),
            "ema_ok": "PASSED" if checklist.get("ema_ok") else ("WAITING_FOR_BREAKOUT" if not checklist.get("breakout_close") else "EMA_FILTER_FAILED"),
            "volume_ok": "PASSED" if checklist.get("volume_ok") else ("WAITING_FOR_BREAKOUT" if not checklist.get("breakout_close") else "VOLUME_BELOW_THRESHOLD"),
            "retest": "PASSED" if checklist.get("retest") else ("WAITING_FOR_BREAKOUT" if not checklist.get("breakout_close") else "RETEST_NOT_CONFIRMED"),
            "rejection": "PASSED" if checklist.get("rejection") else ("WAITING_FOR_RETEST" if not checklist.get("retest") else "REJECTION_CANDLE_NOT_CONFIRMED"),
            "rr_ok": "PASSED" if checklist.get("rr_ok") else ("WAITING_FOR_REJECTION" if not checklist.get("rejection") else "RISK_REWARD_BELOW_MINIMUM"),
            "risk_ok": "PENDING",
        }
        if custom_reasons:
            r.update(custom_reasons)
        return r

    if cfg.target_r < 2.0:
        return _no_trade(
            "NO TRADE: configured target offers less than the required 1:2 reward-to-risk ratio.",
            checklist,
            diagnostic_code="TARGET_R_BELOW_MINIMUM",
            checklist_reasons=_make_reasons("TARGET_R_BELOW_MINIMUM", {"rr_ok": "TARGET_R_BELOW_MINIMUM"}),
        )
    if cfg.orb_minutes != 15:
        return _no_trade(
            "NO TRADE: strategy requires a 15-minute opening range (three 5m candles).",
            checklist,
            diagnostic_code="INVALID_ORB_MINUTES",
            checklist_reasons=_make_reasons("INVALID_ORB_MINUTES", {"orb_set": "INVALID_ORB_MINUTES"}),
        )
    if frame.empty:
        return _no_trade(
            "NO TRADE: no candle data available.",
            checklist,
            diagnostic_code="NO_CANDLE_DATA",
            checklist_reasons=_make_reasons("NO_CANDLE_DATA", {"orb_set": "NO_CANDLE_DATA"}),
        )

    df = _prepare(frame, cfg)
    min_required_history = max(cfg.ema_period + 1, cfg.volume_period + 1)
    if len(df) < min_required_history:
        return _no_trade(
            f"NO TRADE: insufficient candle history for EMA{cfg.ema_period} and {cfg.volume_period}-candle average volume (need at least {min_required_history} candles).",
            checklist,
            df.index[-1] if not df.empty else None,
            diagnostic_code="INSUFFICIENT_CANDLES",
            checklist_reasons=_make_reasons("INSUFFICIENT_CANDLES", {"orb_set": "INSUFFICIENT_CANDLES"}),
        )

    now = df.index[-1]
    session_t = time_of_day.fromisoformat(cfg.session_start)
    session_start = now.normalize() + pd.Timedelta(hours=session_t.hour, minutes=session_t.minute)

    # If the latest candle is before today's session start, trading has not begun
    if now < session_start:
        return _no_trade(
            f"NO TRADE: trading session has not started yet (session start is {cfg.session_start} {cfg.timezone}).",
            checklist,
            now,
            diagnostic_code="SESSION_NOT_STARTED",
            checklist_reasons=_make_reasons("SESSION_NOT_STARTED", {
                "orb_set": "SESSION_NOT_STARTED",
                "breakout_close": "WAITING_FOR_SESSION",
                "ema_ok": "WAITING_FOR_SESSION",
                "volume_ok": "WAITING_FOR_SESSION",
                "retest": "WAITING_FOR_SESSION",
                "rejection": "WAITING_FOR_SESSION",
                "rr_ok": "WAITING_FOR_SESSION",
            }),
        )

    session_end = session_start + pd.Timedelta(minutes=cfg.orb_minutes)
    session = df[(df.index >= session_start) & (df.index < session_start + pd.Timedelta(days=1))]
    opening = session[(session.index >= session_start) & (session.index < session_end)]

    expected_bars = cfg.orb_minutes // 5  # 3 candles
    expected_index = pd.date_range(start=session_start, periods=expected_bars, freq="5min", tz=cfg.timezone)

    # Check if opening range is complete (need 3 completed 5m bars: 00, +5m, +10m)
    # The 3rd bar ends at session_end. The candle index is its start time.
    if (now + pd.Timedelta(minutes=5) < session_end) or len(opening) < expected_bars or not expected_index.isin(opening.index).all():
        return _no_trade(
            f"NO TRADE: opening range incomplete or missing candles: requires completed 5m bars at {session_start.strftime('%H:%M')}, "
            f"{(session_start + pd.Timedelta(minutes=5)).strftime('%H:%M')}, and {(session_start + pd.Timedelta(minutes=10)).strftime('%H:%M')} ({cfg.timezone}).",
            checklist,
            now,
            diagnostic_code="ORB_WINDOW_INCOMPLETE",
            checklist_reasons=_make_reasons("ORB_WINDOW_INCOMPLETE", {
                "orb_set": "ORB_WINDOW_INCOMPLETE",
                "breakout_close": "WAITING_FOR_ORB",
                "ema_ok": "WAITING_FOR_ORB",
                "volume_ok": "WAITING_FOR_ORB",
                "retest": "WAITING_FOR_ORB",
                "rejection": "WAITING_FOR_ORB",
                "rr_ok": "WAITING_FOR_ORB",
            }),
        )

    opening = opening.reindex(expected_index)
    if opening[["Open", "High", "Low", "Close", "Volume"]].isna().any().any():
        return _no_trade(
            "NO TRADE: one or more opening-range candles contain missing or invalid data.",
            checklist,
            now,
            diagnostic_code="ORB_WINDOW_INCOMPLETE",
            checklist_reasons=_make_reasons("ORB_WINDOW_INCOMPLETE", {"orb_set": "ORB_DATA_INVALID"}),
        )

    orb_high = float(opening["High"].max())
    orb_low = float(opening["Low"].min())

    checklist["orb_set"] = True

    post_orb = session[session.index >= session_end]
    if post_orb.empty:
        return _no_trade(
            "NO TRADE: opening range is established; waiting for a confirmed breakout close.",
            checklist,
            now,
            orb_high,
            orb_low,
            diagnostic_code="NO_BREAKOUT_CLOSE",
            checklist_reasons=_make_reasons("NO_BREAKOUT_CLOSE", {
                "orb_set": "PASSED",
                "breakout_close": "NO_BREAKOUT_CLOSE",
            }),
        )

    breakout_dir = None
    breakout_i = None
    failed_reason = None
    failed_code = None
    latest_signal = None
    session_positions = {ts: i for i, ts in enumerate(df.index)}

    for ts, row in post_orb.iterrows():
        i = session_positions[ts]
        close = float(row["Close"])
        opn = float(row["Open"])
        high = float(row["High"])
        low = float(row["Low"])
        vol = float(row["Volume"])

        ema_now = row["EMA20"]
        ema_prev = df["EMA20"].iloc[i - 1] if i > 0 else float("nan")
        vol_avg = row["VolumeAvg20Prior"]
        atr_now = row["ATR"]

        if pd.isna(ema_now) or pd.isna(ema_prev) or pd.isna(vol_avg) or vol_avg <= 0:
            continue

        crosses = ema_cross_count(df["Close"].iloc[max(0, i - 9):i + 1], df["EMA20"].iloc[max(0, i - 9):i + 1], 10)

        # Step 1: Look for initial breakout candle close
        if breakout_dir is None:
            # Wick-only breakout does not count; candle MUST close beyond ORB
            direction = "LONG" if close > orb_high else "SHORT" if close < orb_low else None
            if direction is None:
                continue

            ema_aligned = (close > ema_now and ema_now > ema_prev) if direction == "LONG" else (close < ema_now and ema_now < ema_prev)
            vol_aligned = vol > float(vol_avg)

            checklist.update({
                "breakout_close": True,
                "ema_ok": ema_aligned,
                "volume_ok": vol_aligned,
            })

            # Check chop filter
            if crosses >= cfg.max_ema_crosses:
                failed_reason = f"NO TRADE: price chopped around EMA20 ({crosses} crosses in the last 10 candles; max allowed {cfg.max_ema_crosses - 1})."
                failed_code = "EMA_CHOP_DETECTED"
                break
            if not ema_aligned:
                failed_reason = "NO TRADE: breakout candle lacks EMA20 alignment or positive/negative slope."
                failed_code = "EMA_FILTER_FAILED"
                break
            if not vol_aligned:
                failed_reason = "NO TRADE: breakout candle volume did not exceed previous 20-candle average."
                failed_code = "VOLUME_BELOW_THRESHOLD"
                break

            extension = (close - orb_high) if direction == "LONG" else (orb_low - close)
            if pd.notna(atr_now) and extension > cfg.max_extension_atr * float(atr_now):
                failed_reason = "NO TRADE: breakout candle is too extended from the opening range; avoid chasing."
                failed_code = "PRICE_EXTENDED"
                break

            breakout_dir = direction
            breakout_i = i
            continue

        # Invalidation: A breakout that closes back inside the ORB range invalidates the setup
        if orb_low <= close <= orb_high:
            failed_reason = "NO TRADE: price closed back inside the opening range, invalidating the breakout."
            failed_code = "BREAKOUT_INVALIDATED_RETURNED_INSIDE"
            break

        # Invalidation: Too many bars passed without a retest
        if (i - breakout_i) > cfg.max_bars_after_breakout:
            failed_reason = f"NO TRADE: retest did not occur within {cfg.max_bars_after_breakout} bars of breakout."
            failed_code = "RETEST_TIMEOUT"
            break

        if i <= breakout_i:
            continue

        # Step 2: Retest & Rejection candle verification
        if breakout_dir == "LONG":
            touches_level = low <= (orb_high + cfg.retest_tolerance)
            holds_level = close > orb_high
            is_bullish_close = close > opn
            lower_wick = min(opn, close) - low
            body = max(abs(close - opn), 1e-9)
            rejection_formed = is_bullish_close and (lower_wick >= body * cfg.rejection_wick_body_ratio)
            ema_trend_ok = (close > float(ema_now)) and (float(ema_now) > float(ema_prev))
            too_extended = pd.notna(atr_now) and ((close - orb_high) > cfg.max_extension_atr * float(atr_now))
        else:
            touches_level = high >= (orb_low - cfg.retest_tolerance)
            holds_level = close < orb_low
            is_bearish_close = close < opn
            upper_wick = high - max(opn, close)
            body = max(abs(close - opn), 1e-9)
            rejection_formed = is_bearish_close and (upper_wick >= body * cfg.rejection_wick_body_ratio)
            ema_trend_ok = (close < float(ema_now)) and (float(ema_now) < float(ema_prev))
            too_extended = pd.notna(atr_now) and ((orb_low - close) > cfg.max_extension_atr * float(atr_now))

        if touches_level and holds_level and rejection_formed:
            checklist.update({
                "retest": True,
                "rejection": True,
                "ema_ok": bool(ema_trend_ok),
                "breakout_close": True,
            })

            if crosses >= cfg.max_ema_crosses:
                failed_reason = f"NO TRADE: price chopped around EMA20 ({crosses} crosses in the last 10 candles)."
                failed_code = "EMA_CHOP_DETECTED"
                break
            if not ema_trend_ok:
                failed_reason = "NO TRADE: EMA20 trend alignment or slope failed on rejection candle."
                failed_code = "EMA_FILTER_FAILED"
                break
            if too_extended:
                failed_reason = "NO TRADE: rejection candle closed too extended from the breakout level."
                failed_code = "PRICE_EXTENDED"
                break

            # Calculate Stop Loss: beyond retest extreme or invalidation level + buffer
            if breakout_dir == "LONG":
                stop_loss = min(low, orb_high) - cfg.stop_buffer
            else:
                stop_loss = max(high, orb_low) + cfg.stop_buffer

            # Calculate cost-adjusted Take Profit for at least 2.0R net
            target_price, net_r = calculate_cost_adjusted_target(
                breakout_dir,
                entry=close,
                sl=stop_loss,
                cfg=cfg,
                target_r=max(cfg.target_r, 2.0),
            )

            if net_r < 2.0:
                failed_reason = f"NO TRADE: net reward-to-risk ratio ({net_r:.2f}R) after fees and slippage is below 1:2."
                failed_code = "RISK_REWARD_BELOW_MINIMUM"
                break

            checklist["rr_ok"] = True
            latest_signal = Signal(
                direction=breakout_dir,
                entry=round(close, 4),
                sl=round(stop_loss, 4),
                tp=round(target_price, 4),
                r_multiple=net_r,
                reason="Confirmed ORB breakout + EMA20 slope + volume confirmation + retest rejection candle close.",
                checklist=checklist.copy(),
                timestamp=str(ts),
                orb_high=orb_high,
                orb_low=orb_low,
                diagnostic_code="SIGNAL_CONFIRMED",
                checklist_reasons={k: "PASSED" for k in checklist.keys() if k != "risk_ok"},
            )
            break

    # If a signal formed on the latest completed candle, return it
    if latest_signal and latest_signal.timestamp == str(now):
        return latest_signal

    if failed_reason:
        return _no_trade(
            failed_reason,
            checklist,
            now,
            orb_high,
            orb_low,
            diagnostic_code=failed_code,
            checklist_reasons=_make_reasons(failed_code or "FILTER_REJECTED"),
        )

    last_row = df.iloc[-1]
    last_close = float(last_row["Close"])
    last_vol = float(last_row["Volume"])
    last_vol_avg = float(last_row["VolumeAvg20Prior"]) if pd.notna(last_row["VolumeAvg20Prior"]) else float("inf")

    if breakout_dir is None:
        if orb_low <= last_close <= orb_high:
            reason = "NO TRADE: price remains inside the opening range."
            dcode = "NO_BREAKOUT_CLOSE"
        elif last_vol <= last_vol_avg:
            reason = "NO TRADE: breakout candle volume did not exceed previous 20-candle average."
            dcode = "VOLUME_BELOW_THRESHOLD"
        else:
            reason = "NO TRADE: waiting for a valid completed candle breakout close."
            dcode = "NO_BREAKOUT_CLOSE"
    else:
        reason = f"NO TRADE: {breakout_dir.lower()} breakout confirmed; waiting for a completed retest and rejection candle."
        dcode = "RETEST_NOT_CONFIRMED"

    return _no_trade(
        reason,
        checklist,
        now,
        orb_high,
        orb_low,
        diagnostic_code=dcode,
        checklist_reasons=_make_reasons(dcode),
    )
