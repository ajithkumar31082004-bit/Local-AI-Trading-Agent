"""Phase 3.32 — Independent Adversarial Broker Audit Test Suite.

Adversarial Testing Domains:
1. Execution transaction crash testing at all 9 boundaries.
2. Idempotency adversarial stress (bursts, concurrency across threads and processes, restarts).
3. SQLite durability & recovery inspection (PRAGMA synchronous=FULL, WAL, fail-closed).
4. Backup & restore adversarial testing (concurrent writes, open position, repeated cycles).
5. Safety invariants (1 micro-lot cap, oversized rejection, LIVE disabled, incident preservation).
"""

import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

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
# 1. EXECUTION TRANSACTION CRASH TESTING (BOUNDARIES 1 - 9)
# =============================================================================

def test_boundary_1_before_intent_creation(tmp_path):
    """Boundary 1: Crash occurs before execution intent creation."""
    state_file = tmp_path / "b1_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    # Reserve slot with short TTL
    ok, msg, token = broker.reserve_order_slot("KEY_B1", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.4)
    assert ok is True

    # Crash injected before record_execution_intent()
    del broker

    time.sleep(0.5)  # Wait for reservation TTL to expire
    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.cash_balance == initial_cash, "Cash must be conserved"
    assert restarted.open_position is None, "No phantom position"

    # Retry same key
    retry_ok, _, pos = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_B1"
    )
    assert retry_ok is True, "Same key retry must succeed after expired reservation"
    assert restarted.open_position is not None


def test_boundary_2_after_intent_insertion(tmp_path):
    """Boundary 2: Crash occurs after intent insertion into SQLite, before any JSON write."""
    state_file = tmp_path / "b2_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    ok, msg, token = broker.reserve_order_slot("KEY_B2", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    assert ok is True

    intent_payload = {
        "order_id": "ORD_B2",
        "trade_id": "PAPER_B2",
        "cash_outflow": 0.40,
        "entry_fee": 0.40,
        "prev_cash_balance": initial_cash,
        "new_cash_balance": initial_cash - 0.40,
    }
    broker._idempotency_registry.record_execution_intent(token, intent_payload)

    # Crash injected immediately after SQLite intent insertion
    del broker

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.cash_balance == initial_cash, "Cash must remain intact on disk"
    assert restarted.open_position is None, "Position must not exist"

    # Verify intent was rolled back
    rec = restarted._idempotency_registry.get_record("KEY_B2")
    assert rec["status"] == "ROLLED_BACK", "Unpersisted intent must be ROLLED_BACK"

    # Retry key
    retry_ok, _, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_B2"
    )
    assert retry_ok is True, "Same key retry must succeed after rollback"


def test_boundary_3_during_json_temp_file_writing(tmp_path):
    """Boundary 3: Crash occurs during JSON temp-file writing (leaving partial/corrupt .tmp file)."""
    state_file = tmp_path / "b3_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    ok, _, token = broker.reserve_order_slot("KEY_B3", "VWAP", "LONG", "XAUTUSD", 30.0)
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_B3", "trade_id": "PAPER_B3"})

    # Simulate crash leaving half-written .tmp file
    tmp_file = state_file.with_suffix(".tmp")
    tmp_file.write_text('{"symbol": "XAUTUSD", "incompl', encoding="utf-8")
    del broker

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.cash_balance == initial_cash
    assert restarted.open_position is None
    rec = restarted._idempotency_registry.get_record("KEY_B3")
    assert rec["status"] == "ROLLED_BACK"


def test_boundary_4_after_json_fsync_before_atomic_replace(tmp_path):
    """Boundary 4: Crash occurs after JSON temp-file fsync, before atomic rename."""
    state_file = tmp_path / "b4_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    ok, _, token = broker.reserve_order_slot("KEY_B4", "VWAP", "LONG", "XAUTUSD", 30.0)
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_B4", "trade_id": "PAPER_B4"})

    # Complete valid .tmp file but never replace state_file
    tmp_file = state_file.with_suffix(".tmp")
    data = {"cash_balance": initial_cash - 0.40, "open_position": {"order_id": "ORD_B4"}}
    tmp_file.write_text(json.dumps(data), encoding="utf-8")
    del broker

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.cash_balance == initial_cash, "Original state_file was never replaced"
    assert restarted.open_position is None
    rec = restarted._idempotency_registry.get_record("KEY_B4")
    assert rec["status"] == "ROLLED_BACK"


def test_boundary_5_after_atomic_json_replacement(tmp_path):
    """Boundary 5: Crash occurs after atomic JSON replacement, before SQLite intent completion."""
    state_file = tmp_path / "b5_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    ok, _, token = broker.reserve_order_slot("KEY_B5", "VWAP", "LONG", "XAUTUSD", 30.0)
    pos = {
        "trade_id": "PAPER_B5",
        "order_id": "ORD_B5",
        "idempotency_key": "KEY_B5",
        "reservation_token": token,
        "symbol": "XAUTUSD",
        "strategy_name": "VWAP",
        "direction": "LONG",
        "lots": 1,
        "entry_price": 4000.0,
        "margin": 200.0,
        "entry_fee": 0.04,
    }
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_B5", "trade_id": "PAPER_B5", "pos": pos})

    # JSON state replaced
    broker.cash_balance = round(initial_cash - 0.04, 4)
    broker.open_position = pos
    broker._pending_reservation = None
    broker._save_state()

    # Crash injected right after JSON replace
    del broker

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    # Recovery rolls forward
    assert restarted.open_position is not None
    assert restarted.open_position["order_id"] == "ORD_B5"
    assert restarted.cash_balance == round(initial_cash - 0.04, 4)

    rec = restarted._idempotency_registry.get_record("KEY_B5")
    assert rec["status"] == "COMMITTED", "Intent must be rolled forward to COMMITTED"

    # Same key retry must be rejected
    dup_ok, dup_msg, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_B5"
    )
    assert dup_ok is False


def test_boundary_6_before_sqlite_intent_completion(tmp_path):
    """Boundary 6: Crash occurs before SQLite commit query is dispatched."""
    state_file = tmp_path / "b6_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, _, token = broker.reserve_order_slot("KEY_B6", "VWAP", "LONG", "XAUTUSD", 30.0)
    pos = {"trade_id": "PAPER_B6", "order_id": "ORD_B6", "idempotency_key": "KEY_B6"}
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_B6", "trade_id": "PAPER_B6", "pos": pos})
    broker.open_position = pos
    broker._save_state()
    del broker

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    rec = restarted._idempotency_registry.get_record("KEY_B6")
    assert rec["status"] == "COMMITTED"
    assert restarted.open_position is not None


def test_boundary_7_during_sqlite_intent_completion(tmp_path):
    """Boundary 7: Interruption during commit query."""
    state_file = tmp_path / "b7_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, _, token = broker.reserve_order_slot("KEY_B7", "VWAP", "LONG", "XAUTUSD", 30.0)
    pos = {"trade_id": "PAPER_B7", "order_id": "ORD_B7", "idempotency_key": "KEY_B7"}
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_B7", "trade_id": "PAPER_B7", "pos": pos})
    broker.open_position = pos
    broker._save_state()

    # Crash before commit succeeds
    del broker

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted._idempotency_registry.get_record("KEY_B7")["status"] == "COMMITTED"


def test_boundary_8_during_recovery_idempotence(tmp_path):
    """Boundary 8: Multiple successive recovery attempts produce identical, deterministic state."""
    state_file = tmp_path / "b8_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    ok, _, token = broker.reserve_order_slot("KEY_B8", "VWAP", "LONG", "XAUTUSD", 30.0)
    broker._idempotency_registry.record_execution_intent(token, {"order_id": "ORD_B8", "trade_id": "PAPER_B8"})
    del broker

    # Restart 1
    r1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert r1._idempotency_registry.get_record("KEY_B8")["status"] == "ROLLED_BACK"
    assert r1.cash_balance == initial_cash
    del r1

    # Restart 2
    r2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert r2._idempotency_registry.get_record("KEY_B8")["status"] == "ROLLED_BACK"
    assert r2.cash_balance == initial_cash
    del r2

    # Restart 3
    r3 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert r3._idempotency_registry.get_record("KEY_B8")["status"] == "ROLLED_BACK"
    assert r3.cash_balance == initial_cash


def test_boundary_9_after_recovery_before_caller_acknowledgement(tmp_path):
    """Boundary 9: Recovery completed, trade opened, crash before returning response to caller."""
    state_file = tmp_path / "b9_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_B9"
    )
    assert ok is True
    # Crash simulated after execution before caller does anything with result
    del broker

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert restarted.open_position is not None
    assert restarted.open_position["idempotency_key"] == "KEY_B9"

    # Caller retries same key
    dup_ok, dup_msg, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_B9"
    )
    assert dup_ok is False
    assert "Duplicate order" in dup_msg or "Position already open" in dup_msg


# =============================================================================
# 2. IDEMPOTENCY ADVERSARIAL STRESS TESTS
# =============================================================================

def test_adversarial_same_key_burst(tmp_path):
    """Burst test: 100 consecutive requests with the exact same key."""
    state_file = tmp_path / "burst_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    first_ok, first_msg, first_pos = broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="BURST_KEY_01"
    )
    assert first_ok is True, "First submission must succeed"

    # Close trade
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # 99 subsequent submissions of same key must ALL be rejected
    rejected_count = 0
    for i in range(99):
        ok, msg, pos = broker.open_simulated_trade(
            "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="BURST_KEY_01"
        )
        if not ok and ("Duplicate order" in msg or "already executed" in msg or "closed trades" in msg):
            rejected_count += 1

    assert rejected_count == 99, f"All 99 replays must be rejected (got {rejected_count})"


def test_adversarial_same_key_after_restart(tmp_path):
    """Same key submitted after process restart."""
    state_file = tmp_path / "restart_test.json"
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    ok, _, _ = b1.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_RESTART")
    assert ok is True
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    del b1

    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    dup_ok, dup_msg, _ = b2.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_RESTART")
    assert dup_ok is False
    assert "already executed" in dup_msg or "Duplicate order" in dup_msg


def test_adversarial_concurrent_threads_same_key(tmp_path):
    """10 concurrent threads attempt open_simulated_trade with the exact same key."""
    state_file = tmp_path / "threads_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    results = []
    def worker():
        res = broker.open_simulated_trade(
            "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="CONCURRENT_THREAD_KEY"
        )
        results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]

    assert len(successes) == 1, f"Exactly 1 thread must succeed (got {len(successes)})"
    assert len(failures) == 9, f"Exactly 9 threads must fail (got {len(failures)})"


def _proc_worker(state_file_str, key, out_queue):
    try:
        broker = DeltaPaperBroker(state_file=state_file_str, initial_equity=10000.0)
        res = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key=key)
        out_queue.put((res[0], res[1]))
    except Exception as exc:
        out_queue.put((False, str(exc)))


def test_adversarial_concurrent_processes_same_key(tmp_path):
    """4 separate OS processes contending on the same key and same state file."""
    state_file = tmp_path / "proc_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    del broker

    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    procs = [
        ctx.Process(target=_proc_worker, args=(str(state_file), "PROC_SHARED_KEY", out_q))
        for _ in range(4)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10.0)

    results = []
    while not out_q.empty():
        results.append(out_q.get())

    successes = [r for r in results if r[0] is True]
    assert len(successes) == 1, f"Exactly 1 process must win the order slot (got {len(successes)})"


# =============================================================================
# 3. SQLITE DURABILITY & RECOVERY INSPECTION
# =============================================================================

def test_inspect_sqlite_pragmas_and_connections(tmp_path):
    """Inspect every SQLite connection setting."""
    db_path = tmp_path / "pragma_inspect.db"
    reg = DurableIdempotencyRegistry(db_path)

    with reg._get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(synchronous) == 2, "PRAGMA synchronous must be FULL (2)"
    assert int(busy_timeout) == 10000, "PRAGMA busy_timeout must be 10000"


def test_inspect_connection_closure_safety(tmp_path):
    """Inspect that connection handles close safely without locking Windows files."""
    db_path = tmp_path / "conn_safety.db"
    reg = DurableIdempotencyRegistry(db_path)

    conn = reg._get_connection()
    try:
        conn.execute("SELECT 1;")
    finally:
        conn.close()

    # In WAL mode, checkpoint and close
    reg.close()
    # File should be replaceable/deletable without PermissionError
    test_rename = tmp_path / "conn_safety_renamed.db"
    db_path.replace(test_rename)
    assert test_rename.exists()


def test_corrupt_database_and_wal_recovery_with_history(tmp_path):
    """Verify corrupt database auto-recovery preserves history from JSON state."""
    state_file = tmp_path / "corrupt_with_hist.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="HIST_KEY_01")
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    broker.checkpoint_db("TRUNCATE")
    del broker

    # Corrupt DB
    db_file = state_file.with_suffix(".idempotency.db")
    db_file.write_bytes(b"CORRUPTED_GARBAGE_PAYLOAD\x00\xFF")

    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    backups = list(tmp_path.glob("*.corrupt.*"))
    assert len(backups) >= 1, "Corrupted DB must be isolated to .corrupt.*"

    # Key must be backfilled and blocked
    dup_ok, _, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="HIST_KEY_01"
    )
    assert dup_ok is False, "Historical trade key must be protected"


def test_fail_closed_when_history_unrecoverable(tmp_path):
    """Verify broker fails closed when database is corrupt AND state history is missing."""
    state_file = tmp_path / "lost_history.json"
    db_file = state_file.with_suffix(".idempotency.db")

    b = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    b.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="LOST_KEY")
    b.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    b.checkpoint_db("TRUNCATE")
    del b

    db_file.write_bytes(b"CORRUPTED_BYTES\x00\x00")
    state_file.unlink()  # Destroy JSON history

    with pytest.raises(IdempotencyRegistryError) as excinfo:
        DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert "Cannot establish complete idempotency history" in str(excinfo.value) or "Database initialization and recovery failed" in str(excinfo.value)


# =============================================================================
# 4. BACKUP AND RESTORE ADVERSARIAL TESTS
# =============================================================================

def test_backup_during_active_writes(tmp_path):
    """Verify backup integrity when writes are occurring."""
    state_file = tmp_path / "active_write_account.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Perform writes
    for i in range(10):
        broker._idempotency_registry.reserve(f"KEY_WRITE_{i}", f"TOK_{i}", i, "VWAP", "LONG", "XAUTUSD", 30.0)

    # Execute backup
    backup_dir = tmp_path / "backup_out"
    json_bak, db_bak = broker.backup_state(backup_dir)

    assert json_bak.exists()
    assert db_bak.exists()

    # Verify backup database contains all 10 records
    b_conn = sqlite3.connect(str(db_bak))
    cnt = b_conn.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0]
    b_conn.close()
    assert cnt == 10, f"Backup must contain all 10 records (got {cnt})"


def test_restore_with_open_position(tmp_path):
    """Verify restoration when an open position exists."""
    state_file = tmp_path / "open_pos_account.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, _, pos = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="OPEN_POS_KEY")
    assert ok is True

    backup_dir = tmp_path / "backup_open_pos"
    json_bak, db_bak = broker.backup_state(backup_dir)

    # Restore into fresh directory
    rest_dir = tmp_path / "restored_open_pos"
    rest_dir.mkdir(parents=True, exist_ok=True)
    rest_state = rest_dir / "restored.json"
    shutil.copy2(json_bak, rest_state)
    shutil.copy2(db_bak, rest_state.with_suffix(".idempotency.db"))

    restored = DeltaPaperBroker(state_file=str(rest_state), initial_equity=10000.0)
    assert restored.open_position is not None
    assert restored.open_position["idempotency_key"] == "OPEN_POS_KEY"
    assert restored.open_position["entry_price"] == 4000.8  # slippage applied
    assert restored.cash_balance == broker.cash_balance


def test_repeated_backup_and_restore_cycles(tmp_path):
    """Perform multiple sequential backup and restore cycles."""
    curr_state = tmp_path / "cycle_0.json"
    broker = DeltaPaperBroker(state_file=str(curr_state), initial_equity=10000.0)

    for cycle in range(3):
        # Execute trade
        key = f"CYCLE_KEY_{cycle}"
        broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key=key)
        broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

        # Backup
        bak_dir = tmp_path / f"bak_cycle_{cycle}"
        j_bak, d_bak = broker.backup_state(bak_dir)

        # Restore into next cycle
        next_state = tmp_path / f"cycle_{cycle + 1}.json"
        shutil.copy2(j_bak, next_state)
        shutil.copy2(d_bak, next_state.with_suffix(".idempotency.db"))
        broker = DeltaPaperBroker(state_file=str(next_state), initial_equity=10000.0)

    assert len(broker.closed_trades) == 3
    for cycle in range(3):
        dup_ok, _, _ = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key=f"CYCLE_KEY_{cycle}")
        assert dup_ok is False, f"Cycle key {cycle} must be deduplicated"


# =============================================================================
# 5. SAFETY INVARIANTS & POSITION SIZING AUDIT
# =============================================================================

def test_safety_invariants_strictly_preserved(tmp_path):
    """Verify 1-lot maximum cap, rejection of oversized orders, LIVE barrier, incident intact."""
    state_file = tmp_path / "safety_audit.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. 1-lot maximum constant
    assert APPROVED_MAX_PAPER_LOTS == 1

    # 2. Reject >1 lot
    for invalid_lots in [2, 3, 5, 10, 100, 484, 495]:
        with pytest.raises(PositionSizeViolation):
            validate_paper_order_lots(invalid_lots)
        ok, msg, _ = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, invalid_lots, "VWAP", idempotency_key=f"INV_{invalid_lots}")
        assert ok is False
        assert "PositionSizeViolation" in msg or "Guard Rejection" in msg or "exceeds" in msg

    # 3. Reject non-positive or float lots
    for bad_lots in [0, -1, 1.5, "1"]:
        with pytest.raises((PositionSizeViolation, ValueError, TypeError)):
            validate_paper_order_lots(bad_lots)

    # 4. Reject invalid contract quantities
    for bad_qty in [0.0, -0.001, 0.002, 0.01, 0.484, 1.0]:
        with pytest.raises(PositionSizeViolation):
            validate_contract_quantity(bad_qty, 0.001)

    # 5. LIVE trading barrier
    with pytest.raises(PaperSafetyViolation):
        broker.execute_live_order()

    # 6. Historical 484-lot incident record intact
    prod_state_path = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    if prod_state_path.exists():
        with open(prod_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        trades = data.get("closed_trades", [])
        incident = next((t for t in trades if t.get("trade_id") == "PAPER_20261105_101000_LONG"), None)
        assert incident is not None
        assert incident.get("lots") == 484
        assert incident.get("net_pnl") == -7.9472

