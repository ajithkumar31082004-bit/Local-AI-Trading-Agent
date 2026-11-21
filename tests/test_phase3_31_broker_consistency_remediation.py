"""PHASE 3.31 — Broker State Consistency & SQLite Durability Remediation Test Suite.

Verifies:
1. Recoverable execution transaction across all crash boundaries.
2. No cash deduction without position or explicit recoverable pending state.
3. Deterministic roll-forward and roll-back recovery of EXECUTING intents.
4. SQLite durability (PRAGMA synchronous = FULL, WAL mode, transaction commit/rollback).
5. WAL checkpointing and Online Backup API with isolated restore.
6. Windows deterministic connection teardown and auto-recovery.
7. Strict preservation of safety invariants (1-lot cap, LIVE disabled, historical records).
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from services.idempotency_registry import DurableIdempotencyRegistry, IdempotencyRegistryError
from risk.position_size_guard import (
    validate_paper_order_lots,
    validate_contract_quantity,
    PositionSizeViolation,
    APPROVED_MAX_PAPER_LOTS,
)


# =============================================================================
# 1. CRASH RECOVERY BOUNDARY INJECTION TESTS (STEP 3)
# =============================================================================

def test_crash_boundary_before_reservation(tmp_path):
    """Crash Point 1: Crash occurs before reservation is initiated."""
    state_file = tmp_path / "crash_before_res.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    # Crash injected before reserve_order_slot()
    del broker

    # Restart
    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.cash_balance == initial_cash
    assert restarted.open_position is None
    assert restarted._pending_reservation is None
    assert restarted._idempotency_registry.count_records() == 0


def test_crash_boundary_after_reservation_before_intent(tmp_path):
    """Crash Point 2: Crash occurs after reservation, before execution intent is recorded."""
    state_file = tmp_path / "crash_after_res.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    ok, msg, token = broker.reserve_order_slot("KEY_CRASH_RES", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.5)
    assert ok is True
    # Crash injected before record_execution_intent()
    del broker

    # Restart after reservation TTL expires
    time.sleep(0.6)
    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.cash_balance == initial_cash, "Cash must be conserved"
    assert restarted.open_position is None, "No phantom position"

    # Same key retry must now succeed because previous uncommitted reservation expired
    ok2, msg2, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_CRASH_RES"
    )
    assert ok2 is True, "Same-key retry must succeed after expired reservation"
    assert restarted.open_position is not None


def test_crash_boundary_after_intent_before_json_persistence(tmp_path):
    """Crash Point 3: CRITICAL GAP RESOLUTION - Crash after SQLite intent recorded, before JSON persistence.
    
    Verifies that if a crash occurs after record_execution_intent() (status='EXECUTING')
    but BEFORE JSON state is saved to disk:
    1. Cash was never persisted as deducted.
    2. Position was never persisted.
    3. On restart, _recover_pending_execution_intents() deterministically ROLLS BACK the intent.
    4. Cash remains 100% conserved with zero phantom deductions.
    5. The caller can safely retry with the same key without being blocked as duplicate!
    """
    state_file = tmp_path / "crash_intent_unpersisted.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    # Reserve slot
    ok, msg, token = broker.reserve_order_slot("KEY_INTENT_CRASH", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    assert ok is True

    # Record execution intent directly into SQLite WAL
    intent_payload = {
        "order_id": "ORD_CRASH_INTENT",
        "trade_id": "PAPER_CRASH_INTENT",
        "cash_outflow": 0.40,
        "entry_fee": 0.40,
        "prev_cash_balance": initial_cash,
        "new_cash_balance": initial_cash - 0.40,
    }
    intent_ok, _ = broker._idempotency_registry.record_execution_intent(token, intent_payload)
    assert intent_ok is True

    # Verify SQLite is now EXECUTING
    rec = broker._idempotency_registry.get_record("KEY_INTENT_CRASH")
    assert rec["status"] == "EXECUTING"

    # CRASH INJECTED HERE: Before broker._save_state() is called!
    del broker

    # Restart broker: recovery must run on startup
    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. Verify cash is 100% conserved
    assert restarted.cash_balance == initial_cash, "Cash must not be deducted when position was unpersisted"
    assert restarted.open_position is None, "No phantom open position"

    # 2. Verify SQLite intent was deterministically rolled back
    rec_after = restarted._idempotency_registry.get_record("KEY_INTENT_CRASH")
    assert rec_after["status"] == "ROLLED_BACK", "Unpersisted intent must be rolled back"

    # 3. Verify same-key retry is safe and succeeds!
    retry_ok, retry_msg, retry_pos = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_INTENT_CRASH"
    )
    assert retry_ok is True, f"Same key retry must succeed after rollback: {retry_msg}"
    assert restarted.open_position is not None
    assert restarted.open_position["idempotency_key"] == "KEY_INTENT_CRASH"
    assert restarted.cash_balance < initial_cash, "Cash deducted only after genuine persistence"


def test_crash_boundary_after_json_persistence_before_sqlite_committed(tmp_path):
    """Crash Point 4: Crash after JSON persistence with open position, before SQLite COMMITTED.
    
    Verifies that if a crash occurs after the JSON state has been saved with the position,
    but before SQLite transition to COMMITTED completes:
    1. On restart, _recover_pending_execution_intents() sees open_position in JSON.
    2. Recovery deterministically COMPLETES the intent to COMMITTED in SQLite.
    3. Position and cash are preserved intact.
    4. Duplicate replay of the same key is strictly rejected.
    """
    state_file = tmp_path / "crash_after_json.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    # Reserve slot
    ok, msg, token = broker.reserve_order_slot("KEY_AFTER_JSON", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    assert ok is True

    # Record intent in SQLite
    pos = {
        "trade_id": "PAPER_AFTER_JSON",
        "order_id": "ORD_AFTER_JSON",
        "idempotency_key": "KEY_AFTER_JSON",
        "reservation_token": token,
        "symbol": "XAUTUSD",
        "strategy_name": "VWAP",
        "direction": "LONG",
        "side": "LONG",
        "entry_time": "2026-11-10T12:00:00Z",
        "entry_price": 4000.0,
        "signal_entry_price": 4000.0,
        "market_price": 4000.0,
        "lots": 1,
        "requested_lots": 1,
        "filled_lots": 1,
        "unfilled_lots": 0,
        "contract_qty": 0.001,
        "stop_loss": 3980.0,
        "take_profit": 4040.0,
        "margin": 200.0,
        "order_type": "LIMIT",
        "entry_order_type": "LIMIT",
        "fill_ratio": 1.0,
        "market_regime": "RANGING",
        "fee_rate_pct": 0.01,
        "entry_fee": 0.04,
        "entry_gst": 0.0,
        "exit_fee": 0.0,
        "exit_gst": 0.0,
        "slippage_cost": 0.0,
        "unrealized_pnl": 0.0,
        "signal_reason": "VWAP_CROSS",
    }
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_AFTER_JSON", "trade_id": "PAPER_AFTER_JSON", "pos": pos})

    # Save JSON state with position (simulating step 3 complete)
    broker.cash_balance = round(initial_cash - 0.04, 4)
    broker.total_fees = 0.04
    broker.open_position = pos
    broker._pending_reservation = None
    broker._save_state()

    # INJECT CRASH HERE before broker._idempotency_registry.commit_execution()
    del broker

    # Restart broker
    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. Position and cash preserved
    assert restarted.open_position is not None
    assert restarted.open_position["order_id"] == "ORD_AFTER_JSON"
    assert restarted.cash_balance == round(initial_cash - 0.04, 4)

    # 2. SQLite record deterministically transitioned to COMMITTED
    rec = restarted._idempotency_registry.get_record("KEY_AFTER_JSON")
    assert rec["status"] == "COMMITTED", "Intent must be completed to COMMITTED on recovery"

    # 3. Duplicate replay must be rejected
    dup_ok, dup_msg, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_AFTER_JSON"
    )
    assert dup_ok is False
    assert "already executed" in dup_msg or "Position already open" in dup_msg


def test_crash_boundary_before_caller_acknowledgement(tmp_path):
    """Crash Point 5: Crash after both stores committed, immediately before ack returned."""
    state_file = tmp_path / "crash_before_ack.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Execute trade fully
    ok, msg, pos = broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_BEFORE_ACK"
    )
    assert ok is True
    del broker

    # Restart
    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.open_position is not None
    assert restarted.open_position["idempotency_key"] == "KEY_BEFORE_ACK"

    # Caller retries same key (believing trade may have timed out before ack)
    retry_ok, retry_msg, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_BEFORE_ACK"
    )
    assert retry_ok is False, "Duplicate execution must be prevented"


def test_repeated_crash_recovery_determinism(tmp_path):
    """Crash Point 6: Verify deterministic recovery across repeated process restarts."""
    state_file = tmp_path / "repeat_recovery.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Record unpersisted intent
    ok, _, token = broker.reserve_order_slot("KEY_REPEAT", "VWAP", "LONG", "XAUTUSD", 30.0)
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_REP", "trade_id": "TR_REP"})
    del broker

    # First restart: triggers rollback
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    rec1 = b1._idempotency_registry.get_record("KEY_REPEAT")
    assert rec1["status"] == "ROLLED_BACK"
    del b1

    # Second restart: state remains stable
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    rec2 = b2._idempotency_registry.get_record("KEY_REPEAT")
    assert rec2["status"] == "ROLLED_BACK"
    assert b2.open_position is None


# =============================================================================
# 2. SQLITE DURABILITY SETTINGS (STEP 4)
# =============================================================================

def test_sqlite_durability_full_synchronous(tmp_path):
    """Step 4: Verify PRAGMA synchronous = FULL, WAL mode, and measure commit latency."""
    db_path = tmp_path / "durability_full.db"
    reg = DurableIdempotencyRegistry(db_path)

    with reg._get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]

    assert str(journal_mode).lower() == "wal", "Must be WAL mode"
    assert int(synchronous) == 2, "Must be configured as FULL (2)"
    assert int(busy_timeout) == 10000, "Busy timeout must be 10000ms"

    # Measure latency of 50 commit transactions
    t0 = time.perf_counter()
    for i in range(50):
        ok, token = reg.reserve(f"KEY_LAT_{i}", f"TOK_{i}", i, "VWAP", "LONG", "XAUTUSD", 30.0)
        assert ok is True
    elapsed = time.perf_counter() - t0
    avg_latency_ms = (elapsed / 50) * 1000
    print(f"\nAverage reserve latency under PRAGMA synchronous=FULL: {avg_latency_ms:.2f} ms")
    assert avg_latency_ms < 50.0, "Latency overhead must remain well within tolerable limits"


# =============================================================================
# 3. WAL CHECKPOINT & ONLINE BACKUP LIFECYCLE (STEP 5)
# =============================================================================

def test_wal_checkpoint_truncate(tmp_path):
    """Step 5.1: Test explicit WAL checkpoint TRUNCATE behavior."""
    state_file = tmp_path / "chk_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Generate transactions
    for i in range(5):
        broker.open_simulated_trade(
            "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key=f"KEY_CHK_{i}"
        )
        broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    wal_file = state_file.with_suffix(".idempotency.db-wal")
    # Execute explicit checkpoint
    busy, log_frames, checkpointed = broker.checkpoint_db("TRUNCATE")
    assert busy == 0, "Checkpoint must not be busy"
    if wal_file.exists():
        assert wal_file.stat().st_size == 0, "TRUNCATE checkpoint must zero WAL size"


def test_backup_online_api_and_restore(tmp_path):
    """Step 5.2: Test SQLite Online Backup API and restore to isolated directory."""
    state_file = tmp_path / "active_account.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Execute 3 trades
    for i in range(3):
        broker.open_simulated_trade(
            "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key=f"KEY_BAK_{i}"
        )
        broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # Take backup using broker.backup_state()
    backup_dir = tmp_path / "backups" / "snapshot_01"
    json_bak, db_bak = broker.backup_state(backup_dir)

    assert json_bak.exists()
    assert db_bak.exists()

    # Restore backup into completely isolated target directory
    restore_dir = tmp_path / "restored_account"
    restore_state = restore_dir / "restored.json"
    restore_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(json_bak, restore_state)
    shutil.copy2(db_bak, restore_state.with_suffix(".idempotency.db"))

    # Initialize broker in restored directory
    restored_broker = DeltaPaperBroker(state_file=str(restore_state), initial_equity=10000.0)
    assert len(restored_broker.closed_trades) == 3

    # Verify that all 3 backed-up keys are recognized as duplicates
    for i in range(3):
        dup_ok, dup_msg, _ = restored_broker.open_simulated_trade(
            "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key=f"KEY_BAK_{i}"
        )
        assert dup_ok is False, f"KEY_BAK_{i} must be blocked as duplicate in restored environment"


# =============================================================================
# 4. WINDOWS CONNECTION TEARDOWN & CORRUPTION AUTO-RECOVERY (STEP 6)
# =============================================================================

def test_windows_connection_teardown_corrupt_recovery(tmp_path):
    """Step 6.1: Verify connection handles are closed deterministically and corrupt DB is isolated."""
    state_file = tmp_path / "corrupt_win_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_WIN_CORRUPT")
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    broker.checkpoint_db("TRUNCATE")
    del broker

    # Corrupt SQLite database file
    db_file = state_file.with_suffix(".idempotency.db")
    db_file.write_bytes(b"CORRUPTED_GARBAGE_BYTES_HEADER\x00\xFF\xAA")

    # Start broker: auto-recovery isolates corrupt file to .corrupt.* and repopulates history
    recovered = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    corrupt_files = list(tmp_path.glob("*.corrupt.*"))
    assert len(corrupt_files) >= 1, "Corrupt database must be isolated"

    # Replay of pre-corruption key must be rejected
    ok_rep, msg_rep, _ = recovered.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_WIN_CORRUPT"
    )
    assert ok_rep is False, "Historical key must remain protected after recovery"


def test_windows_fail_closed_when_history_unrecoverable(tmp_path):
    """Step 6.2: Verify broker fails closed when database is corrupt AND state history is lost."""
    state_file = tmp_path / "fail_closed_history.json"
    db_file = state_file.with_suffix(".idempotency.db")

    b = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    b.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_UNREC")
    b.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    b.checkpoint_db("TRUNCATE")
    del b

    # Corrupt DB and destroy state file
    db_file.write_bytes(b"CORRUPTED_ZEROS\x00\x00")
    state_file.unlink()

    # Must fail closed with IdempotencyRegistryError
    with pytest.raises(IdempotencyRegistryError) as excinfo:
        DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert "Cannot establish complete idempotency history" in str(excinfo.value) or "Database initialization and recovery failed" in str(excinfo.value)


# =============================================================================
# 5. SAFETY INVARIANTS (STEP 7)
# =============================================================================

def test_safety_invariants_one_lot_and_live_disabled(tmp_path):
    """Step 7: Verify maximum 1 micro-lot limit, oversized rejections, LIVE blocked, and incident intact."""
    state_file = tmp_path / "safety_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. 1-lot maximum invariant
    assert APPROVED_MAX_PAPER_LOTS == 1

    # 2. Oversized orders rejected
    with pytest.raises(PositionSizeViolation):
        validate_paper_order_lots(2)
    with pytest.raises(PositionSizeViolation):
        validate_paper_order_lots(484)

    ok_2, msg_2, _ = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 2, "VWAP", idempotency_key="KEY_2LOT")
    assert ok_2 is False

    # 3. LIVE trading barrier
    with pytest.raises(PaperSafetyViolation):
        broker.execute_live_order()

    # 4. Historical incident PAPER_20261105_101000_LONG intact
    prod_state_path = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    if prod_state_path.exists():
        with open(prod_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        trades = data.get("closed_trades", [])
        incident = next((t for t in trades if t.get("trade_id") == "PAPER_20261105_101000_LONG"), None)
        assert incident is not None
        assert incident.get("lots") == 484
        assert incident.get("net_pnl") == -7.9472
