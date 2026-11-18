"""Unit tests for Multi-Stage Exits and Breakeven Stop Ratchet in DeltaPaperBroker."""

from pathlib import Path
import pytest

from config.orb_settings import ORBSettings
from services.delta_paper_broker import DeltaPaperBroker


def test_breakeven_ratchet_protects_single_lot_long_trade(tmp_path):
    """Verify that when a 1-lot Long trade reaches +1.0R, stop-loss ratchets to breakeven."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        enable_multi_stage_exits=True,
        breakeven_trigger_r=1.0,
        apply_gst=False,
    )
    broker = DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "be_long.json", initial_equity=10000.0)

    # 1. Open Long at 4000.0, SL at 3980.0 (risk = 20 pts), TP at 4050.0
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4050.0,
        lots=1,
        strategy_name="TEST_MULTI_STAGE",
    )
    assert ok is True
    assert broker.open_position["stop_loss"] == 3980.0
    assert broker.open_position["is_breakeven_active"] is False

    # 2. Price surges to 4020.0 (+1.0R excursion, bar high = 4022.0)
    broker.evaluate_open_position_sl_tp(current_price=4020.0, high=4022.0, low=4005.0)

    # Assert stop-loss was ratcheted to breakeven (>= entry_price)
    assert broker.open_position["is_breakeven_active"] is True
    assert broker.open_position["stop_loss"] >= 4000.0

    # 3. Price pulls back to 3995.0, hitting the ratcheted breakeven stop loss
    closed_trade = broker.evaluate_open_position_sl_tp(current_price=3998.0, high=4005.0, low=3995.0)

    assert closed_trade is not None
    assert closed_trade["exit_reason"] == "BREAKEVEN_SL"
    assert broker.open_position is None
    # Net PnL must be protected from full loss (>= -0.005 instead of -0.02 full stop loss)
    assert closed_trade["net_pnl"] >= -0.005


def test_breakeven_ratchet_allows_full_tp_when_trend_continues(tmp_path):
    """Verify that after stop-loss ratchets to breakeven, continuation to Take Profit fills full TP."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        enable_multi_stage_exits=True,
        breakeven_trigger_r=1.0,
        apply_gst=False,
    )
    broker = DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "tp_cont.json", initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4050.0,
        lots=1,
        strategy_name="TEST_MULTI_STAGE",
    )
    assert ok is True

    # 1. Price surges to 4020.0 (+1.0R excursion) -> ratchets stop to breakeven
    broker.evaluate_open_position_sl_tp(current_price=4020.0, high=4022.0, low=4010.0)
    assert broker.open_position["is_breakeven_active"] is True

    # 2. Price continues to final Take Profit (4050.0)
    final_trade = broker.evaluate_open_position_sl_tp(current_price=4050.0, high=4052.0, low=4030.0)

    assert final_trade is not None
    assert final_trade["exit_reason"] == "TP"
    assert broker.open_position is None
    assert final_trade["net_pnl"] > 0


def test_short_trade_breakeven_ratchet(tmp_path):
    """Verify that a Short trade ratchets stop-loss downward to breakeven upon +1.0R excursion."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        enable_multi_stage_exits=True,
        breakeven_trigger_r=1.0,
        apply_gst=False,
    )
    broker = DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "be_short.json", initial_equity=10000.0)

    # Short at 4000.0, SL at 4020.0 (risk = 20 pts), TP at 3950.0
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry_price=4000.0,
        stop_loss=4020.0,
        take_profit=3950.0,
        lots=1,
        strategy_name="TEST_MULTI_STAGE",
    )
    assert ok is True

    # Price drops to 3980.0 (+1.0R favorable drop, bar low = 3978.0)
    broker.evaluate_open_position_sl_tp(current_price=3980.0, high=3995.0, low=3978.0)

    assert broker.open_position["is_breakeven_active"] is True
    # Stop-loss on Short must be ratcheted down to <= 4000.0
    assert broker.open_position["stop_loss"] <= 4000.0

    # Price bounces back up to 4005.0, hitting the ratcheted breakeven stop
    closed = broker.evaluate_open_position_sl_tp(current_price=4002.0, high=4005.0, low=3990.0)
    assert closed is not None
    assert closed["exit_reason"] == "BREAKEVEN_SL"
    assert closed["net_pnl"] >= -0.005


def test_legacy_mode_does_not_ratchet_when_disabled(tmp_path):
    """Verify that when enable_multi_stage_exits=False, legacy SL/TP behavior is strictly preserved."""
    cfg = ORBSettings(
        symbol="XAUTUSD",
        mode="PAPER",
        enable_multi_stage_exits=False,
        apply_gst=False,
    )
    broker = DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "legacy_state.json", initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4050.0,
        lots=1,
        strategy_name="TEST_LEGACY",
    )
    assert ok is True

    # Price surges to 4020.0 (+1.0R excursion)
    broker.evaluate_open_position_sl_tp(current_price=4020.0, high=4022.0, low=4010.0)

    # In legacy mode, stop_loss must NOT be modified
    assert broker.open_position["stop_loss"] == 3980.0
    assert broker.open_position.get("is_breakeven_active", False) is False
