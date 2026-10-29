"""Unit tests for services/delta_risk.py tiny-account risk guard and blocking rules."""
from datetime import datetime, timezone
import pytest
from services.delta_risk import (
    evaluate_delta_trade,
    compute_lot_risk_and_costs,
    DeltaDailyRiskStore,
    CONTRACT_SIZE_XAUT,
)


def test_contract_size_specification():
    # 1 Lot must equal 0.001 XAUT
    assert CONTRACT_SIZE_XAUT == 0.001


def test_compute_lot_risk_and_costs():
    # Entry = 2650, SL = 2640 ($10 distance). 1 Lot = 0.001 XAUT -> gross loss = $0.01
    res = compute_lot_risk_and_costs(entry=2650.0, sl=2640.0, lots=1, usd_inr_rate=86.5, leverage=100.0)
    assert res["gross_loss_usd"] == 0.01
    assert res["gross_loss_inr"] == round(0.01 * 86.5, 2)  # ~0.87 INR
    assert res["fees_inr"] > 0
    assert res["spread_slippage_inr"] > 0
    assert res["total_loss_inr"] > res["gross_loss_inr"]
    # Margin for $2.65 notional with 100x leverage = $0.0265 -> ~₹2.29
    assert res["margin_required_inr"] == round(2.65 / 100.0 * 86.5, 2)


def test_minimum_lot_loss_exceeds_risk_budget_blocked(tmp_path):
    store = DeltaDailyRiskStore(db_path=tmp_path / "test_risk.db")
    # Entry = 2650, SL = 2600 ($50 distance!).
    # 1 Lot gross loss = $50 * 0.001 = $0.05 => ~₹4.33
    # If max risk per trade is ₹2.00, it must be BLOCKED!
    decision = evaluate_delta_trade(
        entry=2650.0,
        sl=2600.0,
        account_equity_inr=100.0,
        available_margin_inr=50.0,
        max_risk_per_trade_inr=2.0,
        daily_loss_limit_inr=15.0,
        store=store,
    )
    assert not decision.approved
    assert "Minimum 1-lot loss at SL" in decision.reason
    assert "exceeds max risk budget" in decision.reason


def test_daily_loss_limit_hit_blocked(tmp_path):
    store = DeltaDailyRiskStore(db_path=tmp_path / "test_risk.db")
    today_str = datetime.now(timezone.utc).date().isoformat()
    # Simulate prior trade today with ₹16 loss (exceeding ₹15 limit)
    store.record_trade_result(today_str, pnl_inr=-16.0)

    decision = evaluate_delta_trade(
        entry=2650.0,
        sl=2645.0,
        account_equity_inr=100.0,
        available_margin_inr=50.0,
        max_risk_per_trade_inr=5.0,
        daily_loss_limit_inr=15.0,
        store=store,
    )
    assert not decision.approved
    assert "Daily loss limit reached" in decision.reason or "Maximum 1 trade" in decision.reason


def test_one_trade_per_day_limit_blocked(tmp_path):
    store = DeltaDailyRiskStore(db_path=tmp_path / "test_risk.db")
    today_str = datetime.now(timezone.utc).date().isoformat()
    store.record_trade_taken(today_str)

    decision = evaluate_delta_trade(
        entry=2650.0,
        sl=2645.0,
        account_equity_inr=100.0,
        available_margin_inr=50.0,
        max_risk_per_trade_inr=5.0,
        daily_loss_limit_inr=15.0,
        store=store,
    )
    assert not decision.approved
    assert "Maximum 1 trade per day limit already reached" in decision.reason


def test_required_margin_exceeds_available_blocked(tmp_path):
    store = DeltaDailyRiskStore(db_path=tmp_path / "test_risk.db")
    # 1 Lot required margin is ~₹2.29. If available margin is only ₹1.00, it must be BLOCKED!
    decision = evaluate_delta_trade(
        entry=2650.0,
        sl=2645.0,
        account_equity_inr=100.0,
        available_margin_inr=1.00,
        max_risk_per_trade_inr=5.0,
        daily_loss_limit_inr=15.0,
        store=store,
    )
    assert not decision.approved
    assert "Required margin" in decision.reason or "exceeds available margin" in decision.reason


def test_tiny_account_trade_approved(tmp_path):
    store = DeltaDailyRiskStore(db_path=tmp_path / "test_risk.db")
    # Healthy parameters on ₹100 account with ₹5 risk budget and ₹20 available margin
    decision = evaluate_delta_trade(
        entry=2650.0,
        sl=2646.0,  # $4 stop distance => ~$0.004 gross => ~₹0.35 loss
        account_equity_inr=100.0,
        available_margin_inr=20.0,
        max_risk_per_trade_inr=5.0,
        daily_loss_limit_inr=15.0,
        store=store,
    )
    assert decision.approved
    assert decision.lots >= 1
    assert decision.loss_inr <= 5.0
    assert decision.margin_required_inr <= 20.0
    assert "Risk checks passed" in decision.reason


def test_sqlite_persistence_across_instances(tmp_path):
    db = tmp_path / "test_persist.db"
    store1 = DeltaDailyRiskStore(db_path=db)
    store1.record_trade_taken("2026-05-01")
    store1.record_trade_result("2026-05-01", -10.5)

    store2 = DeltaDailyRiskStore(db_path=db)
    stats = store2.get_day_stats("2026-05-01")
    assert stats["trade_count"] == 1
    assert stats["realized_pnl_inr"] == -10.5
    assert stats["realized_loss_inr"] == 10.5

    # If another trade is recorded with is_new_trade=True:
    store2.record_trade_result("2026-05-01", 5.0, is_new_trade=True)
    stats_updated = store2.get_day_stats("2026-05-01")
    assert stats_updated["trade_count"] == 2
    assert stats_updated["realized_pnl_inr"] == -5.5
