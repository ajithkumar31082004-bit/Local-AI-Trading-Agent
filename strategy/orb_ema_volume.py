"""Deterministic ORB + EMA20 + Volume strategy; no LLM decisions."""
from dataclasses import dataclass, asdict
from datetime import time as time_of_day, timedelta
from typing import Any, Optional
import pandas as pd
from zoneinfo import ZoneInfo
from config.orb_settings import ORBSettings, settings
from indicators.orb_indicators import add_orb_indicators, ema_cross_count

@dataclass
class Signal:
    direction: str
    entry: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    r_multiple: float
    reason: str
    checklist: dict[str, Any]
    timestamp: Optional[str] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _no_trade(reason: str, checklist: dict[str, Any], timestamp=None, high=None, low=None) -> Signal:
    return Signal("NO TRADE", None, None, None, 0.0, reason, checklist,
                  str(timestamp) if timestamp is not None else None, high, low)

def _prepare(frame: pd.DataFrame, cfg: ORBSettings) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out.columns = [str(c).strip().title() if str(c).lower() in {"open","high","low","close","volume"} else c for c in out.columns]
    missing = {"Open","High","Low","Close","Volume"} - set(out.columns)
    if missing:
        raise ValueError(f"Missing OHLCV columns: {sorted(missing)}")
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is None:
        idx = idx.tz_localize(cfg.timezone, ambiguous="raise", nonexistent="raise")
    else:
        idx = idx.tz_convert(cfg.timezone)
    out.index = idx
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ["Open","High","Low","Close","Volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Open","High","Low","Close","Volume"])
    return add_orb_indicators(out, cfg.ema_period, cfg.volume_period, cfg.atr_period)

def evaluate_orb(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Evaluate the latest completed candle. Session start is local time in cfg.timezone."""
    base_check = {"orb_set": False, "breakout_close": False, "ema_ok": False, "volume_ok": False,
                  "retest": False, "rejection": False, "rr_ok": False, "risk_ok": None}
    if cfg.target_r < 2.0:
        return _no_trade("NO TRADE: configured target offers less than the required 1:2 reward-to-risk ratio.", base_check)
    if cfg.orb_minutes != 15:
        return _no_trade("NO TRADE: v1 requires a 15-minute opening range (three 5m candles).", base_check)
    if frame.empty:
        return _no_trade("No candle data available.", base_check)
    df = _prepare(frame, cfg)
    if len(df) < max(cfg.ema_period + 2, cfg.volume_period + 2, 25):
        return _no_trade("Insufficient candles for EMA20 and prior-20-candle volume average.", base_check, df.index[-1])
    now = df.index[-1]
    session_t = time_of_day.fromisoformat(cfg.session_start)
    session_start = now.normalize() + pd.Timedelta(hours=session_t.hour, minutes=session_t.minute)
    session_end = session_start + pd.Timedelta(minutes=cfg.orb_minutes)
    session = df[(df.index >= session_start) & (df.index < session_start + pd.Timedelta(days=1))]
    opening = session[(session.index >= session_start) & (session.index < session_end)]
    expected = cfg.orb_minutes // 5
    expected_index = pd.date_range(start=session_start, periods=expected, freq="5min")
    if now + pd.Timedelta(minutes=5) < session_end or len(opening) < expected or not expected_index.isin(opening.index).all():
        return _no_trade(f"Opening range incomplete or missing candles: need bars at {cfg.session_start}, +5m and +10m ({cfg.timezone}).", base_check, now)
    opening = opening.reindex(expected_index)
    if opening[["Open", "High", "Low", "Close", "Volume"]].isna().any().any():
        return _no_trade("NO TRADE: one or more opening-range candles are missing.", base_check, now)
    orb_high, orb_low = float(opening["High"].max()), float(opening["Low"].min())
    checklist = dict(base_check)
    checklist["orb_set"] = True
    session = session[session.index >= session_end]
    if session.empty:
        return _no_trade("Opening range is set; waiting for a breakout close.", checklist, now, orb_high, orb_low)

    # Strategy is reconstructed from today's candles so live and backtest use identical rules.
    breakout_dir = None
    breakout_i = None
    failed_reason = None
    latest_signal = None
    session_positions = {ts: i for i, ts in enumerate(df.index)}
    for ts, row in session.iterrows():
        i = session_positions[ts]
        close, opn = float(row["Close"]), float(row["Open"])
        ema_now = row["EMA20"]
        ema_prev = df["EMA20"].iloc[i-1] if i > 0 else float("nan")
        vol_avg = row["VolumeAvg20Prior"]
        atr_now = row["ATR"]
        if pd.isna(ema_now) or pd.isna(ema_prev) or pd.isna(vol_avg) or vol_avg <= 0:
            continue
        crosses = ema_cross_count(df["Close"].iloc[max(0, i-9):i+1], df["EMA20"].iloc[max(0, i-9):i+1], 10)
        if breakout_dir is None:
            direction = "LONG" if close > orb_high else "SHORT" if close < orb_low else None
            if direction is None:
                continue
            checklist.update({"breakout_close": True, "ema_ok": (close > ema_now and ema_now > ema_prev) if direction == "LONG" else (close < ema_now and ema_now < ema_prev),
                              "volume_ok": float(row["Volume"]) > float(vol_avg)})
            if crosses > cfg.max_ema_crosses:
                failed_reason = f"NO TRADE: price chopped around EMA20 ({crosses} crosses in last 10 candles)."
                break
            if not checklist["ema_ok"]:
                failed_reason = "NO TRADE: breakout close lacks EMA20 direction/slope confirmation."
                break
            if not checklist["volume_ok"]:
                failed_reason = "NO TRADE: breakout candle volume did not exceed the previous 20-candle average."
                break
            extension = (close - orb_high) if direction == "LONG" else (orb_low - close)
            if pd.isna(atr_now) or extension > cfg.max_extension_atr * float(atr_now):
                failed_reason = "NO TRADE: breakout candle is too extended; avoid chasing."
                break
            breakout_dir, breakout_i = direction, i
            continue
        # A breakout that closes back inside the range invalidates the setup for this session.
        if orb_low <= close <= orb_high:
            failed_reason = "NO TRADE: breakout fell back inside the opening range."
            break
        if i - breakout_i > cfg.max_bars_after_breakout:
            failed_reason = "NO TRADE: retest did not occur within the configured window."
            break
        if i <= breakout_i:
            continue
        if breakout_dir == "LONG":
            touches = float(row["Low"]) <= orb_high + cfg.retest_tolerance
            holds = close > orb_high
            bullish = close > opn
            wick = min(opn, close) - float(row["Low"])
            body = max(abs(close-opn), 1e-9)
            rejection = bullish and wick >= body * cfg.rejection_wick_body_ratio
            extended = not pd.isna(atr_now) and (close - orb_high) > cfg.max_extension_atr * float(atr_now)
            ema_ok = close > float(ema_now) and float(ema_now) > float(ema_prev)
        else:
            touches = float(row["High"]) >= orb_low - cfg.retest_tolerance
            holds = close < orb_low
            bearish = close < opn
            wick = float(row["High"]) - max(opn, close)
            body = max(abs(close-opn), 1e-9)
            rejection = bearish and wick >= body * cfg.rejection_wick_body_ratio
            extended = not pd.isna(atr_now) and (orb_low - close) > cfg.max_extension_atr * float(atr_now)
            ema_ok = close < float(ema_now) and float(ema_now) < float(ema_prev)
        if touches and holds and rejection:
            checklist.update({"retest": True, "rejection": True, "ema_ok": bool(ema_ok), "breakout_close": True})
            if crosses > cfg.max_ema_crosses:
                failed_reason = f"NO TRADE: price chopped around EMA20 ({crosses} crosses in last 10 candles)."
                break
            if not ema_ok:
                failed_reason = "NO TRADE: EMA20 trend filter failed on retest candle."
                break
            if extended:
                failed_reason = "NO TRADE: retest candle is too extended; avoid chasing."
                break
            if breakout_dir == "LONG":
                stop = min(float(row["Low"]), orb_high) - cfg.stop_buffer
                risk = close - stop
                target = close + cfg.target_r * risk
            else:
                stop = max(float(row["High"]), orb_low) + cfg.stop_buffer
                risk = stop - close
                target = close - cfg.target_r * risk
            if risk <= 0 or target <= 0:
                failed_reason = "NO TRADE: invalid stop/target geometry."
                break
            checklist["rr_ok"] = cfg.target_r >= 2.0
            latest_signal = Signal(breakout_dir, close, stop, target, cfg.target_r,
                                   "ORB breakout + EMA20 slope + volume confirmation + retest rejection.",
                                   checklist.copy(), str(ts), orb_high, orb_low)
            break
    if latest_signal and latest_signal.timestamp == str(now):
        return latest_signal
    if failed_reason:
        return _no_trade(failed_reason, checklist, now, orb_high, orb_low)
    last = df.iloc[-1]
    if breakout_dir is None:
        if orb_low <= float(last["Close"]) <= orb_high:
            reason = "NO TRADE: price still inside the opening range."
        elif float(last["Volume"]) <= (float(last["VolumeAvg20Prior"]) if pd.notna(last["VolumeAvg20Prior"]) else float("inf")):
            reason = "NO TRADE: latest candle volume is not confirming a breakout."
        else:
            reason = "NO TRADE: waiting for a valid close-confirmed breakout."
    else:
        reason = f"NO TRADE: {breakout_dir.lower()} breakout confirmed; waiting for a valid retest and rejection candle."
    return _no_trade(reason, checklist, now, orb_high, orb_low)
