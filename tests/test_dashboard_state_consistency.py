"""Unit tests for dashboard state consistency, active runner state resolution, and staleness detection."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from config.orb_settings import ORBSettings
from services.delta_paper_broker import DeltaPaperBroker
from services.dashboard_state_service import (
    resolve_dashboard_paper_state,
    load_historical_14d_reference,
    DashboardAccountState,
)


def test_dashboard_and_runner_read_same_intended_state(tmp_path):
    """Verify that dashboard and runner bind to the same intended state file."""
    state_file = tmp_path / "paper_forward_24h_state.json"
    live_status_file = tmp_path / "paper_forward_24h_live_status.json"

    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    runner_broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # Runner writes live heartbeat
    live_payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ws_connected": True,
        "ticks_count": 12500,
        "completed_5m": 455,
        "completed_15m": 151,
        "regime": "TRENDING_BULLISH",
    }
    with open(live_status_file, "w", encoding="utf-8") as f:
        json.dump(live_payload, f)

    # Dashboard resolves state
    dash_state = resolve_dashboard_paper_state(
        custom_state_path=state_file,
        custom_live_status_path=live_status_file,
    )

    assert dash_state.exists is True
    assert dash_state.is_stale is False
    assert dash_state.state_file_path == state_file
    assert dash_state.cash_balance == runner_broker.cash_balance
    assert dash_state.equity == runner_broker.get_account_summary()["equity"]
    assert dash_state.has_open_position is False
    assert dash_state.warning_message is None
    assert dash_state.live_telemetry["ticks_count"] == 12500


def test_historical_14d_results_not_mixed_with_active_test(tmp_path):
    """Verify that historical 14-day study results are isolated from the active 24h runner."""
    state_file = tmp_path / "paper_forward_24h_state.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    runner_broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    dash_state = resolve_dashboard_paper_state(
        custom_state_path=state_file,
        base_dir=tmp_path,
    )

    # Active runner has 0 closed trades
    assert dash_state.closed_trade_count == 0
    assert len(dash_state.closed_trades) == 0

    # Historical reference is loaded separately and not merged
    hist_ref = load_historical_14d_reference()
    if hist_ref:
        assert hist_ref["total_trades"] == 7
        assert hist_ref["win_rate_pct"] == 28.57
        assert hist_ref["net_pnl"] == -0.0121
        # Crucial invariant: active dash_state trades do NOT include historical trades
        assert len(dash_state.closed_trades) != hist_ref["total_trades"]


def test_missing_state_file_produces_warning(tmp_path):
    """Verify that missing state files generate an explicit warning without silent fallbacks."""
    missing_file = tmp_path / "non_existent_paper_state.json"

    dash_state = resolve_dashboard_paper_state(
        custom_state_path=missing_file,
    )

    assert dash_state.exists is False
    assert dash_state.is_stale is True
    assert dash_state.broker is None
    assert dash_state.warning_message is not None
    assert "Active paper runner state file not found" in dash_state.warning_message
    assert str(missing_file) in dash_state.warning_message


def test_stale_state_files_identified(tmp_path):
    """Verify that stale runner heartbeats or state files are flagged."""
    state_file = tmp_path / "paper_forward_24h_state.json"
    live_status_file = tmp_path / "paper_forward_24h_live_status.json"

    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # Stale heartbeat: 600 seconds ago (threshold is 300s)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    with open(live_status_file, "w", encoding="utf-8") as f:
        json.dump({"updated_at": stale_time, "ws_connected": True}, f)

    dash_state = resolve_dashboard_paper_state(
        custom_state_path=state_file,
        custom_live_status_path=live_status_file,
        stale_threshold_seconds=300,
    )

    assert dash_state.exists is True
    assert dash_state.is_stale is True
    assert dash_state.warning_message is not None
    assert "Active runner heartbeat is stale" in dash_state.warning_message


def test_equity_and_pnl_consistency(tmp_path):
    """Verify exact formula: Starting Equity + Realized P&L + Unrealized P&L = Equity."""
    state_file = tmp_path / "test_consistency_state.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # Execute one trade
    broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="VWAP_EMA"
    )
    _, _, closed = broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    dash_state = resolve_dashboard_paper_state(custom_state_path=state_file)

    assert dash_state.starting_equity == 10000.0
    assert dash_state.realized_pnl == closed["net_pnl"]
    assert dash_state.unrealized_pnl == 0.0
    expected_equity = round(10000.0 + closed["net_pnl"], 4)
    assert dash_state.equity == expected_equity
    assert dash_state.cash_balance == expected_equity
    assert dash_state.closed_trade_count == 1


def test_restart_recovery_preserves_account_identity_and_balance(tmp_path):
    """Verify that process restarts preserve open position state and account identity."""
    state_file = tmp_path / "restart_state.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")

    # Process 1 opens position
    b1 = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    b1.open_simulated_trade(
        symbol="XAUTUSD", direction="SHORT", entry_price=4050.0, stop_loss=4070.0, take_profit=4010.0, lots=1, strategy_name="VWAP_EMA"
    )

    # Process 2 (Dashboard reading after restart)
    dash_state = resolve_dashboard_paper_state(custom_state_path=state_file)

    assert dash_state.exists is True
    assert dash_state.has_open_position is True
    assert dash_state.open_position is not None
    assert dash_state.open_position["direction"] == "SHORT"
    assert dash_state.open_position["lots"] == 1
    assert dash_state.account_name == "XAUTUSD Paper Account (restart_state)"

