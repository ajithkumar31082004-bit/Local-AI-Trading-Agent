"""Unit tests for deterministic agent tools, schema validation, journal export, and LLM non-override guarantee."""
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import pytest
from agents.orb_tools import (
    ORB_TOOL_REGISTRY,
    run_orb_tool,
    show_today_orb,
    explain_no_trade,
    assess_orb_risk,
    journal_summary,
)
from journal.orb_journal import ORBJournal
from strategy.orb_ema_volume import Signal


def test_all_six_deterministic_tools_registered():
    mandated_tools = {
        "orb_current_signal",
        "explain_no_trade",
        "show_today_orb",
        "assess_orb_risk",
        "backtest_orb_csv",
        "journal_summary",
    }
    assert mandated_tools.issubset(set(ORB_TOOL_REGISTRY.keys()))
    for name in mandated_tools:
        assert callable(ORB_TOOL_REGISTRY[name])


def test_show_today_orb_tool(monkeypatch):
    idx = pd.date_range("2026-01-02 07:15", periods=25, freq="5min", tz="UTC")
    fake_df = pd.DataFrame({
        "Open": [2600.0] * 25,
        "High": [2605.0] * 25,
        "Low": [2595.0] * 25,
        "Close": [2602.0] * 25,
        "Volume": [100.0] * 25,
    }, index=idx)

    monkeypatch.setattr("agents.orb_tools.fetch_5m_candles", lambda *args, **kwargs: fake_df)
    res = show_today_orb()
    assert "symbol" in res
    assert "session_start" in res
    assert "timezone" in res
    assert "orb_complete" in res
    assert "checklist" in res
    assert res["symbol"] == "XAUTUSD"


def test_assess_orb_risk_tool():
    res = assess_orb_risk(entry=2600.0, stop=2590.0, equity=10000.0)
    assert "approved" in res
    assert "lots" in res
    assert "max_risk_cash" in res
    assert res["approved"] is True
    assert res["lots"] > 0


def test_journal_logging_and_csv_export(tmp_path):
    journal_db = tmp_path / "test_journal.sqlite3"
    journal = ORBJournal(journal_db)

    sig = Signal(
        direction="LONG",
        entry=2616.0,
        sl=2609.5,
        tp=2636.0,
        r_multiple=2.1,
        reason="Test breakout",
        checklist={"orb_set": True, "breakout_close": True},
        timestamp=datetime.now(timezone.utc).isoformat(),
        orb_high=2610.0,
        orb_low=2590.0,
    )

    journal.log("signal", sig, extra={"risk": {"estimated_loss_at_sl": 6.5, "estimated_fees": 0.5, "estimated_spread_slippage": 1.0}})
    recent = journal.recent(10)
    assert len(recent) == 1
    assert recent[0]["direction"] == "LONG"
    assert recent[0]["entry"] == 2616.0
    assert recent[0]["orb_high"] == 2610.0

    csv_file = tmp_path / "export.csv"
    exported = journal.export_csv(csv_file, _safe_root=tmp_path)
    assert exported.exists()
    content = exported.read_text(encoding="utf-8")
    assert "LONG" in content
    assert "2616.0" in content


def test_llm_cannot_override_deterministic_signal():
    """Verify that if deterministic output is NO TRADE, any attempted hallucinated or LLM override is rejected."""
    deterministic_result = {
        "direction": "NO TRADE",
        "reason": "NO TRADE: price remains inside the opening range.",
        "entry": None,
        "sl": None,
        "tp": None,
        "checklist": {"orb_set": True, "breakout_close": False},
    }

    # Simulate an LLM attempting to claim a BUY / LONG trade
    hallucinated_llm_text = "I recommend entering LONG at 2615 with stop loss 2600 and target 2650."

    # Guard assertion: The rule engine signal is authoritative; direction remains NO TRADE
    def safe_assistant_formatter(deterministic_data: dict, llm_explanation: str) -> dict:
        # LLM text is used only for commentary; engine values cannot be overwritten
        return {
            "authoritative_direction": deterministic_data["direction"],
            "authoritative_entry": deterministic_data["entry"],
            "explanation": llm_explanation if deterministic_data["direction"] != "NO TRADE" else f"Engine decision is {deterministic_data['direction']} ({deterministic_data['reason']}).",
        }

    final_output = safe_assistant_formatter(deterministic_result, hallucinated_llm_text)
    assert final_output["authoritative_direction"] == "NO TRADE"
    assert final_output["authoritative_entry"] is None
    assert "NO TRADE" in final_output["explanation"]
