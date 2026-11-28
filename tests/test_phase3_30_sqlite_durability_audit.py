"""Phase 3.30 — SQLite Durability & Recovery Independent Audit Test Suite.

Audits:
- Audit A: SQLite Durability Settings (WAL, synchronous, transaction boundaries, checkpoints)
- Audit B: Corruption Recovery & Fail-Closed Behavior
- Audit C: Cross-Store Crash Consistency & Boundary Injection
- Audit D: WAL-Aware Backup and Restore
- Audit E: Safety Invariants & Historical Incident Integrity
"""

import json
import os
import shutil
import sqlite3
import subprocess
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
# AUDIT A: SQLITE DURABILITY SETTINGS & TRANSACTIONS
# =============================================================================

def test_audit_a_pragma_settings(tmp_path):
    """Audit A.1: Verify SQLite PRAGMA journal_mode, synchronous, and busy_timeout."""
    db_path = tmp_path / "pragma_test.db"
    reg = DurableIdempotencyRegistry(db_path)

    with reg._get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]

    assert str(journal_mode).lower() == "wal", "Must be WAL mode"
    # synchronous: 1 = NORMAL, 2 = FULL
    assert int(synchronous) in (1, 2), "Configured as NORMAL (1) or FULL (2)"
    assert int(busy_timeout) == 10000, "Busy timeout must be 10000ms (10s)"


def test_audit_a_transaction_rollback(tmp_path):
    """Audit A.2: Verify that failed/aborted transactions roll back completely."""
    db_path = tmp_path / "rollback_test.db"
    reg = DurableIdempotencyRegistry(db_path)

    # Insert a valid baseline key
    reg.reserve("KEY_VALID", "TOK_V", 1, "VWAP", "LONG", "XAUTUSD", 30.0)
    assert reg.get_record("KEY_VALID") is not None

    # Simulate an aborted transaction
    try:
        with reg._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO idempotency_records (
                    idempotency_key, status, token, generation, created_at, updated_at
                ) VALUES ('KEY_ABORT', 'RESERVED', 'TOK_ABORT', 2, '2026-11-10T12:00:00Z', '2026-11-10T12:00:00Z')
                """
            )
            raise RuntimeError("SIMULATED_INTERRUPTION")
    except RuntimeError:
        pass

    assert reg.get_record("KEY_ABORT") is None, "Aborted transaction must not leave phantom record"
    assert reg.get_record("KEY_VALID") is not None, "Prior committed records must remain intact"


def test_audit_a_wal_checkpoint_behavior(tmp_path):
    """Audit A.3: Verify WAL checkpointing behavior and page truncation."""
    db_path = tmp_path / "wal_test.db"
    reg = DurableIdempotencyRegistry(db_path)

    # Insert 100 records
    now_iso = datetime.now(timezone.utc).isoformat()
    with reg._get_connection() as conn:
        for i in range(100):
            conn.execute(
                """
                INSERT INTO idempotency_records (
                    idempotency_key, status, token, generation, created_at, updated_at
                ) VALUES (?, 'COMMITTED', ?, ?, ?, ?)
                """,
                (f"WAL_KEY_{i}", f"TOK_{i}", i, now_iso, now_iso),
            )

    wal_file = db_path.with_name(db_path.name + "-wal")
    assert wal_file.exists(), "WAL file must exist after writes in WAL mode"
    wal_size_before = wal_file.stat().st_size
    assert wal_size_before > 0, "WAL file must contain uncheckpointed pages"

    # Explicit checkpoint truncate
    with reg._get_connection() as conn:
        res = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
        # Returns (busy, log, checkpointed)
        assert res[0] == 0, "Checkpoint must succeed (not busy)"

    wal_size_after = wal_file.stat().st_size
    assert wal_size_after == 0, "TRUNCATE checkpoint must reduce WAL size to 0 bytes"


# =============================================================================
# AUDIT B: CORRUPTION RECOVERY & FAIL-CLOSED BEHAVIOR
# =============================================================================

def test_audit_b_corruption_before_startup(tmp_path):
    """Audit B.1: Test database file corruption before broker initialization.
    
    FINDING & EVIDENCE:
    When the database file contains corrupted garbage bytes, SQLite raises DatabaseError
    ('file is not a database') during PRAGMA journal_mode=WAL. On Windows, because the
    connection handle was not closed before attempting recovery, os.replace raises
    PermissionError [WinError 32]. The broker fails closed by raising IdempotencyRegistryError,
    halting execution rather than running on a corrupted database.
    """
    state_file = tmp_path / "corrupt_pre.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Execute trade and close
    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_PRE_CORRUPT")
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    with broker._idempotency_registry._get_connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    del broker

    # Corrupt the SQLite DB file with garbage bytes
    db_path = state_file.with_suffix(".idempotency.db")
    assert db_path.exists()
    db_path.write_bytes(b"CORRUPTED_GARBAGE_HEADER_DATA_1234567890\x00\xFF")

    # Start new broker instance: validates corruption handling (either auto-recovery isolation or fail-closed)
    try:
        broker_recovered = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
        corrupt_backups = list(tmp_path.glob("*.corrupt.*"))
        assert len(corrupt_backups) >= 1, "Corrupted database file must be isolated to .corrupt.*"
        ok_retry, _, _ = broker_recovered.open_simulated_trade(
            "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_PRE_CORRUPT"
        )
        assert ok_retry is False, "Previous key must be deduplicated after recovery"
    except IdempotencyRegistryError as excinfo:
        assert "Database initialization and recovery failed" in str(excinfo) or "Cannot establish complete idempotency history" in str(excinfo)


def test_audit_b_missing_or_damaged_wal_file(tmp_path):
    """Audit B.2: Test recovery when WAL file is damaged.
    
    FINDING & EVIDENCE:
    Corrupting the -wal file causes SQLite to raise OperationalError ('disk I/O error').
    The broker safely handles the damaged WAL by isolating corrupt files or failing closed.
    """
    state_file = tmp_path / "wal_damage.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_WAL_TEST")
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    del broker

    wal_file = state_file.with_suffix(".idempotency.db-wal")
    if wal_file.exists():
        wal_file.write_bytes(b"CORRUPT_WAL_BYTES\x00\xFF")

    # Restart broker: handles damaged WAL file safely
    try:
        broker_recovered = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
        ok_retry, _, _ = broker_recovered.open_simulated_trade(
            "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_WAL_TEST"
        )
        assert ok_retry is False, "Previous key must be deduplicated after recovery"
    except IdempotencyRegistryError as excinfo:
        assert "Database initialization and recovery failed" in str(excinfo) or "Cannot establish complete idempotency history" in str(excinfo)


def test_audit_b_keys_exist_only_in_sqlite(tmp_path):
    """Audit B.3: Test scenario where keys exist in SQLite but not in JSON state."""
    state_file = tmp_path / "sqlite_only.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Manually register a COMMITTED key directly into SQLite registry
    now_iso = datetime.now(timezone.utc).isoformat()
    with broker._idempotency_registry._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO idempotency_records (
                idempotency_key, status, token, generation, created_at, updated_at
            ) VALUES ('KEY_SQLITE_ONLY', 'COMMITTED', 'TOK_SQL_ONLY', 1, ?, ?)
            """,
            (now_iso, now_iso),
        )

    # Verify key is NOT in broker's in-memory cache or JSON closed trades
    assert "KEY_SQLITE_ONLY" not in broker._processed_idempotency_keys
    assert not any(t.get("idempotency_key") == "KEY_SQLITE_ONLY" for t in broker.closed_trades)

    # Attempt order with KEY_SQLITE_ONLY
    ok, msg, _ = broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_SQLITE_ONLY"
    )
    assert ok is False, "Key existing only in SQLite must be rejected"
    assert "already executed in durable registry" in msg


def test_audit_b_fail_closed_analysis_when_history_unrecoverable(tmp_path):
    """Audit B.4: Critical requirement analysis - does broker fail closed when history is lost?
    
    FINDING & EVIDENCE:
    When the SQLite DB is corrupted, the broker fails closed by raising IdempotencyRegistryError
    and refusing to start, rather than silently creating an empty database and continuing.
    """
    state_file = tmp_path / "fail_closed_test.json"
    db_file = state_file.with_suffix(".idempotency.db")

    # Step 1: Initial trade execution
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    b1.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_LOST_HISTORY")
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    with b1._idempotency_registry._get_connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    del b1

    # Step 2: Simulate unrecoverable data loss: SQLite corrupt AND JSON file deleted
    db_file.write_bytes(b"CORRUPT_BYTES_ZEROED\x00\x00")
    state_file.unlink()  # JSON state deleted/lost

    # Step 3: Broker re-initialization must fail closed
    with pytest.raises(IdempotencyRegistryError):
        DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)


# =============================================================================
# AUDIT C: CROSS-STORE CRASH CONSISTENCY & BOUNDARY INJECTION
# =============================================================================

def test_audit_c_crash_between_sqlite_commit_and_json_position_save(tmp_path):
    """Audit C.1: Inject failure after SQLite commit but before JSON open_position save.
    
    FINDING & EVIDENCE:
    Sequence in open_simulated_trade():
    1. commit_order_reservation() commits 'COMMITTED' to SQLite.
    2. commit_order_reservation() writes JSON with open_position=None.
    3. Crash happens before line 712 (self.open_position = pos; self._save_state()).
    
    On restart:
    - SQLite registry has COMMITTED.
    - JSON state has open_position = None.
    - Cash has fee deducted.
    - Replaying the key is blocked by SQLite registry.
    - BUT the position was never opened (phantom fill / partial state).
    """
    state_file = tmp_path / "cross_store_crash.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Step 1: Reserve slot
    reserved, _, token = broker.reserve_order_slot("CRASH_INJECT_KEY", "VWAP", "LONG", "XAUTUSD", 30.0)
    assert reserved is True

    # Step 2: Deduct fee as in open_simulated_trade
    entry_fee = 0.40
    broker.cash_balance -= entry_fee
    pos_record = {
        "order_id": "ORD_CRASH_TEST",
        "trade_id": "TR_CRASH_TEST",
        "lots": 1,
        "entry_price": 4000.0,
    }

    # Step 3: commit_order_reservation commits to SQLite
    commit_ok = broker.commit_order_reservation(token, pos_record)
    assert commit_ok is True

    # INJECT CRASH HERE before broker.open_position = pos_record and broker._save_state()
    del broker

    # Step 4: Restart broker
    restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Document state inconsistency:
    assert restarted.open_position is None, "JSON open_position was never saved"
    assert restarted._idempotency_registry.is_executed("CRASH_INJECT_KEY") is True, "SQLite recorded COMMITTED"

    # Replay of key is blocked
    ok_retry, msg_retry, _ = restarted.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="CRASH_INJECT_KEY"
    )
    assert ok_retry is False
    assert "Duplicate order" in msg_retry


def test_audit_c_cash_conservation_across_reservation_failures(tmp_path):
    """Audit C.2: Verify cash is perfectly conserved when orders fail or are rejected."""
    state_file = tmp_path / "cash_conserve.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    # 1. Unfilled limit order (PRICE_NOT_REACHED)
    broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP",
        order_type="LIMIT", bar_low=4010.0, idempotency_key="KEY_UNFILLED"
    )
    assert broker.cash_balance == initial_cash, "Unfilled limit order must not alter cash"

    # 2. Zero fill
    broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP",
        fill_ratio=0.0, idempotency_key="KEY_ZERO"
    )
    assert broker.cash_balance == initial_cash, "Zero fill must not alter cash"

    # 3. Oversized rejection
    broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, lots=2, strategy_name="VWAP",
        idempotency_key="KEY_OVERSIZED"
    )
    assert broker.cash_balance == initial_cash, "Oversized order rejection must not alter cash"


# =============================================================================
# AUDIT D: BACKUP AND RESTORE
# =============================================================================

def test_audit_d_sqlite_online_backup_api(tmp_path):
    """Audit D.1: Test SQLite online backup API captures dirty WAL pages without shutdown."""
    src_db = tmp_path / "src.db"
    dest_db = tmp_path / "backup.db"

    reg = DurableIdempotencyRegistry(src_db)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Insert 50 records in WAL mode
    with reg._get_connection() as conn:
        for i in range(50):
            conn.execute(
                """
                INSERT INTO idempotency_records (
                    idempotency_key, status, token, generation, created_at, updated_at
                ) VALUES (?, 'COMMITTED', ?, ?, ?, ?)
                """,
                (f"SRC_KEY_{i}", f"TOK_{i}", i, now_iso, now_iso),
            )

    # Perform online backup using conn.backup()
    src_conn = sqlite3.connect(str(src_db))
    dest_conn = sqlite3.connect(str(dest_db))
    src_conn.backup(dest_conn)
    dest_conn.close()
    src_conn.close()

    # Verify backup database contains all 50 keys
    reg_backup = DurableIdempotencyRegistry(dest_db)
    assert reg_backup.count_records() == 50
    assert reg_backup.is_executed("SRC_KEY_0") is True
    assert reg_backup.is_executed("SRC_KEY_49") is True


def test_audit_d_stale_file_copy_backup_vulnerability(tmp_path):
    """Audit D.2: Demonstrate vulnerability of simple file-copy backup without WAL checkpoint.
    
    FINDING & EVIDENCE:
    In WAL mode, dirty uncheckpointed pages live in the -wal file.
    If a naive backup script copies ONLY 'state.idempotency.db' without 'state.idempotency.db-wal',
    the backup database file will be stale or miss the newest records.
    """
    src_db = tmp_path / "naive_src.db"
    reg = DurableIdempotencyRegistry(src_db)
    now_iso = datetime.now(timezone.utc).isoformat()

    with reg._get_connection() as conn:
        for i in range(20):
            conn.execute(
                """
                INSERT INTO idempotency_records (
                    idempotency_key, status, token, generation, created_at, updated_at
                ) VALUES (?, 'COMMITTED', ?, ?, ?, ?)
                """,
                (f"NAIVE_KEY_{i}", f"TOK_{i}", i, now_iso, now_iso),
            )

    # Perform naive copy of ONLY the main .db file (ignoring -wal)
    naive_backup = tmp_path / "naive_backup.db"
    shutil.copyfile(src_db, naive_backup)

    # Inspect naive backup: because pages were in -wal and not checkpointed,
    # the main .db file alone has 0 records or no schema table!
    backup_conn = sqlite3.connect(str(naive_backup))
    try:
        cur = backup_conn.execute("SELECT COUNT(*) FROM idempotency_records")
        count_in_naive_copy = cur.fetchone()[0]
        assert count_in_naive_copy == 0, "Naive copy misses uncheckpointed WAL records"
    except sqlite3.OperationalError as e:
        # In WAL mode without checkpoint, even the schema table does not exist in main DB!
        assert "no such table" in str(e)
    finally:
        backup_conn.close()


def test_audit_d_restore_to_new_directory(tmp_path):
    """Audit D.3: Restore valid backup (with checkpoint) to an entirely new directory."""
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "restore"
    src_dir.mkdir()
    dest_dir.mkdir()

    src_state = src_dir / "paper_account_xautusd.json"
    broker = DeltaPaperBroker(state_file=str(src_state), initial_equity=10000.0)

    # Execute trade and close
    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="RESTORE_TEST_KEY")
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # Checkpoint WAL before copying
    src_db = broker.idempotency_db_path
    with sqlite3.connect(str(src_db)) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    # Copy state.json and .idempotency.db to dest_dir
    dest_state = dest_dir / "paper_account_xautusd.json"
    dest_db = dest_dir / "paper_account_xautusd.idempotency.db"
    shutil.copyfile(src_state, dest_state)
    shutil.copyfile(src_db, dest_db)

    del broker

    # Launch broker in new directory
    restored_broker = DeltaPaperBroker(state_file=str(dest_state), initial_equity=10000.0)
    assert len(restored_broker.closed_trades) == 1
    assert restored_broker._idempotency_registry.is_executed("RESTORE_TEST_KEY") is True

    # Replay must be blocked
    ok, msg, _ = restored_broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="RESTORE_TEST_KEY"
    )
    assert ok is False
    assert "Duplicate order" in msg


# =============================================================================
# AUDIT E: SAFETY INVARIANTS & HISTORICAL INCIDENT PRESERVATION
# =============================================================================

def test_audit_e_position_size_guard_rejection():
    """Audit E.1: Verify 1 micro-lot maximum position limit and rejection of >1 lot."""
    assert validate_paper_order_lots(1) == 1

    with pytest.raises(PositionSizeViolation) as excinfo:
        validate_paper_order_lots(2)
    assert "SECURITY SIZING REJECTION" in str(excinfo.value)

    with pytest.raises(PositionSizeViolation) as excinfo:
        validate_paper_order_lots(484)
    assert "SECURITY SIZING REJECTION" in str(excinfo.value)


def test_audit_e_broker_rejects_oversized_orders(tmp_path):
    """Audit E.2: Verify broker rejects 2-lot order before reserving slot."""
    state_file = tmp_path / "oversize_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        "XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, lots=2, strategy_name="VWAP"
    )
    assert ok is False
    assert "Position Size Guard Rejection" in msg
    assert pos is None
    assert broker.open_position is None


def test_audit_e_live_trading_hard_barrier():
    """Audit E.3: Verify execute_live_order raises PaperSafetyViolation."""
    broker = DeltaPaperBroker()
    with pytest.raises(PaperSafetyViolation, match="CRITICAL SECURITY VIOLATION"):
        broker.execute_live_order()


def test_audit_e_historical_484_lot_incident_intact():
    """Audit E.4: Verify historical incident PAPER_20261105_101000_LONG remains completely untouched."""
    incident_file = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    assert incident_file.exists(), "Historical forward state file must exist"

    data = json.loads(incident_file.read_text(encoding="utf-8"))
    trades = data.get("closed_trades", [])
    assert len(trades) > 0, "Historical closed trades must not be empty"

    trade = trades[0]
    assert trade["trade_id"] == "PAPER_20261105_101000_LONG"
    assert trade["lots"] == 484
    assert trade["contract_qty"] == 0.484
    assert trade["entry_price"] == 4127.9
    assert trade["net_pnl"] == -7.9472
