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


