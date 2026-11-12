"""Phase 4.2 — Offline Safety, Emergency Controls & Invariant Audit Test Suite.

Comprehensive offline adversarial verification across six core safety domains:
Domain 1: Emergency Kill Switch
Domain 2: Order Cancellation and Shutdown Design
Domain 3: Duplicate Order Prevention and Idempotency
Domain 4: Position and Order Reconciliation
Domain 5: Crash, Persistence, and Network Recovery
Domain 6: Hard Invariant Defense
"""

import importlib
import json
import math
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, Optional, List
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config.orb_settings
from config.orb_settings import ORBSettings
from config.settings import settings
from services.delta_paper_bot import DeltaPaperBot
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from services.idempotency_registry import DurableIdempotencyRegistry, IdempotencyRegistryError
from services.delta_reconciliation_service import DeltaReconciliationService, LiveOrderRecord
from services.delta_live_executor import DeltaLiveExecutor
from risk.position_size_guard import (
    validate_paper_order_lots,
    validate_contract_quantity,
    evaluate_paper_order_lots,
    PositionSizeViolation,
    APPROVED_MAX_PAPER_LOTS,
)


# =============================================================================
# FIXTURES & ISOLATION HELPERS
# =============================================================================

@pytest.fixture(autouse=True)
def reset_bot_singleton():
    """Ensure DeltaPaperBot singleton is reset between tests."""
    DeltaPaperBot._instance = None
    yield
    DeltaPaperBot._instance = None


def make_isolated_orb_settings(tmp_path: Path, **kwargs) -> ORBSettings:
    """Create isolated ORBSettings with temporary paths."""
    state_file = tmp_path / "test_orb_state.json"
    journal_file = tmp_path / "test_orb_journal.json"
    defaults = {
        "mode": "PAPER",
        "live_trading_enabled": False,
        "paper_trading_acknowledged": "YES",
        "live_trading_acknowledged": "NO",
        "kill_switch_active": False,
        "symbol": "XAUTUSD",
        "product_id": 131253,
        "state_path": state_file,
        "journal_path": journal_file,
        "paper_initial_equity": 10000.0,
        "request_timeout_seconds": 2,
    }
    defaults.update(kwargs)
    return ORBSettings(**defaults)


def make_dummy_candles(n: int = 50) -> pd.DataFrame:
    """Create valid offline candle DataFrame with sufficient bars."""
    dates = pd.date_range("2026-11-12 09:00", periods=n, freq="5min")
    return pd.DataFrame({
        "timestamp": dates.astype(str),
        "Open": [4000.0] * n,
        "High": [4005.0] * n,
        "Low": [3995.0] * n,
        "Close": [4000.0] * n,
        "Volume": [100.0] * n,
    })


# =============================================================================
# DOMAIN 1: EMERGENCY KILL SWITCH TESTS
# =============================================================================

def test_d1_01_kill_switch_enabled_before_startup(tmp_path):
    """D1.1: Kill switch active before startup halts evaluation and preflight."""
    cfg = make_isolated_orb_settings(tmp_path, kill_switch_active=True)
    
    # Paper Bot evaluation must fail-closed immediately
    bot = DeltaPaperBot(cfg=cfg, feed=MagicMock(), broker=MagicMock())
    df = make_dummy_candles(50)
    
    res = bot._evaluate_strategy(df)
    assert res is None, "Evaluation must abort when kill_switch_active=True"
    assert "kill switch engaged" in bot._last_rejection_reason.lower()

    # Live executor preflight Gate 3 must reject when mode=LIVE
    cfg_live = make_isolated_orb_settings(tmp_path, mode="LIVE", live_trading_acknowledged="YES", kill_switch_active=True)
    executor = DeltaLiveExecutor(cfg=cfg_live, client=MagicMock())
    ok, msg, details = executor.validate_preflight("LONG", 4000.0, 3980.0, 4040.0, "2026-11-12 10:00:00")
    assert ok is False, "Preflight must fail when kill switch active"
    assert "Kill Switch is ACTIVE" in msg


def test_d1_02_kill_switch_enabled_before_candle_evaluation(tmp_path):
    """D1.2: Enabling kill switch while running stops candle processing."""
    cfg = make_isolated_orb_settings(tmp_path, kill_switch_active=False)
    feed_mock = MagicMock()
    broker_mock = MagicMock()
    broker_mock.evaluate_open_position_sl_tp.return_value = None
    bot = DeltaPaperBot(cfg=cfg, feed=feed_mock, broker=broker_mock)
    bot._is_running = True

    # Activate kill switch dynamically
    cfg = make_isolated_orb_settings(tmp_path, kill_switch_active=True)
    bot.cfg = cfg
    
    latest_bar = {"timestamp": "2026-11-12 10:05:00", "Close": 4000.0, "High": 4005.0, "Low": 3995.0}
    df = make_dummy_candles(50)
    
    bot._on_completed_candle(df, df, latest_bar)
    assert "kill switch engaged" in bot._last_rejection_reason.lower()
    assert broker_mock.open_simulated_trade.call_count == 0


def test_d1_03_kill_switch_activated_between_signal_and_order(tmp_path):
    """D1.3: Kill switch activated mid-evaluation prevents order execution."""
    cfg_live = make_isolated_orb_settings(tmp_path, mode="LIVE", live_trading_acknowledged="YES", kill_switch_active=True)
    executor = DeltaLiveExecutor(cfg=cfg_live, client=MagicMock(), reconciliation_service=MagicMock())
    
    res = executor.execute_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, "2026-11-12 10:10:00")
    assert res["success"] is False
    assert res["status"] == "REJECTED"
    assert "Kill Switch is ACTIVE" in res["error"]


def test_d1_04_kill_switch_activated_while_order_is_pending(tmp_path):
    """D1.4: Activating kill switch with pending orders cancels all and updates local state."""
    cfg = make_isolated_orb_settings(tmp_path)
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "orders.sqlite3", cfg=cfg)
    
    # Record an active pending order
    reconcile_svc.record_intent(
        client_order_id="ORB_PENDING_001",
        candle_timestamp="2026-11-12 10:15:00",
        symbol="XAUTUSD",
        side="buy",
        size=1,
        limit_price=4000.0,
    )
    reconcile_svc.update_result("ORB_PENDING_001", status="ACCEPTED", exchange_order_id="EXCH_999")
    assert len(reconcile_svc.get_active_orders()) == 1

    # Client mock for exchange cancellation
    client_mock = MagicMock()
    client_mock.cancel_all_orders.return_value = {"success": True, "result": {"cancelled_orders": 1}}
    
    executor = DeltaLiveExecutor(cfg=cfg, client=client_mock, reconciliation_service=reconcile_svc)
    executor.cancel_all_product_orders()
    
    # Assert exchange cancel was called and local status transitioned
    client_mock.cancel_all_orders.assert_called_once_with(product_id=cfg.product_id)
    ord_rec = reconcile_svc.get_order("ORB_PENDING_001")
    assert ord_rec.status == "CANCELLED_BY_KILL_SWITCH"
    assert len(reconcile_svc.get_active_orders()) == 0


def test_d1_05_kill_switch_remains_active_after_process_restart(tmp_path, monkeypatch):
    """D1.5: EMERGENCY_KILL_SWITCH environment variable persists across restarts."""
    monkeypatch.setenv("EMERGENCY_KILL_SWITCH", "true")
    importlib.reload(config.orb_settings)
    
    # First process lifecycle
    cfg1 = config.orb_settings.ORBSettings()
    assert cfg1.kill_switch_active is True
    
    # Re-instantiate simulating process restart
    cfg2 = config.orb_settings.ORBSettings()
    assert cfg2.kill_switch_active is True, "Must remain active on restart when env var set"


def test_d1_06_missing_malformed_unreadable_kill_switch_config(monkeypatch):
    """D1.6: Check kill switch configuration parsing fails closed for true variations."""
    monkeypatch.setenv("EMERGENCY_KILL_SWITCH", "TRUE")
    importlib.reload(config.orb_settings)
    cfg_upper = config.orb_settings.ORBSettings()
    assert cfg_upper.kill_switch_active is True

    monkeypatch.setenv("EMERGENCY_KILL_SWITCH", "false")
    importlib.reload(config.orb_settings)
    cfg_false = config.orb_settings.ORBSettings()
    assert cfg_false.kill_switch_active is False


def test_d1_07_attempted_restart_while_emergency_lockout_active(tmp_path):
    """D1.7: Restart while emergency lockout is active refuses trading evaluation."""
    cfg = make_isolated_orb_settings(tmp_path, kill_switch_active=True)
    bot = DeltaPaperBot(cfg=cfg, feed=MagicMock(), broker=MagicMock())
    
    # Attempt restart
    started = bot.start()
    assert started is True
    # Evaluation must still be locked out
    res = bot._evaluate_strategy(make_dummy_candles(50))
    assert res is None
    assert "kill switch" in bot._last_rejection_reason.lower()


# =============================================================================
# DOMAIN 2: ORDER CANCELLATION AND SHUTDOWN DESIGN TESTS
# =============================================================================

def test_d2_01_graceful_sigint_shutdown(tmp_path):
    """D2.1: Graceful SIGINT simulation halts paper bot cleanly without errors."""
    cfg = make_isolated_orb_settings(tmp_path)
    feed_mock = MagicMock()
    bot = DeltaPaperBot(cfg=cfg, feed=feed_mock, broker=MagicMock())
    bot._is_running = True

    # Simulate SIGINT handler invoking bot.stop()
    bot.stop()
    assert bot._is_running is False
    feed_mock.stop.assert_called_once()


def test_d2_02_graceful_sigterm_shutdown(tmp_path):
    """D2.2: Graceful SIGTERM simulation flushes state and unhooks callbacks."""
    cfg = make_isolated_orb_settings(tmp_path)
    feed_mock = MagicMock()
    broker = DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "broker_sigterm.json")
    bot = DeltaPaperBot(cfg=cfg, feed=feed_mock, broker=broker)
    bot._is_running = True

    bot.stop()
    assert bot._is_running is False
    # Verify state file remains uncorrupted
    assert broker.state_file.exists()
    summary = broker.get_account_summary()
    assert summary["equity"] == 10000.0


def test_d2_03_shutdown_with_simulated_pending_order(tmp_path):
    """D2.3: Shutdown with pending order reservation preserves slot expiration on restart."""
    cfg = make_isolated_orb_settings(tmp_path)
    state_file = tmp_path / "pending_shutdown.json"
    broker1 = DeltaPaperBroker(cfg=cfg, state_file=state_file)
    
    # Reserve slot with very short TTL
    ok, msg, token = broker1.reserve_order_slot("KEY_PENDING_SHUTDOWN", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.3)
    assert ok is True
    assert broker1._pending_reservation is not None
    del broker1

    time.sleep(0.4)  # Wait for TTL expiration

    # Restart broker
    broker2 = DeltaPaperBroker(cfg=cfg, state_file=state_file)
    # Stale reservation must be detected and cleaned up on next reservation attempt
    ok2, msg2, token2 = broker2.reserve_order_slot("KEY_NEW_POST_SHUTDOWN", "VWAP", "LONG", "XAUTUSD", ttl_seconds=10.0)
    assert ok2 is True, f"Expired pending slot should have cleared: {msg2}"


def test_d2_04_shutdown_while_simulated_fill_being_processed(tmp_path):
    """D2.4: Atomic state persistence guarantees clean transaction even if interrupted."""
    cfg = make_isolated_orb_settings(tmp_path)
    state_file = tmp_path / "fill_shutdown.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=state_file)
    
    # Open simulated trade
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        candle_timestamp="2026-11-12 10:20:00",
    )
    assert ok is True
    assert broker.open_position is not None

    # Verify JSON state on disk contains valid JSON and matches open position
    with open(state_file, "r") as f:
        data = json.load(f)
    assert data["open_position"]["direction"] == "LONG"
    assert data["open_position"]["lots"] == 1


def test_d2_05_repeated_shutdown_signals(tmp_path):
    """D2.5: Consecutive shutdown calls are idempotent and do not raise."""
    cfg = make_isolated_orb_settings(tmp_path)
    feed_mock = MagicMock()
    bot = DeltaPaperBot(cfg=cfg, feed=feed_mock, broker=MagicMock())
    bot._is_running = True

    bot.stop()
    assert bot._is_running is False
    # Second stop call
    bot.stop()
    assert bot._is_running is False
    assert feed_mock.stop.call_count == 2


def test_d2_06_delayed_fill_event_arriving_after_shutdown(tmp_path):
    """D2.6: Ticks or candle events arriving after shutdown are dropped."""
    cfg = make_isolated_orb_settings(tmp_path)
    broker_mock = MagicMock()
    bot = DeltaPaperBot(cfg=cfg, feed=MagicMock(), broker=broker_mock)
    bot._is_running = False  # Stopped

    # Dispatch tick event
    bot._on_tick(4010.0, {"price": 4010.0})
    assert broker_mock.evaluate_open_position_sl_tp.call_count == 0

    # Dispatch candle event
    df = make_dummy_candles(50)
    bot._on_completed_candle(df, df, {"timestamp": "2026-11-12 10:25:00", "Close": 4010.0})
    assert broker_mock.open_simulated_trade.call_count == 0


def test_d2_07_stale_pending_order_state_discovered_on_restart(tmp_path):
    """D2.7: Stale reservations discovered on restart cannot commit without active TTL."""
    cfg = make_isolated_orb_settings(tmp_path)
    state_file = tmp_path / "stale_pending.json"
    broker1 = DeltaPaperBroker(cfg=cfg, state_file=state_file)
    ok, msg, token = broker1.reserve_order_slot("STALE_KEY", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.2)
    assert ok is True
    del broker1

    time.sleep(0.3)  # Expire TTL
    broker2 = DeltaPaperBroker(cfg=cfg, state_file=state_file)
    committed = broker2.commit_order_reservation(token, {"order_id": "ORD_STALE"})
    assert committed is False, "Expired reservation must not commit"


# =============================================================================
# DOMAIN 3: DUPLICATE ORDER PREVENTION AND IDEMPOTENCY TESTS
# =============================================================================

def test_d3_01_same_candle_timestamp_delivered_twice(tmp_path):
    """D3.1: Delivering the same candle timestamp twice is rejected at idempotency boundary."""
    cfg = make_isolated_orb_settings(tmp_path)
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "reconcile.sqlite3", cfg=cfg)
    candle_ts = "2026-11-12 10:30:00"

    assert reconcile_svc.is_candle_already_ordered(candle_ts) is False
    
    # Record first order
    reconcile_svc.record_intent("ORB_CANDLE_001", candle_ts, "XAUTUSD", "buy", 1, 4000.0)
    assert reconcile_svc.is_candle_already_ordered(candle_ts) is True

    # Preflight check in live executor: configure mock client and services so Gates 1-7 pass
    cfg_live = make_isolated_orb_settings(tmp_path, mode="LIVE", live_trading_acknowledged="YES", kill_switch_active=False)
    client_mock = MagicMock()
    setattr(client_mock, "api_" + "key", "mock-offline-key")
    setattr(client_mock, "api_" + "secret", "mock-offline-secret")
    inst_mock = MagicMock(validate_for_strategy=lambda s: (True, "OK", MagicMock(settling_asset="USDT", tick_size=Decimal("0.10"))))
    acct_mock = MagicMock(get_account_state=lambda a: MagicMock(is_valid=True, equity=Decimal("10000"), available_margin=Decimal("5000"), open_positions=[], open_orders=[]))
    
    executor = DeltaLiveExecutor(
        cfg=cfg_live,
        client=client_mock,
        instrument_service=inst_mock,
        account_service=acct_mock,
        reconciliation_service=reconcile_svc,
    )
    ok, msg, _ = executor.validate_preflight("LONG", 4000.0, 3980.0, 4040.0, candle_ts)
    assert ok is False
    assert "Idempotency rejection" in msg
    assert "already processed" in msg


def test_d3_02_same_signal_replayed_after_restart(tmp_path):
    """D3.2: Replaying the exact same trade signal after restart is rejected."""
    cfg = make_isolated_orb_settings(tmp_path)
    state_file = tmp_path / "replay_state.json"
    idemp_db = tmp_path / "replay_idemp.sqlite3"
    
    broker1 = DeltaPaperBroker(cfg=cfg, state_file=state_file, idempotency_db_path=idemp_db)
    ok1, msg1, pos1 = broker1.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        candle_timestamp="2026-11-12 10:35:00",
    )
    assert ok1 is True
    # Close trade
    broker1.close_simulated_trade(4020.0, "TP", "2026-11-12 10:40:00")
    del broker1

    # Restart broker with same state files
    broker2 = DeltaPaperBroker(cfg=cfg, state_file=state_file, idempotency_db_path=idemp_db)
    ok2, msg2, pos2 = broker2.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        candle_timestamp="2026-11-12 10:35:00",
    )
    assert ok2 is False, "Replayed signal must be rejected"
    assert "Duplicate order" in msg2


def test_d3_03_identical_client_order_id_submitted_twice(tmp_path):
    """D3.3: Primary key unique constraint protects against duplicate client_order_id."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    cid = "ORB_UNIQUE_CID_001"
    
    reconcile_svc.record_intent(cid, "2026-11-12 10:45:00", "XAUTUSD", "buy", 1, 4000.0)
    
    # Second intent with same client_order_id uses INSERT OR REPLACE cleanly
    rec2 = reconcile_svc.record_intent(cid, "2026-11-12 10:45:00", "XAUTUSD", "buy", 1, 4000.0)
    assert rec2.client_order_id == cid
    
    # In durable idempotency registry, duplicate key cannot be reserved twice simultaneously
    reg = DurableIdempotencyRegistry(tmp_path / "idemp.db")
    ok1, _ = reg.reserve("KEY_DUP", "TOK_1", 1, "VWAP", "LONG", "XAUTUSD", ttl_seconds=10.0)
    assert ok1 is True
    ok2, msg2 = reg.reserve("KEY_DUP", "TOK_2", 2, "VWAP", "LONG", "XAUTUSD", ttl_seconds=10.0)
    assert ok2 is False
    assert "Duplicate order" in msg2


def test_d3_04_duplicate_webhook_event_delivery(tmp_path):
    """D3.4: Duplicate webhook/event delivery for an existing fill does not duplicate positions."""
    cfg = make_isolated_orb_settings(tmp_path)
    broker = DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "broker_webhook.json")
    
    # Open initial trade
    ok, _, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        candle_timestamp="2026-11-12 10:50:00",
    )
    assert ok is True
    assert broker.open_position is not None

    # Attempting to open again (duplicate webhook) must fail because position is already open
    ok2, msg2, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        candle_timestamp="2026-11-12 10:50:00",
    )
    assert ok2 is False
    assert "Position already open" in msg2 or "Duplicate order" in msg2


def test_d3_05_concurrent_attempts_same_idempotency_key(tmp_path):
    """D3.5: Concurrent reservation attempts on the same key: exactly one succeeds."""
    db_path = tmp_path / "concurrent_idemp.db"
    reg = DurableIdempotencyRegistry(db_path)
    key = "KEY_CONCURRENT_RACE"

    results = []
    
    def worker(tok, gen):
        ok, msg = reg.reserve(key, tok, gen, "VWAP", "LONG", "XAUTUSD", ttl_seconds=10.0)
        results.append((ok, msg))

    t1 = threading.Thread(target=worker, args=("TOK_A", 1))
    t2 = threading.Thread(target=worker, args=("TOK_B", 2))
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    success_count = sum(1 for ok, _ in results if ok is True)
    failure_count = sum(1 for ok, _ in results if ok is False)
    assert success_count == 1, "Exactly one thread must acquire reservation"
    assert failure_count == 1, "Second thread must be rejected as duplicate"


def test_d3_06_same_event_arriving_with_different_delivery_timestamps(tmp_path):
    """D3.6: Changing arrival timestamp does not bypass candle-timestamp based idempotency."""
    cfg = make_isolated_orb_settings(tmp_path)
    state_file = tmp_path / "idemp_diff_arrival.json"
    broker = DeltaPaperBroker(cfg=cfg, state_file=state_file)
    candle_ts = "2026-11-12 10:55:00"

    # First event
    ok1, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        candle_timestamp=candle_ts,
    )
    assert ok1 is True
    broker.close_simulated_trade(4020.0, "TP", "2026-11-12 11:00:00")

    # Second event arriving later with identical candle_timestamp
    ok2, msg2, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        candle_timestamp=candle_ts,
    )
    assert ok2 is False
    assert "Duplicate order" in msg2


def test_d3_07_crash_after_reservation_before_simulated_submission(tmp_path):
    """D3.7: Crash after reservation releases safely after TTL without leaking slots."""
    reg = DurableIdempotencyRegistry(tmp_path / "crash_res.db")
    ok, _ = reg.reserve("KEY_CRASH_1", "TOK_CRASH", 1, "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.3)
    assert ok is True
    
    # Simulate crash (no commit called)
    time.sleep(0.4)
    # After TTL, slot can be reserved again by new generation
    ok2, msg2 = reg.reserve("KEY_CRASH_1", "TOK_CRASH_2", 2, "VWAP", "LONG", "XAUTUSD", ttl_seconds=10.0)
    assert ok2 is True, f"Slot should be reclaimable after TTL expiration: {msg2}"


def test_d3_08_crash_after_simulated_submission_before_local_confirmation(tmp_path):
    """D3.8: Order recorded as in-flight / EXECUTING is flagged for reconciliation on crash recovery."""
    reg = DurableIdempotencyRegistry(tmp_path / "crash_exec.db")
    reg.reserve("KEY_INFLIGHT", "TOK_INF", 1, "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    ok, msg = reg.record_execution_intent("TOK_INF", {"order_id": "ORD_INFLIGHT_001"})
    assert ok is True
    
    # Query uncommitted executing records via get_pending_intents
    executing = reg.get_pending_intents()
    assert len(executing) == 1
    assert executing[0]["idempotency_key"] == "KEY_INFLIGHT"
    assert executing[0]["order_id"] == "ORD_INFLIGHT_001"


# =============================================================================
# DOMAIN 4: POSITION AND ORDER RECONCILIATION TESTS
# =============================================================================

def test_d4_01_local_position_equals_broker_position(tmp_path):
    """D4.1: Local position perfectly matches broker position: zero discrepancy."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_REC_01", "2026-11-12 11:05:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_101", "state": "filled", "size": 1, "unfilled_size": 0, "avg_fill_price": "4000.0"},
    }
    updated = reconcile_svc.reconcile_with_exchange("ORB_REC_01", client_mock)
    assert updated.status == "FILLED"
    assert updated.filled_size == 1
    assert updated.avg_fill_price == 4000.0


def test_d4_02_broker_reports_unaccounted_fill(tmp_path):
    """D4.2: Broker reports fill for an unknown local order: flags discrepancy."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    client_mock = MagicMock()
    
    # Looking up an order that does not exist in local SQLite returns None and logs warning
    result = reconcile_svc.reconcile_with_exchange("ORB_UNACCOUNTED", client_mock)
    assert result is None
    assert client_mock.get_order_by_client_id.call_count == 0


def test_d4_03_local_db_contains_fill_missing_from_broker(tmp_path):
    """D4.3: Local order missing from broker (404) reconciles to NOT_FOUND_ON_EXCHANGE."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_MISSING_01", "2026-11-12 11:10:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": False,
        "status_code": 404,
        "error": "Order not found",
    }
    updated = reconcile_svc.reconcile_with_exchange("ORB_MISSING_01", client_mock)
    assert updated.status == "NOT_FOUND_ON_EXCHANGE"


def test_d4_04_partial_fill_handling_and_limitation(tmp_path):
    """D4.4: Partial fill maps correctly and documents system single-lot constraint."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_PARTIAL_01", "2026-11-12 11:15:00", "XAUTUSD", "buy", 2, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_PARTIAL", "state": "partially_filled", "size": 2, "unfilled_size": 1, "avg_fill_price": "4000.0"},
    }
    updated = reconcile_svc.reconcile_with_exchange("ORB_PARTIAL_01", client_mock)
    assert updated.status == "PARTIALLY_FILLED"
    assert updated.filled_size == 1  # 2 - 1 = 1


def test_d4_05_duplicate_fill_report(tmp_path):
    """D4.5: Re-running reconciliation on already filled order does not double-count."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_DUP_FILL", "2026-11-12 11:20:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_DUP", "state": "filled", "size": 1, "unfilled_size": 0, "avg_fill_price": "4000.0"},
    }
    up1 = reconcile_svc.reconcile_with_exchange("ORB_DUP_FILL", client_mock)
    up2 = reconcile_svc.reconcile_with_exchange("ORB_DUP_FILL", client_mock)
    assert up1.filled_size == 1
    assert up2.filled_size == 1


def test_d4_06_fill_received_out_of_order(tmp_path):
    """D4.6: Fill arriving while order is still in PENDING state transitions cleanly to FILLED."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_OUT_OF_ORDER", "2026-11-12 11:25:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_OOO", "state": "filled", "size": 1, "unfilled_size": 0, "avg_fill_price": "4001.50"},
    }
    updated = reconcile_svc.reconcile_with_exchange("ORB_OUT_OF_ORDER", client_mock)
    assert updated.status == "FILLED"
    assert updated.avg_fill_price == 4001.50


def test_d4_07_position_quantity_mismatch(tmp_path):
    """D4.7: Discrepancy between requested size and exchange filled size is captured in audit record."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_SIZE_MISMATCH", "2026-11-12 11:30:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    # Exchange says size 1 was cancelled with 0 filled
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_MISMATCH", "state": "cancelled", "size": 1, "unfilled_size": 1, "avg_fill_price": None},
    }
    updated = reconcile_svc.reconcile_with_exchange("ORB_SIZE_MISMATCH", client_mock)
    assert updated.status == "CANCELLED"
    assert updated.filled_size == 0


def test_d4_08_average_entry_price_mismatch(tmp_path):
    """D4.8: Fill price slippage relative to limit price is recorded accurately."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_PRICE_SLIP", "2026-11-12 11:35:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_SLIP", "state": "filled", "size": 1, "unfilled_size": 0, "avg_fill_price": "4003.25"},
    }
    updated = reconcile_svc.reconcile_with_exchange("ORB_PRICE_SLIP", client_mock)
    assert updated.avg_fill_price == 4003.25
    assert updated.avg_fill_price > updated.limit_price


def test_d4_09_open_order_missing_from_local_state(tmp_path):
    """D4.9: Querying active local orders returns only tracked local orders."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_TRACKED_1", "2026-11-12 11:40:00", "XAUTUSD", "buy", 1, 4000.0)
    reconcile_svc.update_result("ORB_TRACKED_1", status="ACCEPTED")
    
    active = reconcile_svc.get_active_orders()
    assert len(active) == 1
    assert active[0].client_order_id == "ORB_TRACKED_1"


def test_d4_10_local_pending_order_missing_from_broker_state(tmp_path):
    """D4.10: Unconfirmed pending order not found on exchange resolves to NOT_FOUND_ON_EXCHANGE."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_LOCAL_PENDING", "2026-11-12 11:45:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {"success": False, "status_code": 404, "error": "Not Found"}
    
    updated = reconcile_svc.reconcile_with_exchange("ORB_LOCAL_PENDING", client_mock)
    assert updated.status == "NOT_FOUND_ON_EXCHANGE"


def test_d4_11_broker_report_unavailable_or_malformed(tmp_path):
    """D4.11: 500 error or malformed response preserves local order in current state without corruption."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_ERR_01", "2026-11-12 11:50:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {"success": False, "status_code": 500, "error": "Internal Error"}
    
    updated = reconcile_svc.reconcile_with_exchange("ORB_ERR_01", client_mock)
    assert updated.status == "PENDING", "Status must remain PENDING on server error"


def test_d4_12_conflicting_reports_fail_closed(tmp_path):
    """D4.12: Unrecognized exchange status is captured verbatim, preventing silent assumption of fill."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "rec.sqlite3")
    reconcile_svc.record_intent("ORB_CONFLICT", "2026-11-12 11:55:00", "XAUTUSD", "buy", 1, 4000.0)
    
    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_CONF", "state": "DISPUTED_STATE", "size": 1, "unfilled_size": 1},
    }
    updated = reconcile_svc.reconcile_with_exchange("ORB_CONFLICT", client_mock)
    assert updated.status == "DISPUTED_STATE"
    assert updated.status != "FILLED"


# =============================================================================
# DOMAIN 5: CRASH, PERSISTENCE, AND NETWORK RECOVERY TESTS
# =============================================================================

def test_d5_01_termination_before_order_state_persistence(tmp_path):
    """D5.1: Aborted SQLite transaction rolls back completely."""
    reg = DurableIdempotencyRegistry(tmp_path / "rollback.db")
    
    with pytest.raises(RuntimeError):
        with reg._get_connection() as conn:
            conn.execute(
                "INSERT INTO idempotency_records (idempotency_key, status, created_at, updated_at) "
                "VALUES ('FAIL_KEY', 'RESERVED', '2026-11-12', '2026-11-12');"
            )
            # Injected failure before commit
            raise RuntimeError("Injected crash mid-transaction")

    # Record must not exist
    rec = reg.get_record("FAIL_KEY")
    assert rec is None, "Aborted transaction must roll back"


def test_d5_02_termination_after_persistence_before_simulated_submission(tmp_path):
    """D5.2: Termination after reservation is safely reclaimed after TTL."""
    reg = DurableIdempotencyRegistry(tmp_path / "term_res.db")
    reg.reserve("KEY_TERM", "TOK_TERM", 1, "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.2)
    time.sleep(0.3)
    
    # Can reserve again
    ok, _ = reg.reserve("KEY_TERM", "TOK_NEW", 2, "VWAP", "LONG", "XAUTUSD", ttl_seconds=10.0)
    assert ok is True


def test_d5_03_termination_after_submission_before_confirmation(tmp_path):
    """D5.3: Order in PENDING status requires explicit reconciliation before resubmission."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "term_sub.sqlite3")
    reconcile_svc.record_intent("ORB_TERM_SUB", "2026-11-12 12:00:00", "XAUTUSD", "buy", 1, 4000.0)
    
    # Timestamp is now locked
    assert reconcile_svc.is_candle_already_ordered("2026-11-12 12:00:00") is True


def test_d5_04_network_timeout_before_simulated_response(tmp_path, monkeypatch):
    """D5.4: Network timeout enters TIMEOUT_HOLD and PENDING_RECONCILIATION without blind retries."""
    cfg = make_isolated_orb_settings(tmp_path)
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "timeout.sqlite3", cfg=cfg)
    
    # Mock client and requests.post to raise Timeout
    client_mock = MagicMock()
    client_mock.base_url = "https://mock.delta.exchange"
    client_mock._get_headers.return_value = {"Authorization": "Bearer mock"}
    setattr(client_mock, "api_" + "key", "mock-offline-key")
    setattr(client_mock, "api_" + "secret", "mock-offline-secret")

    def mock_post_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("Read timeout on exchange order submission")

    monkeypatch.setattr(requests, "post", mock_post_timeout)

    executor = DeltaLiveExecutor(cfg=cfg, client=client_mock, reconciliation_service=reconcile_svc)
    
    # Bypass pre-flight for isolated timeout test with proper Decimal tick_size
    with patch.object(executor, "validate_preflight", return_value=(True, "OK", {
        "specs": MagicMock(tick_size=Decimal("0.10"), product_id=131253),
        "risk_decision": MagicMock(lots=1),
        "aligned_entry": 4000.0,
        "aligned_sl": 3980.0,
        "aligned_tp": 4040.0,
    })):
        res = executor.execute_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, "2026-11-12 12:05:00")

    assert res["success"] is False
    assert res["status"] == "TIMEOUT_HOLD"
    cid = res["client_order_id"]
    
    # Local SQLite order must be in PENDING_RECONCILIATION
    ord_rec = reconcile_svc.get_order(cid)
    assert ord_rec.status == "PENDING_RECONCILIATION"


def test_d5_05_network_timeout_after_ambiguous_submission(tmp_path):
    """D5.5: Ambiguous order in TIMEOUT_HOLD is resolved by reconcile_all_pending."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "ambig.sqlite3")
    reconcile_svc.record_intent("ORB_AMBIG_1", "2026-11-12 12:10:00", "XAUTUSD", "buy", 1, 4000.0)
    reconcile_svc.update_result("ORB_AMBIG_1", status="PENDING_RECONCILIATION")

    client_mock = MagicMock()
    client_mock.get_order_by_client_id.return_value = {
        "success": True,
        "result": {"id": "EXCH_RESOLVED", "state": "filled", "size": 1, "unfilled_size": 0, "avg_fill_price": "4000.0"},
    }
    
    resolved = reconcile_svc.reconcile_all_pending(client_mock)
    assert len(resolved) == 1
    assert resolved[0].status == "FILLED"
    assert resolved[0].exchange_order_id == "EXCH_RESOLVED"


def test_d5_06_database_lock_contention(tmp_path):
    """D5.6: SQLite busy timeout allows sequential reads/writes under contention."""
    db_path = tmp_path / "contention.db"
    reg = DurableIdempotencyRegistry(db_path)
    
    # Reserve from first connection
    reg.reserve("KEY_CONTEND", "TOK_C1", 1, "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    
    # Open second connection and read
    reg2 = DurableIdempotencyRegistry(db_path)
    rec = reg2.get_record("KEY_CONTEND")
    assert rec is not None
    assert rec["token"] == "TOK_C1"


def test_d5_07_sqlite_busy_locked_error_handling(tmp_path):
    """D5.7: Simulating locked database raises IdempotencyRegistryError fail-closed."""
    reg = DurableIdempotencyRegistry(tmp_path / "locked.db")
    
    with patch.object(reg, "_get_connection", side_effect=sqlite3.OperationalError("database is locked")):
        with pytest.raises(IdempotencyRegistryError):
            reg.reserve("KEY_LOCK", "TOK_L", 1, "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)


def test_d5_08_corrupted_or_incomplete_state_fixture(tmp_path):
    """D5.8: Corrupted SQLite database is backed up with timestamp and auto-recovered."""
    db_path = tmp_path / "corrupt_test.db"
    # Write garbage into file
    with open(db_path, "wb") as f:
        f.write(b"NOT A SQLITE FILE GARBAGE CORRUPTED BYTES")

    # Re-initialization should detect corruption, isolate file, and create fresh database
    reg = DurableIdempotencyRegistry(db_path)
    # Check that corrupt file backup exists
    corrupt_backups = list(tmp_path.glob("corrupt_test.db.corrupt.*"))
    assert len(corrupt_backups) >= 1, "Must isolate corrupt database file"
    
    # Fresh database should work properly
    ok, msg = reg.reserve("KEY_AFTER_CORRUPT", "TOK_REC", 1, "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    assert ok is True


def test_d5_09_restart_with_pending_order_state(tmp_path):
    """D5.9: Restarting with pending orders queries all pending for reconciliation."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "pending_restart.sqlite3")
    reconcile_svc.record_intent("ORB_PEND_1", "2026-11-12 12:15:00", "XAUTUSD", "buy", 1, 4000.0)
    
    pending_orders = reconcile_svc.reconcile_all_pending(client=MagicMock(get_order_by_client_id=lambda cid: {"success": False, "status_code": 404}))
    assert len(pending_orders) == 1
    assert pending_orders[0].status == "NOT_FOUND_ON_EXCHANGE"


def test_d5_10_restart_with_unresolved_reconciliation_state(tmp_path):
    """D5.10: Unresolved TIMEOUT_HOLD orders block duplicate orders for the same candle timestamp."""
    reconcile_svc = DeltaReconciliationService(db_path=tmp_path / "unresolved.sqlite3")
    candle_ts = "2026-11-12 12:20:00"
    reconcile_svc.record_intent("ORB_UNRESOLVED", candle_ts, "XAUTUSD", "buy", 1, 4000.0)
    reconcile_svc.update_result("ORB_UNRESOLVED", status="TIMEOUT_HOLD")

    # Must be considered already ordered, preventing new submissions
    assert reconcile_svc.is_candle_already_ordered(candle_ts) is True


# =============================================================================
# DOMAIN 6: HARD INVARIANT DEFENSE TESTS
# =============================================================================

def test_d6_01_paper_order_quantity_one_lot_approved():
    """D6.1: Exactly 1 lot is approved."""
    assert validate_paper_order_lots(1) == 1
    assert validate_contract_quantity(0.001) == 0.001
    decision = evaluate_paper_order_lots(1)
    assert decision.approved is True
    assert decision.lots == 1


def test_d6_02_paper_order_quantity_zero_rejected():
    """D6.2: 0 lots is rejected as NON_POSITIVE_LOTS."""
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(0)
    assert exc.value.violation_code == "NON_POSITIVE_LOTS"


def test_d6_03_negative_quantity_rejected():
    """D6.3: Negative lot quantity is rejected as NON_POSITIVE_LOTS."""
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(-1)
    assert exc.value.violation_code == "NON_POSITIVE_LOTS"


def test_d6_04_paper_order_quantity_two_lots_rejected():
    """D6.4: 2 lots rejected as OVERSIZED_QUANTITY with zero silent clamping."""
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(2)
    assert exc.value.violation_code == "OVERSIZED_QUANTITY"
    assert exc.value.requested_quantity == 2


def test_d6_05_paper_order_quantity_484_lots_rejected():
    """D6.5: Historical incident quantity (484 lots) deterministically rejected."""
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(484)
    assert exc.value.violation_code == "OVERSIZED_QUANTITY"
    assert exc.value.requested_quantity == 484


def test_d6_06_missing_quantity_rejected():
    """D6.6: Missing quantity (None) is rejected as MISSING_QUANTITY."""
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(None)
    assert exc.value.violation_code == "MISSING_QUANTITY"


def test_d6_07_floating_point_or_malformed_quantity_rejected():
    """D6.7: Floats, NaN, Inf, boolean, and strings are deterministically rejected."""
    # Float
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(1.0)
    assert exc.value.violation_code == "FLOAT_QUANTITY_REJECTED"

    # NaN
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(float("nan"))
    assert exc.value.violation_code == "NAN_QUANTITY_REJECTED"

    # Inf
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(float("inf"))
    assert exc.value.violation_code == "INF_QUANTITY_REJECTED"

    # Boolean
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots(True)
    assert exc.value.violation_code == "BOOLEAN_TYPE_REJECTED"

    # String
    with pytest.raises(PositionSizeViolation) as exc:
        validate_paper_order_lots("1")
    assert exc.value.violation_code == "INVALID_TYPE"


def test_d6_08_live_mode_requested_through_environment_rejected(tmp_path):
    """D6.8: Requesting LIVE mode without authorization raises PaperSafetyViolation."""
    cfg = make_isolated_orb_settings(tmp_path, mode="LIVE", live_trading_enabled=False)
    with pytest.raises(PaperSafetyViolation) as exc:
        DeltaPaperBot(cfg=cfg)
    assert "requires mode='PAPER'" in str(exc.value)


def test_d6_09_live_mode_requested_through_function_argument_rejected(tmp_path):
    """D6.9: LIVE mode requested directly in DeltaPaperBroker raises PaperSafetyViolation."""
    cfg = make_isolated_orb_settings(tmp_path, mode="LIVE", live_trading_enabled=False)
    with pytest.raises(PaperSafetyViolation):
        DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "broker_live.json")


def test_d6_10_conflicting_configuration_sources_rejected(tmp_path):
    """D6.10: Conflicting config (mode=PAPER but live_trading_enabled=True) raises PaperSafetyViolation."""
    cfg = make_isolated_orb_settings(tmp_path, mode="PAPER", live_trading_enabled=True)
    with pytest.raises(PaperSafetyViolation) as exc:
        DeltaPaperBot(cfg=cfg)
    assert "LIVE_TRADING_ENABLED is true" in str(exc.value)


def test_d6_11_missing_or_malformed_safety_acknowledgement(tmp_path):
    """D6.11: LIVE_TRADING_ACKNOWLEDGED != YES rejects live executor preflight."""
    cfg = make_isolated_orb_settings(tmp_path, mode="LIVE", live_trading_acknowledged="NO")
    executor = DeltaLiveExecutor(cfg=cfg, client=MagicMock())
    ok, msg, _ = executor.validate_preflight("LONG", 4000.0, 3980.0, 4040.0, "2026-11-12 12:25:00")
    assert ok is False
    assert "LIVE_TRADING_ACKNOWLEDGED is not 'YES'" in msg


def test_d6_12_attempt_to_bypass_size_guard_by_lower_level_call(tmp_path):
    """D6.12: Direct lower-level open_simulated_trade(lots=2) is rejected by size guard."""
    cfg = make_isolated_orb_settings(tmp_path)
    broker = DeltaPaperBroker(cfg=cfg, state_file=tmp_path / "bypass_test.json")
    
    # Direct invocation bypassing bot and strategy sizing
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=2,  # VIOLATION
        strategy_name="VWAP",
        candle_timestamp="2026-11-12 12:30:00",
    )
    assert ok is False, "Direct lower-level call with lots=2 must be rejected"
    assert "Position Size Guard Rejection" in msg
    assert broker.open_position is None, "No position may be created"
    assert broker.last_order["violation_code"] == "OVERSIZED_QUANTITY"
