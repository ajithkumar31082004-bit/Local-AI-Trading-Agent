"""Explicit System and Market Regime Readiness State Machine for Strategy Intelligence V2.

Tracks readiness transitions:
- DISCONNECTED: Market data feed stopped or disconnected.
- CONNECTING: Initializing network connection to Delta Exchange India.
- BOOTSTRAPPING: Fetching historical OHLCV candles from Delta REST API.
- WARMING_UP: Ingestion active, but history or indicators (EMA50, ADX14) are stabilizing.
- READY: Full history and all active indicators are validated, stable, and ready for strategy evaluation.
- DEGRADED: Connection lost or feed stalled after previously being active.

STRICT CONSTRAINTS:
- Do NOT classify an insufficient-data market as a valid trading regime (e.g. UNCERTAIN).
- System must explicitly report which data or indicator requirement is preventing readiness.
- No trading is permitted unless state is strictly READY.
"""
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, Any, List, Optional
import pandas as pd

from config.orb_settings import ORBSettings, settings as default_settings
from indicators.orb_indicators import (
    ema,
    atr,
    adx,
    vwap,
    resample_to_15m,
)


class ReadinessState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    WARMING_UP = "WARMING_UP"
    READY = "READY"
    DEGRADED = "DEGRADED"


@dataclass
class SystemReadiness:
    """Explicit report of system and indicator readiness."""
    state: ReadinessState
    is_ready: bool
    completed_5m_count: int
    required_5m_count: int
    completed_15m_count: int
    required_15m_count: int
    indicator_status: Dict[str, bool]
    missing_requirements: List[str]
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


def evaluate_system_readiness(
    df_5m: pd.DataFrame,
    is_ws_connected: bool = True,
    is_feed_running: bool = True,
    is_bootstrapping: bool = False,
    is_stale: bool = False,
    cfg: Optional[ORBSettings] = None,
) -> SystemReadiness:
    """Evaluate whether historical candle depth and indicator warm-up satisfy readiness.
    
    Requirements for READY:
    1. Feed is running and WebSocket is connected.
    2. Not in active REST bootstrapping.
    3. At least min_completed_5m_bars (default 150) completed 5m candles.
    4. At least min_completed_15m_bars (default 50) completed 15m resampled candles.
    5. 15m EMA50 is valid (non-NaN float).
    6. 15m EMA20 is valid (non-NaN float).
    7. 15m ADX14 is valid (non-NaN float).
    8. 5m EMA50 and ATR14 are valid (non-NaN float).
    """
    settings = cfg or default_settings
    req_5m = getattr(settings, "min_completed_5m_bars", 150)
    req_15m = getattr(settings, "min_completed_15m_bars", 50)

    missing: List[str] = []
    indicators: Dict[str, bool] = {
        "15m_EMA50": False,
        "15m_EMA20": False,
        "15m_ADX14": False,
        "15m_ATR14": False,
        "5m_EMA50": False,
        "5m_EMA20": False,
        "5m_ATR14": False,
        "5m_VWAP": False,
    }

    # 1. Connection-level checks
    if not is_feed_running and df_5m.empty:
        return SystemReadiness(
            state=ReadinessState.DISCONNECTED,
            is_ready=False,
            completed_5m_count=0,
            required_5m_count=req_5m,
            completed_15m_count=0,
            required_15m_count=req_15m,
            indicator_status=indicators,
            missing_requirements=["Feed is not running"],
            details={"connection": "DISCONNECTED"},
        )

    if is_bootstrapping:
        return SystemReadiness(
            state=ReadinessState.BOOTSTRAPPING,
            is_ready=False,
            completed_5m_count=len(df_5m),
            required_5m_count=req_5m,
            completed_15m_count=0,
            required_15m_count=req_15m,
            indicator_status=indicators,
            missing_requirements=["Bootstrapping historical candles via REST API"],
            details={"connection": "BOOTSTRAPPING"},
        )

    if is_feed_running and not is_ws_connected and df_5m.empty:
        return SystemReadiness(
            state=ReadinessState.CONNECTING,
            is_ready=False,
            completed_5m_count=0,
            required_5m_count=req_5m,
            completed_15m_count=0,
            required_15m_count=req_15m,
            indicator_status=indicators,
            missing_requirements=["WebSocket connecting to exchange"],
            details={"connection": "CONNECTING"},
        )

    # 2. Candle history count checks
    completed_5m_cnt = len(df_5m)
    if completed_5m_cnt < req_5m:
        missing.append(f"Insufficient 5m candles: {completed_5m_cnt}/{req_5m}")

    df_15m = resample_to_15m(df_5m) if not df_5m.empty else pd.DataFrame()
    completed_15m_cnt = len(df_15m)
    if completed_15m_cnt < req_15m:
        missing.append(f"Insufficient 15m resampled candles: {completed_15m_cnt}/{req_15m} for EMA50 baseline")

    # 3. Indicator calculation and non-NaN validation
    if completed_15m_cnt >= req_15m:
        try:
            ema50_15 = ema(df_15m["Close"], 50)
            ema20_15 = ema(df_15m["Close"], 20)
            adx_15 = adx(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)
            atr_15 = atr(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)

            indicators["15m_EMA50"] = pd.notna(ema50_15.iloc[-1]) if not ema50_15.empty else False
            indicators["15m_EMA20"] = pd.notna(ema20_15.iloc[-1]) if not ema20_15.empty else False
            indicators["15m_ADX14"] = pd.notna(adx_15.iloc[-1]) if not adx_15.empty else False
            indicators["15m_ATR14"] = pd.notna(atr_15.iloc[-1]) if not atr_15.empty else False

            if not indicators["15m_EMA50"]:
                missing.append("15m EMA50 contains NaN (insufficient warm-up)")
            if not indicators["15m_EMA20"]:
                missing.append("15m EMA20 contains NaN")
            if not indicators["15m_ADX14"]:
                missing.append("15m ADX14 contains NaN")
            if not indicators["15m_ATR14"]:
                missing.append("15m ATR14 contains NaN")
        except Exception as exc:
            missing.append(f"15m indicator calculation error: {exc}")

    if completed_5m_cnt >= 50:
        try:
            ema50_5 = ema(df_5m["Close"], 50)
            ema20_5 = ema(df_5m["Close"], 20)
            atr_5 = atr(df_5m["High"], df_5m["Low"], df_5m["Close"], 14)
            vwap_5 = vwap(df_5m["High"], df_5m["Low"], df_5m["Close"], df_5m["Volume"])

            indicators["5m_EMA50"] = pd.notna(ema50_5.iloc[-1]) if not ema50_5.empty else False
            indicators["5m_EMA20"] = pd.notna(ema20_5.iloc[-1]) if not ema20_5.empty else False
            indicators["5m_ATR14"] = pd.notna(atr_5.iloc[-1]) if not atr_5.empty else False
            indicators["5m_VWAP"] = pd.notna(vwap_5.iloc[-1]) if not vwap_5.empty else False

            if not indicators["5m_EMA50"]:
                missing.append("5m EMA50 contains NaN")
            if not indicators["5m_EMA20"]:
                missing.append("5m EMA20 contains NaN")
            if not indicators["5m_ATR14"]:
                missing.append("5m ATR14 contains NaN")
        except Exception as exc:
            missing.append(f"5m indicator calculation error: {exc}")

    # 4. Connection drop or staleness detection (DEGRADED)
    if (is_feed_running and not is_ws_connected and not df_5m.empty) or is_stale:
        if is_stale:
            missing.append("Market data feed is stale (no updates received within freshness threshold)")
        if is_feed_running and not is_ws_connected:
            missing.append("WebSocket disconnected during streaming")
        return SystemReadiness(
            state=ReadinessState.DEGRADED,
            is_ready=False,
            completed_5m_count=completed_5m_cnt,
            required_5m_count=req_5m,
            completed_15m_count=completed_15m_cnt,
            required_15m_count=req_15m,
            indicator_status=indicators,
            missing_requirements=missing,
            details={"connection": "STALE_OR_DISCONNECTED", "is_stale": is_stale, "ws_connected": is_ws_connected},
        )

    # 5. Final State Resolution
    if missing:
        return SystemReadiness(
            state=ReadinessState.WARMING_UP,
            is_ready=False,
            completed_5m_count=completed_5m_cnt,
            required_5m_count=req_5m,
            completed_15m_count=completed_15m_cnt,
            required_15m_count=req_15m,
            indicator_status=indicators,
            missing_requirements=missing,
            details={"connection": "CONNECTED_WARMING_UP"},
        )

    return SystemReadiness(
        state=ReadinessState.READY,
        is_ready=True,
        completed_5m_count=completed_5m_cnt,
        required_5m_count=req_5m,
        completed_15m_count=completed_15m_cnt,
        required_15m_count=req_15m,
        indicator_status=indicators,
        missing_requirements=[],
        details={"connection": "CONNECTED_READY"},
    )

