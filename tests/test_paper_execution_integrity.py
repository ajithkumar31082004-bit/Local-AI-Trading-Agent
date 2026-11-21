"""Phase 3.24 — PAPER Execution Integrity & Controlled Validation Tests.

Validates the complete PAPER execution engine after Phase 3.23:
1. One-lot order acceptance.
2. Two-lot and larger order rejection at final broker boundary.
3. Partial-fill accounting.
4. Stop-loss execution.
5. Take-profit execution.
6. Maker and taker fee accounting.
7. Slippage accounting without double counting.
8. Cash and equity conservation.
9. Duplicate-order prevention.
10. Restart and recovery from saved PAPER state.
11. Missing or stale market data handling.
12. Invalid contract quantity rejection.
13. Rejected orders must not change cash, equity, positions or fills.
14. Historical 484-lot incident remains unchanged.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import pytest
import pandas as pd
from unittest.mock import MagicMock

from config.orb_settings import ORBSettings
from risk.position_size_guard import (
    APPROVED_MAX_PAPER_LOTS,
    APPROVED_MIN_PAPER_LOTS,
    STANDARD_CONTRACT_SIZE_XAUT,
    PositionSizeViolation,
    validate_contract_quantity,
    validate_paper_order_lots,
)
from services.delta_paper_broker import DeltaPaperBroker
from services.delta_paper_bot import DeltaPaperBot
from regime.readiness import ReadinessState, SystemReadiness


# =============================================================================
# 1. One-lot order acceptance
# =============================================================================

def test_1_one_lot_order_acceptance(tmp_path):
    """Verify that a valid one-lot paper order is cleanly accepted, fills at 0.001 XAUT, and records open position."""
    state_file = tmp_path / "test_1_state.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        fill_ratio=1.0,
        strategy_name="VWAP_EMA",
    )

    assert ok is True
    assert pos is not None
    assert pos["lots"] == 1
    assert pos["requested_lots"] == 1
    assert pos["unfilled_lots"] == 0
    assert pos["contract_qty"] == 0.001
    assert broker.open_position is not None
    assert broker.open_position["lots"] == 1
    assert broker.open_position["contract_qty"] == 0.001
    assert broker.open_position["direction"] == "LONG"


# =============================================================================
# 2. Two-lot and larger order rejection at final broker boundary
# =============================================================================

@pytest.mark.parametrize("oversized_lots", [2, 3, 5, 10, 100, 484, 495])
def test_2_oversized_order_rejection_at_broker_boundary(tmp_path, oversized_lots):
    """Verify that any order requesting > 1 lot is deterministically rejected by the Central Position Size Guard."""
    state_file = tmp_path / f"test_2_{oversized_lots}_state.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=oversized_lots,
        strategy_name="ORB",
    )

    assert ok is False
    assert pos is None
    assert broker.open_position is None
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["reason"] == "SECURITY_SIZING_VIOLATION"
    assert broker.last_order["violation_code"] == "OVERSIZED_QUANTITY"
    assert broker.cash_balance == 10000.0


# =============================================================================
# 3. Partial-fill accounting
# =============================================================================

def test_3_partial_fill_accounting_discrete_lots(tmp_path):
    """Verify discrete integer fill accounting under the 1-lot paper regime."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")

    # 3a. 100% fill on 1 lot -> COMPLETE FILL (1 lot = 0.001 XAUT)
    b1 = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "fill_100.json"), initial_equity=10000.0)
    ok1, _, pos1 = b1.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=1.0, strategy_name="TEST"
    )
    assert ok1 is True
    assert pos1["lots"] == 1
    assert pos1["contract_qty"] == 0.001
    assert pos1["unfilled_lots"] == 0

    # 3b. 50% fill on 1 lot (0.5 lot < 1 integer lot) -> ZERO_FILL (no position opened)
    b2 = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "fill_50.json"), initial_equity=10000.0)
    ok2, msg2, pos2 = b2.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=0.50, strategy_name="TEST"
    )
    assert ok2 is False
    assert pos2 is None
    assert b2.open_position is None
    assert b2.last_order["status"] == "ZERO_FILL"
    assert b2.last_order["filled_lots"] == 0
    assert b2.last_order["unfilled_lots"] == 1
    assert b2.cash_balance == 10000.0

    # 3c. Zero fill ratio (0.0) -> ZERO_FILL
    b3 = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "fill_0.json"), initial_equity=10000.0)
    ok3, msg3, pos3 = b3.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=0.0, strategy_name="TEST"
    )
    assert ok3 is False
    assert pos3 is None
    assert b3.open_position is None
    assert b3.last_order["status"] == "ZERO_FILL"

    # 3d. Ratio > 1.0 (clamped to 1.0, never overfilling)
    b4 = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "fill_over.json"), initial_equity=10000.0)
    ok4, _, pos4 = b4.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=1.75, strategy_name="TEST"
    )
    assert ok4 is True
    assert pos4["lots"] == 1
    assert pos4["unfilled_lots"] == 0


# =============================================================================
# 4. Stop-loss execution
# =============================================================================

def test_4_stop_loss_execution(tmp_path):
    """Verify that touching or breaching stop loss executes as a TAKER exit with taker fees and slippage."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "test_4_state.json"), initial_equity=10000.0)

    # Open LONG @ 4000.0, SL @ 3980.0, TP @ 4040.0
    ok, _, pos = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="VWAP_EMA"
    )
    assert ok is True

    # Price bar drops: low reaches 3975.0 (below SL 3980.0)
    closed = broker.evaluate_open_position_sl_tp(current_price=3978.0, high=3990.0, low=3975.0)

    assert closed is not None
    assert closed["exit_reason"] == "SL"
    assert closed["exit_order_type"] == "TAKER"
    assert closed["lots"] == 1
    assert closed["contract_qty"] == 0.001
    assert broker.open_position is None
    assert len(broker.closed_trades) == 1
    assert closed["gross_pnl"] < 0
    assert closed["net_pnl"] < closed["gross_pnl"]  # Friction reduces net P&L


# =============================================================================
# 5. Take-profit execution
# =============================================================================

def test_5_take_profit_execution(tmp_path):
    """Verify that touching take profit executes as a MAKER limit exit with maker fees and proper spread/slippage."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "test_5_state.json"), initial_equity=10000.0)

    # Open LONG @ 4000.0, SL @ 3980.0, TP @ 4040.0
    ok, _, pos = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="VWAP_EMA"
    )
    assert ok is True

    # Price bar rises: high reaches 4045.0 (above TP 4040.0)
    closed = broker.evaluate_open_position_sl_tp(current_price=4042.0, high=4045.0, low=4010.0)

    assert closed is not None
    assert closed["exit_reason"] == "TP"
    assert closed["exit_order_type"] == "MAKER"
    assert closed["lots"] == 1
    assert closed["contract_qty"] == 0.001
    assert broker.open_position is None
    assert len(broker.closed_trades) == 1
    assert closed["gross_pnl"] > 0
    assert closed["net_pnl"] > 0


# =============================================================================
# 6. Maker and taker fee accounting
# =============================================================================

def test_6_maker_and_taker_fee_accounting(tmp_path):
    """Verify fee accounting adheres strictly to maker (0.01%) and taker (0.05%) rates and GST."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        maker_fee_pct=0.01,
        taker_fee_pct=0.05,
        fee_scenario="MAKER_TAKER",
        apply_gst=True,
        gst_rate_pct=18.0,
    )
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "test_6_state.json"), initial_equity=10000.0)

    # Open trade with LIMIT order (Maker fee: 0.01%)
    ok, _, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        order_type="LIMIT",
        strategy_name="TEST",
    )
    assert ok is True
    # Notional = 4000.0 * 0.001 = $4.00
    # Maker fee = $4.00 * 0.0001 = $0.0004
    assert pos["fee_rate_pct"] == 0.01
    assert pos["entry_fee"] == pytest.approx(0.0004, abs=0.0001)
    assert pos["entry_gst"] == pytest.approx(0.0004 * 0.18, abs=0.0001)

    # Close via TAKER market exit (Taker fee: 0.05%)
    ok_close, _, closed = broker.close_simulated_trade(
        exit_price=3980.0, exit_reason="SL", order_type="TAKER"
    )
    assert ok_close is True
    assert closed["exit_order_type"] == "TAKER"
    # Exit notional ~ 3980.0 * 0.001 = $3.98
    # Taker fee = $3.98 * 0.0005 = $0.0020
    assert closed["exit_fee"] == pytest.approx(0.0020, abs=0.0002)
    assert closed["brokerage_fee"] == pytest.approx(pos["entry_fee"] + closed["exit_fee"], abs=0.0001)
    assert closed["gst_applied"] is True


# =============================================================================
# 7. Slippage accounting without double counting
# =============================================================================

def test_7_slippage_accounting_no_double_counting(tmp_path):
    """Verify spread and slippage costs are accurately captured without double counting in gross vs net P&L."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        spread_pct_round_trip=0.02,
        slippage_pct_round_trip=0.02,
        apply_gst=False,
    )
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "test_7_state.json"), initial_equity=10000.0)

    # Open LONG @ 4000.0
    # Round-trip spread half = 0.01% = 0.0001; slip half = 0.01% = 0.0001
    # Entry fill = 4000.0 * (1 + 0.0002) = 4000.80
    ok, _, pos = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST"
    )
    assert ok is True
    assert pos["entry_price"] == 4000.80
    assert pos["signal_entry_price"] == 4000.0
    entry_slip = pos["slippage_cost"]  # abs(4000.80 - 4000.0) * 0.001 = $0.0008

    # Close @ exit price 4020.0
    # Long exits by selling: exit fill = 4020.0 * (1 - 0.0002) = 4019.20
    ok_c, _, cl = broker.close_simulated_trade(exit_price=4020.0, exit_reason="TP", order_type="LIMIT")
    assert ok_c is True
    assert cl["exit_price"] == 4019.20
    assert cl["trigger_price"] == 4020.0

    # Signal Benchmark Gross P&L: (4020.0 - 4000.0) * 0.001 = $0.0200
    expected_gross = round((4020.0 - 4000.0) * 0.001, 4)
    assert cl["gross_pnl"] == expected_gross

    # Total Price Impact = Entry Slip ($0.0008) + Exit Slip ($0.0008) = $0.0016
    assert cl["slippage"] == pytest.approx(0.0016, abs=0.0002)

    # Net P&L = Gross P&L ($0.0200) - Friction (Brokerage + Price Impact)
    assert cl["net_pnl"] == pytest.approx(cl["gross_pnl"] - cl["total_friction"], abs=0.0001)


# =============================================================================
# 8. Cash and equity conservation
# =============================================================================

def test_8_cash_and_equity_conservation(tmp_path):
    """Verify that every dollar and cent is conserved across order opening, holding, and closing."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER", leverage=2.0, apply_gst=False)
    initial_cash = 10000.0
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "test_8_state.json"), initial_equity=initial_cash)

    # 1. Before trade: Cash == Equity == 10000.0
    sum_before = broker.get_account_summary()
    assert sum_before["cash_balance"] == initial_cash
    assert sum_before["equity"] == initial_cash

    # 2. Open 1 lot @ 4000.0
    ok, _, pos = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST"
    )
    assert ok is True
    entry_fee = pos["entry_fee"]

    # Cash while open = initial_cash - entry_fee
    sum_open = broker.get_account_summary()
    assert sum_open["cash_balance"] == pytest.approx(initial_cash - entry_fee, abs=0.0001)
    # Total equity while open = cash_balance + margin_used + unrealized_pnl
    expected_open_equity = round(sum_open["cash_balance"] + pos["margin"] + pos["unrealized_pnl"], 4)
    assert sum_open["equity"] == pytest.approx(expected_open_equity, abs=0.0001)

    # 3. Close trade @ 4040.0
    ok_c, _, cl = broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert ok_c is True
    net_pnl = cl["net_pnl"]

    # Final cash = initial_cash + net_pnl
    sum_final = broker.get_account_summary()
    assert sum_final["cash_balance"] == pytest.approx(initial_cash + net_pnl, abs=0.0001)
    assert sum_final["equity"] == pytest.approx(initial_cash + net_pnl, abs=0.0001)
    assert broker.realized_pnl == pytest.approx(net_pnl, abs=0.0001)
    assert broker.open_position is None


# =============================================================================
# 9. Duplicate-order prevention
# =============================================================================

def test_9_duplicate_order_prevention(tmp_path):
    """Verify that attempting to open a second order while a position is active is strictly blocked."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "test_9_state.json"), initial_equity=10000.0)

    # First order -> ACCEPTED
    ok1, msg1, pos1 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="ORB"
    )
    assert ok1 is True
    cash_after_order1 = broker.cash_balance

    # Second order (same direction) -> BLOCKED
    ok2, msg2, pos2 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4005.0, stop_loss=3985.0, take_profit=4045.0, lots=1, strategy_name="ORB"
    )
    assert ok2 is False
    assert "Position already open" in msg2
    assert pos2 is None
    assert broker.cash_balance == cash_after_order1
    assert broker.open_position["lots"] == 1

    # Third order (opposite direction) -> BLOCKED
    ok3, msg3, pos3 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="SHORT", entry_price=4000.0, stop_loss=4020.0, take_profit=3960.0, lots=1, strategy_name="VWAP_EMA"
    )
    assert ok3 is False
    assert "Position already open" in msg3
    assert pos3 is None
    assert broker.cash_balance == cash_after_order1
    assert broker.open_position["lots"] == 1


# =============================================================================
# 10. Restart and recovery from saved PAPER state
# =============================================================================

def test_10_restart_and_recovery_from_state_file(tmp_path):
    """Verify full state recovery across process restarts without data corruption."""
    state_file = tmp_path / "restart_state.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")

    # Instance A: Open 1-lot position
    broker_a = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    ok_a, _, pos_a = broker_a.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TREND"
    )
    assert ok_a is True
    cash_a = broker_a.cash_balance

    # Instance B: Simulating a daemon crash & restart from disk
    broker_b = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    assert broker_b.open_position is not None
    assert broker_b.open_position["lots"] == 1
    assert broker_b.open_position["contract_qty"] == 0.001
    assert broker_b.open_position["entry_price"] == pos_a["entry_price"]
    assert broker_b.cash_balance == cash_a

    # Instance B closes the position
    ok_b, _, closed_b = broker_b.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert ok_b is True
    assert broker_b.open_position is None

    # Instance C: Reload after close
    broker_c = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    assert broker_c.open_position is None
    assert len(broker_c.closed_trades) == 1
    assert broker_c.closed_trades[0]["trade_id"] == pos_a["trade_id"]
    assert broker_c.cash_balance == broker_b.cash_balance


# =============================================================================
# 11. Missing or stale market data handling
# =============================================================================

def test_11_missing_or_stale_market_data_handling(tmp_path):
    """Verify that empty, incomplete, or unready market data feeds deterministically abort evaluation."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "bot_stale_state.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    feed = MagicMock()
    feed.get_readiness.return_value = SystemReadiness(
        state=ReadinessState.READY,
        is_ready=True,
        completed_5m_count=150,
        required_5m_count=150,
        completed_15m_count=50,
        required_15m_count=50,
        indicator_status={"ema50": True},
        missing_requirements=[],
        details={},
    )
    bot = DeltaPaperBot(cfg=cfg, broker=broker, feed=feed)

    # 11a. Empty candle DataFrame -> ABORT
    sig_empty = bot._evaluate_strategy(pd.DataFrame())
    assert sig_empty is None
    assert "Insufficient completed candle history" in bot._last_rejection_reason

    # 11b. Insufficient history (< 30 bars) -> ABORT
    short_df = pd.DataFrame({
        "timestamp": [datetime.now(timezone.utc)] * 10,
        "open": [4000.0] * 10,
        "high": [4010.0] * 10,
        "low": [3990.0] * 10,
        "close": [4005.0] * 10,
        "volume": [100.0] * 10,
    })
    sig_short = bot._evaluate_strategy(short_df)
    assert sig_short is None
    assert "Insufficient completed candle history" in bot._last_rejection_reason

    # 11c. Unready feed state -> ABORT
    feed.get_readiness.return_value = SystemReadiness(
        state=ReadinessState.DEGRADED,
        is_ready=False,
        completed_5m_count=10,
        required_5m_count=150,
        completed_15m_count=2,
        required_15m_count=50,
        indicator_status={},
        missing_requirements=["WebSocket disconnected", "Candle history stale (gap > 15m)"],
        details={},
    )
    df_35 = pd.DataFrame({
        "timestamp": [datetime.now(timezone.utc)] * 35,
        "open": [4000.0] * 35,
        "high": [4010.0] * 35,
        "low": [3990.0] * 35,
        "close": [4005.0] * 35,
        "volume": [100.0] * 35,
    })
    sig_unready = bot._evaluate_strategy(df_35)
    assert sig_unready is None
    assert "Readiness DEGRADED" in bot._last_rejection_reason


# =============================================================================
# 12. Invalid contract quantity rejection
# =============================================================================

@pytest.mark.parametrize("invalid_contract_qty", [
    0.0,
    -0.001,
    0.002,
    0.010,
    0.484,
    1.0,
    None,
    "0.001",
    float("nan"),
    float("inf"),
])
def test_12_invalid_contract_quantity_rejection(invalid_contract_qty):
    """Verify that validate_contract_quantity rejects any physical contract size not strictly matching 0.001 XAUT."""
    with pytest.raises(PositionSizeViolation):
        validate_contract_quantity(invalid_contract_qty, STANDARD_CONTRACT_SIZE_XAUT)


# =============================================================================
# 13. Rejected order must not change cash, equity, positions or fills
# =============================================================================

def test_13_rejected_orders_preserve_all_account_state(tmp_path):
    """Verify that rejected orders (oversized, invalid lots, or malformed) leave cash, equity, and positions 100% pristine."""
    state_file = tmp_path / "test_13_state.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    initial_summary = broker.get_account_summary()
    assert initial_summary["cash_balance"] == 10000.0
    assert initial_summary["equity"] == 10000.0
    assert broker.open_position is None
    assert len(broker.closed_trades) == 0

    rejection_cases = [
        {"lots": 484, "msg": "Historical 484 lots"},
        {"lots": 495, "msg": "Historical 495 lots"},
        {"lots": 10, "msg": "10 lots"},
        {"lots": 2, "msg": "2 lots"},
        {"lots": 0, "msg": "0 lots"},
        {"lots": -1, "msg": "-1 lots"},
        {"lots": 1.5, "msg": "1.5 float lots"},
        {"lots": None, "msg": "None lots"},
        {"lots": "1", "msg": "String lots"},
    ]

    for case in rejection_cases:
        ok, msg, pos = broker.open_simulated_trade(
            symbol="XAUTUSD",
            direction="LONG",
            entry_price=4000.0,
            stop_loss=3980.0,
            take_profit=4040.0,
            lots=case["lots"],
            strategy_name="TEST",
        )
        assert ok is False, f"Expected rejection for {case['msg']}"
        assert pos is None
        assert broker.open_position is None
        assert len(broker.closed_trades) == 0

        # Cash & equity must remain invariant
        curr_summary = broker.get_account_summary()
        assert curr_summary["cash_balance"] == 10000.0
        assert curr_summary["equity"] == 10000.0
        assert broker.total_fees == 0.0
        assert broker.total_slippage == 0.0


# =============================================================================
# 14. Historical 484-lot incident remains unchanged
# =============================================================================

def test_14_historical_484_lot_incident_record_preservation():
    """Verify that the official historical state file preserves PAPER_20261105_101000_LONG without modification."""
    state_path = Path(__file__).resolve().parent.parent / "data" / "paper_forward_24h_state.json"
    assert state_path.exists(), f"State file {state_path} must exist!"

    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 14a. Incident trade identification
    closed = data.get("closed_trades", [])
    assert len(closed) >= 1
    incident_trade = next((t for t in closed if t.get("trade_id") == "PAPER_20261105_101000_LONG"), None)
    assert incident_trade is not None, "Historical trade PAPER_20261105_101000_LONG is missing!"

    # 14b. Exact historical metrics invariance
    assert incident_trade["lots"] == 484
    assert incident_trade["requested_lots"] == 484
    assert incident_trade["filled_lots"] == 484
    assert incident_trade["contract_qty"] == 0.484
    assert incident_trade["entry_price"] == 4127.9
    assert incident_trade["exit_price"] == 4113.95
    assert incident_trade["net_pnl"] == -7.9472
    assert incident_trade["gross_pnl"] == -5.9534
    assert incident_trade["fees"] == 1.1954
    assert incident_trade["slippage"] == 0.7984
    assert incident_trade["r_multiple"] == -1.25
    assert incident_trade["strategy_name"] == "VWAP_EMA"

    # 14c. Account balances invariance
    assert data["cash_balance"] == 9992.0528
    assert data["realized_pnl"] == -7.9472
    assert data["peak_equity"] == 10984.4728
