"""Callable deterministic tools for the existing conversational agent."""
from pathlib import Path
import pandas as pd
from config.orb_settings import settings
from data.delta_candles import fetch_5m_candles
from data.csv_loader import load_candles_csv
from strategy.orb_ema_volume import evaluate_orb
from risk.position_sizing import size_position, DailyRiskState
from backtest.orb_backtest import run_backtest

def orb_current_signal(lookback_candles: int = 500, equity: float = 10000.0) -> dict:
    """Tool: evaluate latest completed XAUTUSD 5m candle using deterministic rules."""
    frame=fetch_5m_candles(settings.symbol,lookback_candles)
    signal=evaluate_orb(frame)
    allowed, guard_reason, _ = DailyRiskState().can_trade(frame.index[-1].to_pydatetime())
    data=signal.to_dict()
    data["daily_risk_guard"]={"allowed":allowed,"reason":guard_reason}
    if signal.direction in {"LONG","SHORT"}:
        risk = size_position(signal.entry, signal.sl, equity)
        data["risk"] = risk.to_dict()
        data["checklist"]["risk_ok"] = risk.approved
        if not allowed or not risk.approved:
            reason = guard_reason if not allowed else risk.reason
            data.update({"direction":"NO TRADE","entry":None,"sl":None,"tp":None,"r_multiple":0.0,
                         "reason":f"NO TRADE: {reason}"})
    return data

def explain_no_trade(lookback_candles: int = 500) -> dict:
    """Tool: return the exact engine reason and checklist, not an LLM-generated price."""
    data=orb_current_signal(lookback_candles)
    return {"direction":data["direction"],"reason":data["reason"],"checklist":data["checklist"],
            "orb_high":data.get("orb_high"),"orb_low":data.get("orb_low"),"timestamp":data.get("timestamp")}

def backtest_orb_csv(path: str, initial_equity: float = 10000.0) -> dict:
    """Tool: replay a local OHLCV CSV using the same ORB strategy rules."""
    frame=load_candles_csv(Path(path),settings.timezone)
    return run_backtest(frame,initial_equity).to_dict()

def assess_orb_risk(entry: float, stop: float, equity: float) -> dict:
    """Tool: deterministic minimum-lot, risk and margin calculation."""
    return size_position(float(entry),float(stop),float(equity)).to_dict()
