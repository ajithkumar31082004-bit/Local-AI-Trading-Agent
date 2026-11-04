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
        lots=10,  # 0.01 XAUT
        strategy_name="TEST",
    )
    # Entry fee on 10 lots: 4000.80 * 0.01 * 0.0001 = 0.0040
    assert broker.open_position["entry_fee"] == 0.0040
    # Cash at entry should be 10000 - 0.004 = 9999.996 (rounded)
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

    # 7. Execute size_position and assert exact lot sizing
    decision = size_position(entry, stop, equity, settings)
    assert decision.approved is True
    assert decision.lots == 495
    assert decision.lots == min(raw_risk_lots, max_margin_lots)

    # 8. Unencumbered equity buffer
    total_margin = margin_per_lot * decision.lots
    assert round(total_margin, 2) == 998.81
    unencumbered_equity = equity - total_margin
    assert unencumbered_equity >= 9000.0  # At least 90% unencumbered collateral buffer



