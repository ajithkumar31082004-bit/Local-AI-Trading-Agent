"""Comprehensive unit tests for deterministic position sizing, margin, and crash-persistent daily risk limits."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from risk.position_sizing import size_position, estimate_lot_risk, DailyRiskState
from config.orb_settings import ORBSettings


def test_minimum_lot_exceeding_risk_budget_is_rejected():
    # Budget = 1000 * 0.001 = $1.00 risk budget
    # Entry = 3000, Stop = 2900 (100 pt stop) * 0.001 XAUT = $0.10 + fees
    # If budget is $0.05, per-lot risk exceeds budget
    cfg = ORBSettings(
        risk_pct=0.005,  # 0.005% of 1000 = $0.05 budget
        contract_size_xaut=0.001,
        fee_pct_per_side=0.01,
        spread_pct_round_trip=0.02,
        slippage_pct_round_trip=0.02,
    )
    result = size_position(3000, 2900, 1000, cfg)
    assert not result.approved
    assert result.lots == 0
    assert "exceeds" in result.reason.lower()


def test_position_sizing_contract_spec_and_margin():
    cfg = ORBSettings(
        risk_pct=1.0,  # 1% of 10,000 = $100 budget
        contract_size_xaut=0.001,
        leverage=2.0,
        max_margin_pct_equity=10.0,  # Max margin $1,000
        lot_increment=1,
    )
    # Entry 3000, Stop 2950 (50 pt stop). Gross risk = 50 * 0.001 = $0.05 per lot + costs (~$0.0518)
    res = size_position(3000, 2950, 10000, cfg)
    assert res.approved
    assert res.lots > 0
    assert res.estimated_loss_at_sl <= res.max_risk_cash
    assert res.estimated_margin <= 1000.0


def test_daily_trade_limit_persists_across_instances(tmp_path):
    cfg = ORBSettings(max_trades_per_day=1, daily_loss_limit_pct=1.5, cooldown_after_loss_minutes=60)
    state_file = tmp_path / "risk_state.json"

    state1 = DailyRiskState(state_file, cfg)
    now = datetime(2026, 10, 28, 5, 0, tzinfo=timezone.utc)
    ok, _, _ = state1.can_trade(now)
    assert ok

    # Reserve the one trade slot
    state1.reserve_trade(now)

    # Re-instantiate from disk (simulating application restart)
    state2 = DailyRiskState(state_file, cfg)
    ok, reason, _ = state2.can_trade(now + timedelta(minutes=1))
    assert not ok
    assert "maximum trades" in reason.lower()


def test_loss_triggers_cooldown(tmp_path):
    cfg = ORBSettings(max_trades_per_day=5, daily_loss_limit_pct=5.0, cooldown_after_loss_minutes=60)
    state_file = tmp_path / "risk_state.json"
    state = DailyRiskState(state_file, cfg)

    now = datetime(2026, 10, 28, 5, 0, tzinfo=timezone.utc)
    # Record a loss trade
    state.record_trade(pnl_cash=-50.0, equity_at_start=10000.0, now=now)

    # 10 minutes later, cooldown should still be active
    ok, reason, _ = state.can_trade(now + timedelta(minutes=10))
    assert not ok
    assert "cooldown active" in reason.lower()

    # 65 minutes later, cooldown expires
    ok, _, _ = state.can_trade(now + timedelta(minutes=65))
    assert ok


def test_no_live_order_placement_code_exists():
    """Safety audit gate: verify no live exchange order placement methods are imported or called in ORB modules."""
    orb_files = [
        Path("strategy/orb_ema_volume.py"),
        Path("risk/position_sizing.py"),
        Path("data/delta_candles.py"),
        Path("agents/orb_tools.py"),
        Path("cli.py"),
        Path("backtest/orb_backtest.py"),
    ]
    forbidden_terms = ["place_order", "create_order", "send_order", "/v2/orders", "private/orders"]
    for path in orb_files:
        if path.exists():
            content = path.read_text(encoding="utf-8").lower()
            for term in forbidden_terms:
                assert term not in content, f"Forbidden live order placement term '{term}' found in {path}"
