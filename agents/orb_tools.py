"""Callable deterministic tools for the existing conversational agent."""
from pathlib import Path
from typing import Optional, Dict, Any
import pandas as pd
from config.orb_settings import settings
from data.delta_candles import fetch_5m_candles
from data.csv_loader import load_candles_csv
from strategy.orb_ema_volume import evaluate_orb
from risk.position_sizing import size_position, DailyRiskState
from backtest.orb_backtest import run_backtest
from journal.orb_journal import ORBJournal


def orb_current_signal(lookback_candles: int = 500, equity: float = 10000.0) -> Dict[str, Any]:
    """Tool: evaluate latest completed XAUTUSD 5m candle using deterministic rules."""
    frame = fetch_5m_candles(settings.symbol, lookback_candles)
    signal = evaluate_orb(frame)
    allowed, guard_reason, _ = DailyRiskState().can_trade(frame.index[-1].to_pydatetime())
    data = signal.to_dict()
    data["daily_risk_guard"] = {"allowed": allowed, "reason": guard_reason}
    if signal.direction in {"LONG", "SHORT"}:
        risk = size_position(signal.entry, signal.sl, equity)
        data["risk"] = risk.to_dict()
        data["checklist"]["risk_ok"] = risk.approved
        if not allowed or not risk.approved:
            reason = guard_reason if not allowed else risk.reason
            data.update({
                "direction": "NO TRADE",
                "entry": None,
                "sl": None,
                "tp": None,
                "r_multiple": 0.0,
                "reason": f"NO TRADE: {reason}",
            })
    ORBJournal().log(
        "signal" if data["direction"] in {"LONG", "SHORT"} else "skipped_setup",
        data,
        extra={"daily_risk_guard": data.get("daily_risk_guard"), "risk": data.get("risk")},
    )
    return data


def explain_no_trade(lookback_candles: int = 500) -> Dict[str, Any]:
    """Tool: return the exact engine reason and checklist, not an LLM-generated price."""
    data = orb_current_signal(lookback_candles)
    return {
        "direction": data["direction"],
        "reason": data["reason"],
        "checklist": data["checklist"],
        "orb_high": data.get("orb_high"),
        "orb_low": data.get("orb_low"),
        "timestamp": data.get("timestamp"),
    }


def show_today_orb(lookback_candles: int = 500) -> Dict[str, Any]:
    """Tool: display today's ORB High, Low, session range timestamps, and completion status."""
    frame = fetch_5m_candles(settings.symbol, lookback_candles)
    signal = evaluate_orb(frame)
    return {
        "symbol": settings.symbol,
        "session_start": settings.session_start,
        "timezone": settings.timezone,
        "orb_high": signal.orb_high,
        "orb_low": signal.orb_low,
        "orb_complete": bool(signal.checklist.get("orb_set", False)),
        "current_price": float(frame["Close"].iloc[-1]) if not frame.empty else None,
        "last_candle_timestamp": str(frame.index[-1]) if not frame.empty else None,
        "checklist": signal.checklist,
        "direction": signal.direction,
        "reason": signal.reason,
    }


def backtest_orb_csv(path: str, initial_equity: float = 10000.0) -> Dict[str, Any]:
    """Tool: replay a local OHLCV CSV using the same ORB strategy rules."""
    frame = load_candles_csv(Path(path), settings.timezone)
    return run_backtest(frame, initial_equity).to_dict()


def assess_orb_risk(entry: float, stop: float, equity: float) -> Dict[str, Any]:
    """Tool: deterministic minimum-lot, risk and margin calculation."""
    return size_position(float(entry), float(stop), float(equity)).to_dict()


def journal_summary(limit: int = 20) -> Dict[str, Any]:
    """Tool: summarize recent saved signals, skipped setups and paper results."""
    rows = ORBJournal().recent(limit)
    return {"count": len(rows), "events": rows}


def explain_last_signal(lookback_candles: int = 500) -> Dict[str, Any]:
    """Tool: retrieve and explain the most recent strategy signal or evaluation."""
    recent_events = ORBJournal().recent(10)
    for ev in recent_events:
        if ev.get("direction") in {"LONG", "SHORT"}:
            return {
                "direction": ev.get("direction"),
                "entry": ev.get("entry"),
                "sl": ev.get("stop_loss"),
                "tp": ev.get("take_profit"),
                "reason": ev.get("reason"),
                "timestamp": ev.get("timestamp"),
                "checklist": ev.get("checklist_json"),
                "source": "journal",
            }
    # If no past signal in journal, evaluate current state
    curr = orb_current_signal(lookback_candles)
    return {
        "direction": curr.get("direction"),
        "entry": curr.get("entry"),
        "sl": curr.get("sl"),
        "tp": curr.get("tp"),
        "reason": curr.get("reason"),
        "timestamp": curr.get("timestamp"),
        "checklist": curr.get("checklist"),
        "source": "current_market",
    }


def generate_orb_explanation(tool_data: Dict[str, Any], query_type: str, ollama_client: Optional[Any] = None) -> str:
    """Generate concise, factual explanation using llama3.2:1b (temp=0.2).
    
    The model is strictly restricted to explaining structured numbers and cannot override logic.
    """
    sys_prompt = (
        "You are an AI Quantitative Trading Assistant explaining deterministic Python strategy outputs. "
        "Strict rules: "
        "1. Never invent prices, levels, or indicators not in the data. "
        "2. If an attribute is missing or unknown, say 'not in the data'. "
        "3. Never decide trades or perform mental math. Explain only the provided data. "
        "4. Keep explanations concise, clear, and factual under 3 sentences. "
        "5. State that this is an educational alert-only tool."
    )
    user_prompt = f"Topic: {query_type}\nDeterministic Data:\n{tool_data}\nSynthesize an explanation for the user."

    if ollama_client:
        reply = ollama_client.generate_response(sys_prompt, user_prompt, max_tokens=150, temperature=0.2)
        if reply:
            return reply

    # Deterministic fallback when Ollama is offline
    direction = tool_data.get("direction", "NO TRADE")
    reason = tool_data.get("reason", "No reason provided.")
    return (
        f"**{direction}** — {reason} "
        f"(ORB High: {tool_data.get('orb_high', 'not in the data')}, "
        f"ORB Low: {tool_data.get('orb_low', 'not in the data')}). "
        "Educational alert-only tool, not financial advice."
    )


ORB_TOOL_REGISTRY = {
    "orb_current_signal": orb_current_signal,
    "explain_no_trade": explain_no_trade,
    "show_today_orb": show_today_orb,
    "explain_last_signal": explain_last_signal,
    "backtest_orb_csv": backtest_orb_csv,
    "assess_orb_risk": assess_orb_risk,
    "journal_summary": journal_summary,
}


def run_orb_tool(name: str, **kwargs) -> Dict[str, Any]:
    """Allowlisted tool dispatcher for deterministic ORB/risk/backtest/journal functions."""
    if name not in ORB_TOOL_REGISTRY:
        raise ValueError(f"Unknown ORB tool: {name}")
    return ORB_TOOL_REGISTRY[name](**kwargs)
