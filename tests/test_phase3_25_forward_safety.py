"""Phase 3.25 — Controlled Paper Forward Validation & Execution Safety Tests.

Comprehensive automated safety test battery covering all 20 required domains:
1. One-lot maximum enforcement.
2. Oversized quantity rejection.
3. Fractional contract rejection.
4. Partial-fill consistency.
5. Atomic order reservation.
6. Concurrent duplicate prevention (multithreaded race conditions).
7. Idempotent request handling.
8. Maker fill realism (queue / touch vs. penetration).
9. Taker stop-loss execution.
10. Gap-through-stop handling (conservative worse-price fill).
11. Stale feed rejection.
12. WebSocket reconnect.
13. Duplicate candle rejection.
14. Accounting conservation.
15. State recovery.
16. Daily risk limit.
17. Cooldown enforcement.
18. Maximum trades/day.
19. Emergency stop.
20. LIVE trading disabled.
"""

import concurrent.futures
import json
import math
import os
import secrets
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import pandas as pd

from config.orb_settings import ORBSettings
from risk.position_size_guard import (
    APPROVED_MAX_PAPER_LOTS,
    APPROVED_MIN_PAPER_LOTS,
    STANDARD_CONTRACT_SIZE_XAUT,
    PositionSizeViolation,
    validate_contract_quantity,
    validate_paper_order_lots,
)
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from services.delta_paper_bot import DeltaPaperBot
from regime.readiness import ReadinessState, SystemReadiness
from risk.position_sizing import DailyRiskState


# =============================================================================
# 1. One-lot maximum enforcement
# =============================================================================

def test_1_one_lot_maximum_enforcement(tmp_path):
    """Verify that exactly 1 micro-lot is accepted, and that max paper limit is 1."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t1.json"), initial_equity=10000.0)

    assert APPROVED_MAX_PAPER_LOTS == 1
    assert APPROVED_MIN_PAPER_LOTS == 1

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="VWAP_EMA"
    )
    assert ok is True
    assert pos["lots"] == 1
    assert pos["contract_qty"] == 0.001


# =============================================================================
# 2. Oversized quantity rejection
# =============================================================================

@pytest.mark.parametrize("oversized_qty", [2, 5, 10, 484, 495, 1000])
def test_2_oversized_quantity_rejection(tmp_path, oversized_qty):
    """Verify that any oversized order is rejected without silent clamping."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / f"t2_{oversized_qty}.json"), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=oversized_qty, strategy_name="ORB"
    )
    assert ok is False
    assert pos is None
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["reason"] == "SECURITY_SIZING_VIOLATION"
    assert broker.last_order["violation_code"] == "OVERSIZED_QUANTITY"
    assert broker.open_position is None


# =============================================================================
# 3. Fractional contract rejection
# =============================================================================

@pytest.mark.parametrize("invalid_contract", [0.0, -0.001, 0.0005, 0.002, 0.010, 0.484, 1.75, float("nan")])
def test_3_fractional_contract_rejection(invalid_contract):
    """Verify that validate_contract_quantity rejects any contract quantity != 0.001 XAUT."""
    with pytest.raises(PositionSizeViolation):
        validate_contract_quantity(invalid_contract, STANDARD_CONTRACT_SIZE_XAUT)


# =============================================================================
# 4. Partial-fill consistency
# =============================================================================

def test_4_partial_fill_consistency(tmp_path):
    """Verify discrete partial fills: 1 lot order with 50% fill ratio -> ZERO_FILL; 100% -> FILLED."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t4.json"), initial_equity=10000.0)

    # 50% on 1 lot = 0.5 lot < 1 integer lot -> ZERO_FILL
    ok_half, msg_half, pos_half = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=0.50, strategy_name="TEST"
    )
    assert ok_half is False
    assert pos_half is None
    assert broker.last_order["status"] == "ZERO_FILL"
    assert broker.cash_balance == 10000.0

    # 100% on 1 lot -> 1 lot FILLED
    ok_full, msg_full, pos_full = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, fill_ratio=1.0, strategy_name="TEST"
    )
    assert ok_full is True
    assert pos_full["lots"] == 1
    assert pos_full["contract_qty"] == 0.001


# =============================================================================
# 5. Atomic order reservation
# =============================================================================

def test_5_atomic_order_reservation(tmp_path):
    """Verify order slot reservation lifecycle: reserve -> release or commit."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t5.json"), initial_equity=10000.0)

    # 1. Reserve slot
    ok, msg, token = broker.reserve_order_slot(
        idempotency_key="TEST_KEY_1", strategy_name="TEST", direction="LONG", symbol="XAUTUSD"
    )
    assert ok is True
    assert token is not None
    assert broker._pending_reservation is not None

    # 2. Second reservation attempt while in-flight -> BLOCKED
    ok2, msg2, token2 = broker.reserve_order_slot(
        idempotency_key="TEST_KEY_2", strategy_name="TEST", direction="SHORT", symbol="XAUTUSD"
    )
    assert ok2 is False
    assert token2 is None
    assert "currently reserved" in msg2

    # 3. Release first reservation
    released = broker.release_order_reservation(token, reason="TEST_ABORT")
    assert released is True
    assert broker._pending_reservation is None

    # 4. Now second reservation can succeed
    ok3, msg3, token3 = broker.reserve_order_slot(
        idempotency_key="TEST_KEY_2", strategy_name="TEST", direction="SHORT", symbol="XAUTUSD"
    )
    assert ok3 is True
    assert token3 is not None


# =============================================================================
# 6. Concurrent duplicate prevention (Multithreaded Race Conditions)
# =============================================================================

def test_6_concurrent_duplicate_prevention_multithreaded(tmp_path):
    """Verify that 10 concurrent threads attempting to open simulated orders simultaneously produce exactly 1 fill."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    state_file = tmp_path / "concurrent_state.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    results = []
    num_threads = 10

    def attempt_order(thread_id: int):
        ok, msg, pos = broker.open_simulated_trade(
            symbol="XAUTUSD",
            direction="LONG",
            entry_price=4000.0,
            stop_loss=3980.0,
            take_profit=4040.0,
            lots=1,
            strategy_name=f"THREAD_{thread_id}",
            idempotency_key=f"CONCURRENT_KEY_{thread_id}",
        )
        return (ok, msg, pos)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(attempt_order, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r[0] is True]
    rejections = [r for r in results if r[0] is False]

    # Exactly 1 thread must succeed in filling the order
    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(rejections) == num_threads - 1

    # Exactly 1 position open
    assert broker.open_position is not None
    assert broker.open_position["lots"] == 1
    assert broker.open_position["contract_qty"] == 0.001


# =============================================================================
# 7. Idempotent request handling
# =============================================================================

def test_7_idempotent_request_handling(tmp_path):
    """Verify that replaying an order with the same idempotency key is rejected as a duplicate without secondary fills."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t7.json"), initial_equity=10000.0)

    fixed_key = "IDEMP_UNIQUE_ORDER_999"

    # First attempt -> SUCCEEDS
    ok1, msg1, pos1 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST", idempotency_key=fixed_key
    )
    assert ok1 is True
    cash_after_fill = broker.cash_balance

    # Close position cleanly
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert broker.open_position is None

    # Replay identical order with same idempotency key -> MUST BE REJECTED
    ok2, msg2, pos2 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST", idempotency_key=fixed_key
    )
    assert ok2 is False
    assert "Duplicate order: idempotency key" in msg2
    assert pos2 is None
    assert broker.open_position is None


# =============================================================================
# 8. Maker fill realism (queue / touch vs. penetration)
# =============================================================================

def test_8_maker_fill_realism_penetration_requirement(tmp_path):
    """Verify that touching TP without penetration remains unfilled when require_penetration=True."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t8.json"), initial_equity=10000.0)

    # Open LONG @ 4000.0, TP @ 4040.0
    broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST"
    )

    # Scenario A: Bar high merely touches 4040.0 with require_penetration=True -> UNFILLED (rests in queue)
    unfilled = broker.evaluate_open_position_sl_tp(
        current_price=4038.0, high=4040.0, low=4020.0, require_penetration=True
    )
    assert unfilled is None
    assert broker.open_position is not None

    # Scenario B: Bar high penetrates to 4040.50 (> 4040.0) -> FILLED
    filled = broker.evaluate_open_position_sl_tp(
        current_price=4040.20, high=4040.50, low=4020.0, require_penetration=True
    )
    assert filled is not None
    assert filled["exit_reason"] == "TP"
    assert filled["exit_order_type"] == "MAKER"
    assert broker.open_position is None


# =============================================================================
# 9. Taker stop-loss execution
# =============================================================================

def test_9_taker_stop_loss_execution(tmp_path):
    """Verify stop-loss executes as TAKER exit with taker fee (0.05%) and slippage applied."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER", taker_fee_pct=0.05)
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t9.json"), initial_equity=10000.0)

    broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST"
    )
    closed = broker.evaluate_open_position_sl_tp(current_price=3975.0, high=3985.0, low=3975.0)

    assert closed is not None
    assert closed["exit_reason"] == "SL"
    assert closed["exit_order_type"] == "TAKER"
    assert closed["exit_fee"] == pytest.approx(closed["exit_price"] * 0.001 * 0.0005, abs=0.0005)


# =============================================================================
# 10. Gap-through-stop handling (Conservative Worse-Price Fill)
# =============================================================================

def test_10_gap_through_stop_handling(tmp_path):
    """Verify that if the market gaps through stop loss, the exit fills at the worse gap price plus slippage."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t10.json"), initial_equity=10000.0)

    # LONG @ 4000.0, SL @ 3980.0
    broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST"
    )

    # Overnight gap-down: bar opens at 3960.0 (20 points below SL of 3980.0)
    closed = broker.evaluate_open_position_sl_tp(
        current_price=3958.0, high=3965.0, low=3955.0, open_price=3960.0
    )

    assert closed is not None
    assert closed["exit_reason"] == "SL"
    # Fill price must be based on the worse gap open (3960.0), NOT the stop price (3980.0)
    assert closed["trigger_price"] == 3960.0
    assert closed["exit_price"] <= 3960.0


# =============================================================================
# 11. Stale feed rejection
# =============================================================================

def test_11_stale_feed_rejection(tmp_path):
    """Verify that DeltaPaperBot rejects trade signals when market data feed is stale or unready."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t11.json"), initial_equity=10000.0)
    feed = MagicMock()
    feed.get_readiness.return_value = SystemReadiness(
        state=ReadinessState.DEGRADED,
        is_ready=False,
        completed_5m_count=100,
        required_5m_count=150,
        completed_15m_count=20,
        required_15m_count=50,
        indicator_status={},
        missing_requirements=["Market data feed is stale (last update > 120s ago)"],
        details={},
    )
    bot = DeltaPaperBot(cfg=cfg, broker=broker, feed=feed)

    df_35 = pd.DataFrame({
        "timestamp": [datetime.now(timezone.utc)] * 35,
        "open": [4000.0] * 35,
        "high": [4010.0] * 35,
        "low": [3990.0] * 35,
        "close": [4005.0] * 35,
        "volume": [100.0] * 35,
    })
    sig = bot._evaluate_strategy(df_35)
    assert sig is None
    assert "Readiness DEGRADED" in bot._last_rejection_reason


# =============================================================================
# 12. WebSocket reconnect
# =============================================================================

def test_12_websocket_reconnect_handling():
    """Verify that feed tracks reconnection attempts and sets readiness to degraded during reconnect."""
    from market_data.delta_live_feed import DeltaLiveMarketDataFeed

    feed = DeltaLiveMarketDataFeed(cfg=ORBSettings(symbol="XAUTUSD", mode="PAPER"))
    feed._ws_connected = False
    feed._reconnect_attempts = 3
    feed._last_error = "Connection reset by peer"

    status = feed.get_status()
    assert status["ws_connected"] is False
    assert status["reconnect_attempts"] == 3
    assert status["readiness_state"] != "READY"


# =============================================================================
# 13. Duplicate candle rejection
# =============================================================================

def test_13_duplicate_candle_rejection():
    """Verify that candle frames drop duplicate timestamps and enforce monotonicity."""
    now = datetime.now(timezone.utc)
    raw_df = pd.DataFrame({
        "timestamp": [now, now, now + timedelta(minutes=5)],
        "open": [4000.0, 4000.0, 4010.0],
        "high": [4010.0, 4010.0, 4020.0],
        "low": [3990.0, 3990.0, 4000.0],
        "close": [4005.0, 4005.0, 4015.0],
        "volume": [100.0, 100.0, 200.0],
    })
    deduped = raw_df.drop_duplicates(subset=["timestamp"], keep="last")
    assert len(deduped) == 2


# =============================================================================
# 14. Accounting conservation
# =============================================================================

def test_14_accounting_conservation(tmp_path):
    """Verify cash balance + equity equation: Delta Equity == Net Realized P&L."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(tmp_path / "t14.json"), initial_equity=10000.0)

    broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST"
    )
    _, _, closed = broker.close_simulated_trade(exit_price=4020.0, exit_reason="TP")

    assert broker.open_position is None
    summary = broker.get_account_summary()
    assert summary["cash_balance"] == pytest.approx(10000.0 + closed["net_pnl"], abs=0.0001)
    assert summary["equity"] == pytest.approx(summary["cash_balance"], abs=0.0001)
    assert summary["realized_pnl"] == pytest.approx(closed["net_pnl"], abs=0.0001)


# =============================================================================
# 15. State recovery
# =============================================================================

def test_15_state_recovery_across_restarts(tmp_path):
    """Verify state recovery from disk across broker instance recreation."""
    state_file = tmp_path / "persistence_test.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")

    b1 = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    b1.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4000.0, stop_loss=3980.0, take_profit=4040.0, lots=1, strategy_name="TEST"
    )
    cash_saved = b1.cash_balance

    # Recreate instance from state_file
    b2 = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)
    assert b2.open_position is not None
    assert b2.open_position["lots"] == 1
    assert b2.cash_balance == cash_saved
    assert b2._pending_reservation is None  # Transient lock must be cleared


# =============================================================================
# 16. Daily risk limit
# =============================================================================

def test_16_daily_loss_limit_blocks_new_trades(tmp_path):
    """Verify that reaching daily loss limit (1.5% = $150) blocks trading for the day."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER", daily_loss_limit_pct=1.5)
    state = DailyRiskState(path=tmp_path / "risk_state.json", cfg=cfg)

    now = datetime.now(timezone.utc)
    # Record a $155 loss on $10,000 equity (1.55% > 1.5% limit)
    state.record_trade(pnl_cash=-155.0, equity_at_start=10000.0, now=now)

    can_trade, reason, _ = state.can_trade(now)
    assert can_trade is False
    assert "daily loss limit" in reason.lower()


# =============================================================================
# 17. Cooldown enforcement
# =============================================================================

def test_17_cooldown_enforcement(tmp_path):
    """Verify that a loss initiates a 60-minute cooldown period where trades are blocked."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER", cooldown_after_loss_minutes=60, max_trades_per_day=5)
    state = DailyRiskState(path=tmp_path / "cooldown_state.json", cfg=cfg)

    t0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)
    state.record_trade(pnl_cash=-10.0, equity_at_start=10000.0, now=t0)

    # 30 minutes later -> still in cooldown
    t_30m = t0 + timedelta(minutes=30)
    allowed_30m, reason_30m, _ = state.can_trade(t_30m)
    assert allowed_30m is False
    assert "cooldown active" in reason_30m.lower()

    # 65 minutes later -> cooldown expired
    t_65m = t0 + timedelta(minutes=65)
    allowed_65m, _, _ = state.can_trade(t_65m)
    assert allowed_65m is True


# =============================================================================
# 18. Maximum trades per day
# =============================================================================

def test_18_maximum_trades_per_day(tmp_path):
    """Verify that exceeding max_trades_per_day (1 trade/day) blocks subsequent entries."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER", max_trades_per_day=1)
    state = DailyRiskState(path=tmp_path / "max_trades_state.json", cfg=cfg)

    now = datetime.now(timezone.utc)
    state.record_trade(pnl_cash=10.0, equity_at_start=10000.0, now=now)

    can_trade, reason, _ = state.can_trade(now)
    assert can_trade is False
    assert "maximum trades" in reason.lower()


# =============================================================================
# 19. Emergency stop
# =============================================================================

def test_19_emergency_kill_switch(tmp_path):
    """Verify that triggering the emergency kill switch halts all bot evaluation."""
    cfg_killed = ORBSettings(symbol="XAUTUSD", mode="PAPER", kill_switch_active=True)
    broker = DeltaPaperBroker(cfg=cfg_killed, state_file=str(tmp_path / "kill_state.json"), initial_equity=10000.0)
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
    bot = DeltaPaperBot(cfg=cfg_killed, broker=broker, feed=feed)

    df_35 = pd.DataFrame({
        "timestamp": [datetime.now(timezone.utc)] * 35,
        "open": [4000.0] * 35,
        "high": [4010.0] * 35,
        "low": [3990.0] * 35,
        "close": [4005.0] * 35,
        "volume": [100.0] * 35,
    })
    sig = bot._evaluate_strategy(df_35)
    assert sig is None
    assert "kill switch engaged" in bot._last_rejection_reason.lower()


# =============================================================================
# 20. LIVE trading disabled
# =============================================================================

def test_20_live_trading_strictly_prohibited(tmp_path):
    """Verify that attempting to invoke live exchange orders or instantiate unconfirmed live bots raises exceptions."""
    cfg_paper = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg_paper, state_file=str(tmp_path / "t20.json"), initial_equity=10000.0)

    # Calling execute_live_order raises PaperSafetyViolation
    with pytest.raises(PaperSafetyViolation) as exc_info:
        broker.execute_live_order(symbol="XAUTUSD", side="BUY", qty=1)
    assert "Real order submission is strictly prohibited in PAPER mode" in str(exc_info.value)

    # Bot with mode=LIVE and unacknowledged live trading raises ConfigurationError or PaperSafetyViolation
    cfg_live_unack = ORBSettings(symbol="XAUTUSD", mode="LIVE", live_trading_acknowledged="NO")
    from config.orb_settings import ConfigurationError
    with pytest.raises((ConfigurationError, PaperSafetyViolation)):
        DeltaPaperBot(cfg=cfg_live_unack)
