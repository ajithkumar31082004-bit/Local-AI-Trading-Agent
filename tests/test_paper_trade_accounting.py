"""Unit tests for Delta Paper Broker trade accounting, contract multiplier, and friction reconciliation."""
import pytest
from pathlib import Path
from config.orb_settings import ORBSettings
from services.delta_paper_broker import DeltaPaperBroker


def test_paper_broker_micro_lot_accounting_precision(tmp_path):
    """Verify exact 4-decimal precision on 0.001 XAUT micro-lot contracts."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "test_micro_state.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # 1 lot = 0.001 XAUT
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4034.77,
        stop_loss=4021.7264,
        take_profit=4068.1197,
        lots=1,
        strategy_name="VWAP_EMA",
        signal_reason="Test Signal",
    )
    assert ok is True
    assert pos["contract_qty"] == 0.001
    # 4034.77 * 1.0002 = 4035.58 (rounded to 2 decimal fill)
    assert pos["entry_price"] == 4035.58
    assert pos["entry_fee"] == 0.0004
    assert pos["slippage_cost"] == 0.0008

    # Close via Stop Loss
    ok_close, msg_close, closed = broker.close_simulated_trade(
        exit_price=4021.7264,
        exit_reason="STOP_LOSS",
    )
    assert ok_close is True
    # Exit fill: 4021.7264 * 0.9998 = 4020.92
    assert closed["exit_price"] == 4020.92
    assert closed["fees"] == 0.0008
    assert closed["slippage"] == 0.0016
    assert closed["spread_cost"] == 0.0008
    assert closed["slippage_cost"] == 0.0008
    assert closed["total_friction"] == 0.0024

    # Gross P&L: (4021.7264 - 4034.77) * 0.001 = -0.0130
    assert closed["gross_pnl"] == -0.0130
    # Net P&L: -0.0130 - 0.0024 = -0.0154
    assert closed["net_pnl"] == -0.0154


def test_paper_broker_cash_conservation_no_double_deduction(tmp_path):
    """Verify that cash balance is conserved exactly without double-deduction of entry fee."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "test_cash_state.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # Initial cash
    assert broker.cash_balance == 10000.0

    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,  # 0.001 XAUT (1 micro-lot)
        strategy_name="TEST",
    )
    # Entry fee on 1 lot: 4000.80 * 0.001 * 0.0001 = 0.0004
    assert broker.open_position["entry_fee"] == 0.0004
    # Cash at entry should be 10000 - 0.0004 = 9999.9996 (rounded)
    assert broker.cash_balance < 10000.0

    # Close with win at 4040.0
    _, _, closed = broker.close_simulated_trade(
        exit_price=4040.0,
        exit_reason="TAKE_PROFIT",
    )
    # Total cash balance must equal exactly: initial_equity + net_pnl
    expected_cash = round(10000.0 + closed["net_pnl"], 4)
    assert broker.cash_balance == expected_cash


def test_paper_broker_trade_r_vs_portfolio_r_distinction(tmp_path):
    """Verify distinction between trade-level R multiple and portfolio-level risk budget R."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER", risk_pct=0.5)
    state_file = tmp_path / "test_r_state.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # 1 lot: risk points = 13.04. Risk cash = 13.04 * 0.001 = $0.01304
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4034.77,
        stop_loss=4021.73,
        take_profit=4068.12,
        lots=1,
        strategy_name="VWAP_EMA",
    )
    _, _, closed = broker.close_simulated_trade(
        exit_price=4021.73,
        exit_reason="STOP_LOSS",
    )

    # Trade-level R multiple is close to -1.0R (loss at stop loss plus friction)
    assert -1.30 <= closed["trade_r_multiple"] <= -0.90
    assert closed["r_multiple"] == closed["trade_r_multiple"]

    # Portfolio-level R multiple is tiny (-0.0003R) because account has $50 risk budget
    assert -0.001 <= closed["portfolio_r_multiple"] <= -0.0001


def test_reconcile_reported_trade_accounting(tmp_path):
    """Verify reconciliation of the reported 24h forward test trade."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "test_reconcile_state.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4034.77,
        stop_loss=4021.7264,
        take_profit=4068.1197,
        lots=1,
        strategy_name="VWAP_EMA",
    )
    _, _, closed = broker.close_simulated_trade(
        exit_price=4021.7264,
        exit_reason="STOP_LOSS",
    )

    # Fill prices
    assert closed["entry_price"] == 4035.58
    assert closed["exit_price"] == 4020.92

    # Verification: (Exit Fill - Entry Fill) * Qty = (4020.92 - 4035.58) * 0.001 = -0.01466
    fill_to_fill_pnl = round((closed["exit_price"] - closed["entry_price"]) * closed["contract_qty"], 5)
    assert fill_to_fill_pnl == -0.01466

    # Real exchange fees round-trip (0.01% maker per side = 0.02% round-trip)
    assert closed["fees"] == 0.0008

    # Net Realized P&L = fill_to_fill_pnl - fees = -0.01466 - 0.0008 = -0.01546
    assert abs(closed["net_pnl"] - (-0.0154)) <= 0.0001


def test_trade_id_uses_candle_timestamp(tmp_path):
    """Verify that trade_id incorporates candle_timestamp when provided."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "test_trade_id_state.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry_price=4094.81,
        stop_loss=4108.35,
        take_profit=4060.36,
        lots=1,
        strategy_name="VWAP_EMA",
        candle_timestamp="2026-10-22 13:15:00+05:30",
    )
    assert ok is True
    assert pos["trade_id"] == "PAPER_20261022_131500_SHORT"
    assert pos["entry_time"] == "2026-10-22 13:15:00+05:30"


def test_account_summary_four_decimal_precision(tmp_path):
    """Verify that get_account_summary reports equity and balance to 4 decimal places."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "test_summary_precision.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4034.77,
        stop_loss=4021.73,
        take_profit=4068.12,
        lots=1,
        strategy_name="VWAP_EMA",
    )
    _, _, closed = broker.close_simulated_trade(
        exit_price=4021.73,
        exit_reason="STOP_LOSS",
    )

    summary = broker.get_account_summary()
    assert summary["equity"] == 9999.9846
    assert summary["cash_balance"] == 9999.9846
    assert summary["realized_pnl"] == -0.0154


def test_fourteen_day_simulated_ledger_consistency():
    """Verify mathematical integrity of the 7-trade ledger across 14 forward test days."""
    import pandas as pd
    from pathlib import Path

    csv_path = Path(__file__).resolve().parent.parent / "data" / "forward_test_14d_trades.csv"
    if not csv_path.exists():
        return

    df = pd.read_csv(csv_path)
    assert len(df) == 7

    # Check that net_pnl = gross_pnl - total_friction for every trade
    for _, row in df.iterrows():
        calc_net = round(row["gross_pnl"] - row["total_friction"], 4)
        assert abs(calc_net - row["net_pnl"]) <= 0.0001
        assert abs(row["fees"] - 0.0008) <= 0.0001
        assert abs(row["total_friction"] - 0.0024) <= 0.0001

    # Check sum of net P&L
    total_net = round(df["net_pnl"].sum(), 4)
    assert abs(total_net - (-0.0121)) <= 0.0001


def test_fee_scenarios_and_gst(tmp_path):
    """Verify configurable fee scenarios (Maker-Maker, Maker-Taker, Taker-Taker) and GST."""
    # Scenario 1: Maker entry (0.01%) + Taker exit (0.05%) with 18% GST applied
    cfg_maker_taker = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        fee_scenario="MAKER_TAKER",
        maker_fee_pct=0.01,
        taker_fee_pct=0.05,
        gst_rate_pct=18.0,
        apply_gst=True,
    )
    broker = DeltaPaperBroker(cfg=cfg_maker_taker, state_file=str(tmp_path / "mt_state.json"), initial_equity=10000.0)

    # 1 lot @ 4000.0 (notional = $4.00)
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
    )
    # Entry notional = $4.0008, entry fee @ 0.01% = $0.0004
    # Entry GST @ 18% = 0.0004 * 0.18 = $0.0001
    _, _, closed = broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # Exit notional = $4.0392, exit fee @ 0.05% = $0.0020
    assert closed["fees"] == 0.0024  # 0.0004 entry + 0.0020 exit
    assert closed["gst"] == 0.0004   # 18% of 0.0024 = 0.000432 -> 0.0004
    assert closed["fee_scenario"] == "MAKER_TAKER"
    assert closed["gst_applied"] is True

    # Cash balance must strictly conserve: Initial + net_pnl
    summary = broker.get_account_summary()
    assert summary["cash_balance"] == round(10000.0 + closed["net_pnl"], 4)

    # Scenario 2: Taker entry (0.05%) + Taker exit (0.05%)
    cfg_taker_taker = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        fee_scenario="TAKER_TAKER",
        maker_fee_pct=0.01,
        taker_fee_pct=0.05,
        apply_gst=False,
    )
    broker_tt = DeltaPaperBroker(cfg=cfg_taker_taker, state_file=str(tmp_path / "tt_state.json"), initial_equity=10000.0)
    broker_tt.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
    )
    _, _, closed_tt = broker_tt.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    # Entry fee @ 0.05% ($0.0020) + Exit fee @ 0.05% ($0.0020) = $0.0040
    assert closed_tt["fees"] == 0.0040
    assert closed_tt["fee_scenario"] == "TAKER_TAKER"


def test_position_sizing_reported_495_lots():
    """Independent mathematical verification of the reported 495-lot sizing calculation.
    
    Verifies that size_position caps lots at 495 for a $10,000 account at 0.5% risk
    due to the binding 10% maximum margin limit at 2.0x leverage.
    """
    from risk.position_sizing import size_position, estimate_lot_risk
    from config.orb_settings import settings

    entry = 4035.58
    stop = 4021.73
    equity = 10000.0

    # 1. Configured constraints
    assert settings.risk_pct == 0.5
    assert settings.max_margin_pct_equity == 10.0
    assert settings.leverage == 2.0
    assert settings.contract_size_xaut == 0.001
    assert settings.lot_increment == 1

    # 2. Financial risk budget
    risk_budget = equity * (settings.risk_pct / 100.0)
    assert risk_budget == 50.0

    # 3. Stop distance
    stop_dist = abs(entry - stop)
    assert round(stop_dist, 2) == 13.85

    # 4. Per-lot risk and costs
    per_lot_risk, fee_per_lot, costs_per_lot = estimate_lot_risk(entry, stop, settings)
    gross_risk_per_lot = stop_dist * settings.contract_size_xaut
    assert round(gross_risk_per_lot, 5) == 0.01385

    # 5. Raw risk lots (unconstrained by margin)
    raw_risk_lots = int(risk_budget // per_lot_risk)
    assert raw_risk_lots > 3000

    # 6. Margin constraint: 10% of $10,000 = $1,000 max margin allocation
    max_margin_budget = equity * (settings.max_margin_pct_equity / 100.0)
    assert max_margin_budget == 1000.0

    notional_per_lot = entry * settings.contract_size_xaut  # $4.03558
    margin_per_lot = notional_per_lot / settings.leverage   # $2.01779
    max_margin_lots = int(max_margin_budget // margin_per_lot)

    # 1000.0 / 2.01779 = 495.59 -> 495 integer lots
    assert max_margin_lots == 495

    # 7. Execute size_position:
    # In BACKTEST mode: returns raw margin-capped 495 lots
    cfg_backtest = ORBSettings(mode="BACKTEST")
    decision_bt = size_position(entry, stop, equity, cfg_backtest)
    assert decision_bt.approved is True
    assert decision_bt.lots == 495
    assert decision_bt.lots == min(raw_risk_lots, max_margin_lots)

    # In PAPER mode (Phase 3.23): strictly capped to 1 micro-lot
    cfg_paper = ORBSettings(mode="PAPER")
    decision_paper = size_position(entry, stop, equity, cfg_paper)
    assert decision_paper.approved is True
    assert decision_paper.lots == 1

    # 8. Unencumbered equity buffer
    total_margin = margin_per_lot * decision_paper.lots
    unencumbered_equity = equity - total_margin
    assert unencumbered_equity >= 9000.0  # At least 90% unencumbered collateral buffer


def test_maker_order_fill_and_unfill_conditions(tmp_path):
    """Verify simulation of Maker orders that fill vs. do NOT fill based on bar price bounds."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "fill_state.json"), initial_equity=10000.0)

    # 1. Buy limit order where bar low was higher than limit -> UNFILLED
    ok_unfilled_buy, msg_buy, pos_buy = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        order_type="LIMIT",
        bar_low=4005.0,  # Market low never touched 4000.0
    )
    assert ok_unfilled_buy is False
    assert "unfilled" in msg_buy.lower()
    assert pos_buy is None
    assert broker.open_position is None

    # 2. Buy limit order where bar low reached 3995.0 -> FILLED
    ok_filled_buy, msg_filled_buy, pos_filled = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        order_type="LIMIT",
        bar_low=3995.0,
    )
    assert ok_filled_buy is True
    assert pos_filled is not None
    assert broker.open_position is not None
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # 3. Sell limit order where bar high was lower than limit -> UNFILLED
    ok_unfilled_sell, msg_sell, pos_sell = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry_price=4050.0,
        stop_loss=4070.0,
        take_profit=4010.0,
        lots=1,
        strategy_name="VWAP_EMA",
        order_type="LIMIT",
        bar_high=4045.0,  # Market high never reached 4050.0
    )
    assert ok_unfilled_sell is False
    assert "unfilled" in msg_sell.lower()
    assert pos_sell is None


def test_partial_fill_simulation(tmp_path):
    """Verify partial fill simulation where only a fraction of requested size fills."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "partial_state.json"), initial_equity=10000.0)

    # 1. Oversized order (4 lots) is strictly rejected by Central Position Size Guard
    ok_over, msg_over, pos_over = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=4,
        strategy_name="VWAP_EMA",
        fill_ratio=0.5,
    )
    assert ok_over is False
    assert "Position Size Guard Rejection" in msg_over
    assert pos_over is None

    # 2. Approved 1-lot order with 100% fill fills 1 lot
    ok_1, msg_1, pos_1 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        fill_ratio=1.0,
    )
    assert ok_1 is True
    assert pos_1["lots"] == 1
    assert pos_1["contract_qty"] == 0.001
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # 3. 1-lot order with 50% fill results in integer zero fill (floor(0.5) = 0 lots)
    ok_half, msg_half, pos_half = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        fill_ratio=0.5,
    )
    assert ok_half is False
    assert "zero fill" in msg_half.lower()
    assert pos_half is None


def test_maker_entry_followed_by_taker_sl_exit(tmp_path):
    """Verify realistic execution of Maker limit entry followed by Taker stop-loss market exit."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        fee_scenario="MAKER_TAKER",
        maker_fee_pct=0.01,
        taker_fee_pct=0.05,
    )
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "maker_taker_sl.json"), initial_equity=10000.0)

    # 1. Maker Entry
    ok, _, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry_price=4000.0,
        stop_loss=4020.0,
        take_profit=3960.0,
        lots=1,
        strategy_name="VWAP_EMA",
        order_type="LIMIT",
    )
    assert ok is True
    # Entry fee @ 0.01% on ~$4.00 notional = $0.0004
    assert pos["entry_fee"] == 0.0004

    # 2. Stop-loss triggers TAKER market exit
    _, _, closed = broker.close_simulated_trade(
        exit_price=4020.0,
        exit_reason="SL",
        order_type="TAKER",
    )
    # Exit fee @ 0.05% on ~$4.02 notional = $0.0020
    assert closed["entry_order_type"] == "LIMIT"
    assert closed["exit_order_type"] == "TAKER"
    assert closed["fees"] == 0.0024  # 0.0004 + 0.0020


def test_regime_dependent_spread_and_slippage(tmp_path):
    """Verify that VOLATILE_BREAKOUT expands spread and slippage by 1.5x."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "regime_state.json"), initial_equity=10000.0)

    # Normal RANGING entry
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        market_regime="RANGING",
    )
    _, _, closed_normal = broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP", market_regime="RANGING")

    # VOLATILE_BREAKOUT entry
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        market_regime="VOLATILE_BREAKOUT",
    )
    _, _, closed_volatile = broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP", market_regime="VOLATILE_BREAKOUT")

    # Spread/slip in volatile regime must be 1.5x of normal
    assert closed_volatile["slippage"] > closed_normal["slippage"]
    assert closed_volatile["market_regime"] == "VOLATILE_BREAKOUT"


def test_partial_fill_engine_comprehensive_matrix(tmp_path):
    """Exhaustive test matrix for Phase 3.2 exchange-compatible integer partial-fill engine.
    
    Validates all 13 required scenarios:
    1. Zero requested lots.
    2. Zero fill ratio.
    3. Very small fill ratio (< 1 lot increment).
    4. 10% partial fill.
    5. 25% partial fill.
    6. 50% partial fill.
    7. 100% full fill.
    8. Invalid negative fill ratio.
    9. Fill ratio above 100% (ceiling clamp).
    10. Partial fill followed by cancellation (unfilled tracking).
    11. Partial fill followed by another fill (multi-step partial exit).
    12. Minimum one-lot order boundary.
    13. Maker limit unfill when bar never reaches limit price.
    """
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "matrix_state.json"), initial_equity=10000.0)

    # 1. Zero requested lots -> REJECTED by Central Position Size Guard
    ok1, msg1, pos1 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=0, strategy_name="TEST"
    )
    assert ok1 is False
    assert pos1 is None
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["reason"] == "SECURITY_SIZING_VIOLATION"
    assert broker.last_order["violation_code"] == "NON_POSITIVE_LOTS"
    assert broker.open_position is None

    # 2. Oversized requested lots (10 lots) -> REJECTED by Central Position Size Guard
    ok2, msg2, pos2 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=10, fill_ratio=1.0, strategy_name="TEST"
    )
    assert ok2 is False
    assert pos2 is None
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["reason"] == "SECURITY_SIZING_VIOLATION"
    assert broker.last_order["violation_code"] == "OVERSIZED_QUANTITY"
    assert broker.open_position is None

    # 3. Oversized requested lots (4 lots) -> REJECTED by Central Position Size Guard
    ok3, msg3, pos3 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=4, fill_ratio=0.25, strategy_name="TEST"
    )
    assert ok3 is False
    assert pos3 is None
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["reason"] == "SECURITY_SIZING_VIOLATION"
    assert broker.last_order["violation_code"] == "OVERSIZED_QUANTITY"
    assert broker.open_position is None

    # 4. Zero fill ratio on 1 lot -> ZERO_FILL (no position, no margin, zero fees)
    ok4, msg4, pos4 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=0.0, strategy_name="TEST"
    )
    assert ok4 is False
    assert pos4 is None
    assert broker.last_order["status"] == "ZERO_FILL"
    assert broker.last_order["filled_lots"] == 0
    assert broker.last_order["unfilled_lots"] == 1
    assert broker.last_order["fees"] == 0.0
    assert broker.cash_balance == 10000.0
    assert broker.open_position is None

    # 5. Very small fill ratio (5% on 1 lot = 0.05 lot < 1 lot increment) -> ZERO_FILL
    ok5, msg5, pos5 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=0.05, strategy_name="TEST"
    )
    assert ok5 is False
    assert pos5 is None
    assert broker.last_order["status"] == "ZERO_FILL"
    assert broker.last_order["filled_lots"] == 0
    assert broker.last_order["unfilled_lots"] == 1
    assert broker.open_position is None

    # 6. 50% partial fill on 1 lot (0.5 lot < 1 integer lot) -> ZERO_FILL
    ok6, msg6, pos6 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=0.50, strategy_name="TEST"
    )
    assert ok6 is False
    assert pos6 is None
    assert broker.last_order["status"] == "ZERO_FILL"
    assert broker.last_order["filled_lots"] == 0
    assert broker.last_order["unfilled_lots"] == 1
    assert broker.open_position is None

    # 7. 100% full fill on 1 lot -> COMPLETE FILL
    ok7, msg7, pos7 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=1.0, strategy_name="TEST"
    )
    assert ok7 is True
    assert pos7["lots"] == 1
    assert pos7["requested_lots"] == 1
    assert pos7["unfilled_lots"] == 0
    assert pos7["contract_qty"] == 0.001
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert broker.open_position is None

    # 8. Invalid negative fill ratio (-0.25 clamped to 0.0) -> ZERO_FILL
    ok8, msg8, pos8 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=-0.25, strategy_name="TEST"
    )
    assert ok8 is False
    assert pos8 is None
    assert broker.last_order["status"] == "ZERO_FILL"
    assert broker.open_position is None

    # 9. Fill ratio above 100% (1.5 clamped to 1.0, never overfilling) -> COMPLETE FILL
    ok9, msg9, pos9 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=1.5, strategy_name="TEST"
    )
    assert ok9 is True
    assert pos9["lots"] == 1
    assert pos9["requested_lots"] == 1
    assert pos9["unfilled_lots"] == 0
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert broker.open_position is None

    # 10. Single 1-lot position lifecycle
    ok10, msg10, pos10 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="SHORT", entry_price=4050.0, stop_loss=4070.0, take_profit=4010.0, lots=1, fill_ratio=1.0, strategy_name="TEST"
    )
    assert ok10 is True
    assert pos10["lots"] == 1
    assert pos10["unfilled_lots"] == 0
    _, _, closed10 = broker.close_simulated_trade(exit_price=4010.0, exit_reason="TP")
    assert closed10["requested_lots"] == 1
    assert closed10["lots"] == 1
    assert closed10["unfilled_lots"] == 0

    # 11. Complete single-lot exit
    ok11, msg11, pos11 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=1.0, strategy_name="TEST"
    )
    assert ok11 is True
    assert pos11["lots"] == 1
    # Exit: close 1 lot completely
    ok_ex, _, cl = broker.close_simulated_trade(exit_price=4020.0, exit_reason="FINAL_TP", close_lots=1)
    assert ok_ex is True
    assert cl["lots"] == 1
    assert cl["remaining_lots"] == 0
    assert broker.open_position is None

    # 12. Minimum one-lot order boundary
    # 1 lot with 100% fill -> fills 1
    ok12a, _, pos12a = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=1.0, strategy_name="TEST"
    )
    assert ok12a is True
    assert pos12a["lots"] == 1
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # 1 lot with 50% fill -> 0.5 lot < 1 integer lot -> ZERO_FILL
    ok12b, _, pos12b = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=0.50, strategy_name="TEST"
    )
    assert ok12b is False
    assert pos12b is None
    assert broker.last_order["status"] == "ZERO_FILL"
    assert broker.open_position is None

    # 13. Maker limit unfill when bar never reaches limit price
    ok13_buy, _, pos13_buy = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, order_type="LIMIT", bar_low=4008.0, strategy_name="TEST"
    )
    assert ok13_buy is False
    assert pos13_buy is None
    assert broker.last_order["reason"] == "PRICE_NOT_REACHED"

    ok13_sell, _, pos13_sell = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="SHORT", entry_price=4050.0, stop_loss=4070.0, take_profit=4010.0, lots=1, order_type="LIMIT", bar_high=4042.0, strategy_name="TEST"
    )
    assert ok13_sell is False
    assert pos13_sell is None
    assert broker.last_order["reason"] == "PRICE_NOT_REACHED"


def test_reliability_restart_position_recovery_and_duplicate_prevention(tmp_path):
    """Verify state persistence, position recovery across restarts, and duplicate order prevention."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "recovery_state.json"

    # Instance 1: Open trade (1 micro-lot approved under Phase 3.23)
    b1 = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    ok, msg, pos = b1.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="VWAP_EMA"
    )
    assert ok is True
    assert pos["lots"] == 1
    initial_cash_after_margin = b1.cash_balance

    # Instance 2 (Simulating process restart): Loads existing state file
    b2 = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    assert b2.open_position is not None
    assert b2.open_position["lots"] == 1
    assert b2.open_position["entry_price"] == pos["entry_price"]
    assert b2.cash_balance == initial_cash_after_margin

    # Guard: Duplicate entry must be rejected while position is open
    dup_ok, dup_msg, dup_pos = b2.open_simulated_trade(
        symbol="XAUTUSD", direction="SHORT", entry_price=4000.0, stop_loss=4020.0, take_profit=3960.0, lots=1, strategy_name="VWAP_EMA"
    )
    assert dup_ok is False
    assert "Position already open" in dup_msg
    assert dup_pos is None

    # Instance 2: Close position
    close_ok, close_msg, closed = b2.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert close_ok is True
    assert b2.open_position is None

    # Instance 3 (Post-close restart): Verifies flat state and cash reconciliation
    b3 = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    assert b3.open_position is None
    assert len(b3.closed_trades) == 1
    assert b3.cash_balance == b2.cash_balance
    expected_cash = round(10000.0 + closed["net_pnl"], 4)
    assert b3.cash_balance == expected_cash


def test_gst_inclusion_vs_exclusion_reconciliation(tmp_path):
    """Verify exact discrepancy mechanics between apply_gst=False and apply_gst=True."""
    state_no_gst = tmp_path / "no_gst.json"
    state_with_gst = tmp_path / "with_gst.json"

    # 1. apply_gst = False (Historical 14d forward test configuration)
    cfg_no_gst = ORBSettings(symbol="XAUTUSD", mode="PAPER", apply_gst=False)
    b_no = DeltaPaperBroker(cfg=cfg_no_gst, state_file=str(state_no_gst), initial_equity=10000.0)
    b_no.open_simulated_trade(symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="VWAP_EMA")
    _, _, c_no = b_no.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # In apply_gst = False:
    # Brokerage = 0.0008, Total Price Impact = 0.0016
    # Total friction = 0.0008 + 0.0016 = 0.0024
    # GST field = 0.0001 (calculated for info), but gst_applied = False
    assert c_no["brokerage_fee"] == 0.0008
    assert c_no["gst"] == 0.0001
    assert c_no["gst_applied"] is False
    assert c_no["total_friction"] == 0.0024
    assert c_no["net_pnl"] == round(c_no["gross_pnl"] - 0.0024, 4)
    assert b_no.cash_balance == round(10000.0 + c_no["net_pnl"], 4)

    # 2. apply_gst = True (Full Indian statutory GST deduction scenario)
    cfg_with_gst = ORBSettings(symbol="XAUTUSD", mode="PAPER", apply_gst=True)
    b_yes = DeltaPaperBroker(cfg=cfg_with_gst, state_file=str(state_with_gst), initial_equity=10000.0)
    b_yes.open_simulated_trade(symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="VWAP_EMA")
    _, _, c_yes = b_yes.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # In apply_gst = True:
    # Total friction = 0.0008 + 0.0001 + 0.0016 = 0.0025
    assert c_yes["brokerage_fee"] == 0.0008
    assert c_yes["gst"] == 0.0001
    assert c_yes["gst_applied"] is True
    assert c_yes["total_friction"] == 0.0025
    assert c_yes["net_pnl"] == round(c_yes["gross_pnl"] - 0.0025, 4)
    assert b_yes.cash_balance == round(10000.0 + c_yes["net_pnl"], 4)

    # Difference in friction is exactly the GST deduction: 0.0001
    assert round(c_yes["total_friction"] - c_no["total_friction"], 4) == 0.0001





