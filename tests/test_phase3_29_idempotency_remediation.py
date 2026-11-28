"""Phase 3.29 — Permanent Idempotency Remediation Test Suite.

Verifies:
1. Duplicate key in the same process.
2. Duplicate key after restart.
3. Replay after 1,005 unique keys (survives L1 FIFO cache eviction).
4. Replay after 10,000 unique keys using high-volume practical fixture.
5. Duplicate key after the corresponding trade is closed.
6. Two processes submit the same key simultaneously.
7. Crash during idempotency persistence (ACID rollback integrity).
8. Crash after execution-state persistence but before acknowledgement.
9. Corrupt or unavailable idempotency database (safe fallback & recovery).
10. Recovery without allowing duplicate execution.
11. Existing one-micro-lot guard remains unchanged.
12. Historical incident remains unchanged.
"""

import json
import os
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
# TEST 1: DUPLICATE KEY IN THE SAME PROCESS
# =============================================================================

def test_1_duplicate_key_same_process(tmp_path):
    """Test 1: Submitting duplicate idempotency key in same process is rejected."""
    state_file = tmp_path / "test1_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. Execute initial trade with KEY_SAME_PROC
    ok1, msg1, pos1 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_SAME_PROC",
    )
    assert ok1 is True
    assert pos1 is not None

    # 2. Attempt second trade with identical key
    ok2, msg2, pos2 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_SAME_PROC",
    )
    assert ok2 is False
    assert "Duplicate order" in msg2
    assert pos2 is None


# =============================================================================
# TEST 2: DUPLICATE KEY AFTER RESTART
# =============================================================================

def test_2_duplicate_key_after_restart(tmp_path):
    """Test 2: Submitting duplicate key after process termination/restart is rejected."""
    state_file = tmp_path / "test2_state.json"
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Execute trade and close it
    ok, _, _ = b1.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_RESTART_TEST",
    )
    assert ok is True
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert b1.open_position is None

    # Terminate b1 instance
    del b1

    # Initialize b2 from disk
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert b2.open_position is None

    # Attempt re-execution with KEY_RESTART_TEST
    ok_retry, msg_retry, _ = b2.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_RESTART_TEST",
    )
    assert ok_retry is False
    assert "Duplicate order" in msg_retry


# =============================================================================
# TEST 3: REPLAY AFTER 1,005 UNIQUE KEYS (L1 CACHE PRUNING RESILIENCE)
# =============================================================================

def test_3_replay_after_1005_keys_pruning(tmp_path):
    """Test 3: Confirms key pruned from 1,000-entry in-memory cache is STILL rejected by durable registry."""
    state_file = tmp_path / "test3_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=100000.0)

    # 1. Execute initial trade with KEY_TARGET_1005
    ok, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_TARGET_1005",
    )
    assert ok is True
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert broker.open_position is None
    assert "KEY_TARGET_1005" in broker._processed_idempotency_keys

    # Make KEY_TARGET_1005 have an older timestamp so subsequent 1,005 keys sort newer
    broker._processed_idempotency_keys["KEY_TARGET_1005"]["timestamp"] = "2026-11-01T00:00:00Z"

    # 2. Simulate 1,005 subsequent trades triggering pruning of KEY_TARGET_1005
    for i in range(1005):
        k = f"KEY_FILL_{i:04d}"
        broker._processed_idempotency_keys[k] = {
            "token": f"TOK_{i}",
            "order_id": f"ORD_{i}",
            "trade_id": f"TR_{i}",
            "status": "FILLED",
            "timestamp": f"2026-11-02T12:{i//60:02d}:{i%60:02d}Z",
        }
    broker._save_state()

    # Trigger commit of a key to prune in-memory cache down to 1000
    broker.reserve_order_slot("KEY_FORCE_PRUNE", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    tok = broker._pending_reservation["token"]
    broker.commit_order_reservation(tok, {"order_id": "ORD_FP", "trade_id": "TR_FP"})
    broker._save_state()

    # Verify KEY_TARGET_1005 is no longer in in-memory cache
    assert "KEY_TARGET_1005" not in broker._processed_idempotency_keys

    # 3. Process restart
    del broker
    broker_restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=100000.0)
    assert "KEY_TARGET_1005" not in broker_restarted._processed_idempotency_keys

    # 4. Attempt replay of KEY_TARGET_1005
    ok_replay, msg_replay, _ = broker_restarted.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_TARGET_1005",
    )

    # REMEDIATION VERIFICATION: Replay MUST be rejected by durable registry
    assert ok_replay is False, "Pruned key must be deterministically rejected by durable registry"
    assert "already executed in durable registry" in msg_replay


# =============================================================================
# TEST 4: REPLAY AFTER 10,000 UNIQUE KEYS (HIGH-VOLUME STRESS FIXTURE)
# =============================================================================

def test_4_replay_after_10000_unique_keys(tmp_path):
    """Test 4: High-volume stress test verifying rejection after 10,000 unique historical keys."""
    state_file = tmp_path / "test4_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=100000.0)

    # 1. Execute initial trade with KEY_10K_TARGET
    ok, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="KEY_10K_TARGET",
    )
    assert ok is True
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # 2. Batch-insert 10,000 unique records directly into SQLite registry
    db_path = broker.idempotency_db_path
    reg = DurableIdempotencyRegistry(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    records = [
        (
            f"STRESS_KEY_{i:05d}",
            "COMMITTED",
            f"TOK_{i}",
            i,
            f"ORD_{i}",
            f"TR_{i}",
            now_iso,
            now_iso,
        )
        for i in range(10000)
    ]
    with reg._get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO idempotency_records (
                idempotency_key, status, token, generation, order_id, trade_id,
                created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            records,
        )

    # Total records in durable registry is > 10,000
    assert reg.count_records() >= 10001

    # 3. Restart broker with clean in-memory cache
    del broker
    broker_reloaded = DeltaPaperBroker(state_file=str(state_file), initial_equity=100000.0)

    # 4. Attempt to replay KEY_10K_TARGET
    ok_replay, msg_replay, _ = broker_reloaded.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="KEY_10K_TARGET",
    )
    assert ok_replay is False
    assert "Duplicate order" in msg_replay

    # 5. Also verify any of the 10,000 stress keys is rejected by durable registry (not present in L1 cache)
    ok_stress, msg_stress, _ = broker_reloaded.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="STRESS_KEY_05000",
    )
    assert ok_stress is False
    assert "already executed in durable registry" in msg_stress


# =============================================================================
# TEST 5: DUPLICATE KEY AFTER CORRESPONDING TRADE IS CLOSED
# =============================================================================

def test_5_duplicate_key_after_trade_closed(tmp_path):
    """Test 5: Verify duplicate key is rejected after position is closed and flat."""
    state_file = tmp_path / "test5_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok1, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry_price=4000.0,
        stop_loss=4020.0,
        take_profit=3960.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="KEY_CLOSED_LIFECYCLE",
    )
    assert ok1 is True
    broker.close_simulated_trade(exit_price=3960.0, exit_reason="TP")
    assert broker.open_position is None

    # Account is completely flat, but key must be rejected permanently
    ok2, msg2, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="SHORT",
        entry_price=4000.0,
        stop_loss=4020.0,
        take_profit=3960.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="KEY_CLOSED_LIFECYCLE",
    )
    assert ok2 is False
    assert "Duplicate order" in msg2


# =============================================================================
# TEST 6: TWO PROCESSES SUBMIT SAME KEY SIMULTANEOUSLY
# =============================================================================

def test_6_concurrent_processes_same_key(tmp_path):
    """Test 6: Two separate OS processes race with the exact same idempotency key."""
    state_file = str(tmp_path / "test6_state.json")
    b_init = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    del b_init

    worker_script = f"""
import sys, json
from services.delta_paper_broker import DeltaPaperBroker

state_file = {repr(state_file)}
key = sys.argv[1]
broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
ok, msg, pos = broker.open_simulated_trade(
    symbol='XAUTUSD',
    direction='LONG',
    entry_price=4000.0,
    stop_loss=3980.0,
    take_profit=4040.0,
    lots=1,
    strategy_name='VWAP',
    idempotency_key=key,
)
print(json.dumps({{'ok': ok, 'msg': msg}}))
"""
    p1 = subprocess.Popen([sys.executable, "-c", worker_script, "SHARED_RACE_KEY"], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))
    p2 = subprocess.Popen([sys.executable, "-c", worker_script, "SHARED_RACE_KEY"], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))

    out1, _ = p1.communicate(timeout=10)
    out2, _ = p2.communicate(timeout=10)

    res1 = json.loads(out1.strip().splitlines()[-1])
    res2 = json.loads(out2.strip().splitlines()[-1])

    # Exactly one process must succeed, and one must be rejected
    results = [res1["ok"], res2["ok"]]
    assert results.count(True) == 1, f"Expected exactly 1 success: res1={res1}, res2={res2}"
    assert results.count(False) == 1, f"Expected exactly 1 failure: res1={res1}, res2={res2}"


# =============================================================================
# TEST 7: CRASH DURING IDEMPOTENCY PERSISTENCE (ACID ROLLBACK)
# =============================================================================

def test_7_crash_during_idempotency_persistence(tmp_path):
    """Test 7: Uncommitted SQLite transactions roll back cleanly without phantom state."""
    db_path = tmp_path / "acid_test.db"
    reg = DurableIdempotencyRegistry(db_path)

    # Simulate an aborted transaction
    try:
        with reg._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO idempotency_records (
                    idempotency_key, status, token, generation, created_at, updated_at
                ) VALUES ('ABORTED_KEY', 'RESERVED', 'TOK_ABORT', 1, '2026-11-10T12:00:00Z', '2026-11-10T12:00:00Z')
                """
            )
            # Crash simulation: raise unhandled exception before commit
            raise RuntimeError("SIMULATED_PROCESS_CRASH_DURING_WRITE")
    except RuntimeError:
        pass

    # Verify aborted key was rolled back
    assert reg.get_record("ABORTED_KEY") is None
    assert reg.is_executed("ABORTED_KEY") is False


# =============================================================================
# TEST 8: CRASH AFTER PERSISTENCE BUT BEFORE ACKNOWLEDGEMENT
# =============================================================================

def test_8_crash_after_persistence_before_ack(tmp_path):
    """Test 8: If crash occurs right after persistence, retrying the key is blocked."""
    state_file = tmp_path / "test8_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Reserve and commit order
    broker.reserve_order_slot("CRASH_ACK_KEY", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    tok = broker._pending_reservation["token"]
    broker.commit_order_reservation(tok, {"order_id": "ORD_CRASH_ACK", "trade_id": "TR_CRASH_ACK"})

    # Caller crashes before receiving order response.
    # Simulate restart:
    del broker
    broker_restarted = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Client retries with exact same key
    ok, msg, _ = broker_restarted.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="CRASH_ACK_KEY",
    )
    assert ok is False
    assert "Duplicate order" in msg


# =============================================================================
# TEST 9: CORRUPT OR UNAVAILABLE IDEMPOTENCY DATABASE
# =============================================================================

def test_9_corrupt_database_recovery(tmp_path):
    """Test 9: Corrupted database triggers auto-recovery without permitting duplicate execution."""
    state_file = tmp_path / "test9_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. Execute initial trade
    ok, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="KEY_BEFORE_CORRUPT",
    )
    assert ok is True
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    del broker

    # 2. Corrupt SQLite DB by overwriting with garbage bytes
    db_file = state_file.with_suffix(".idempotency.db")
    assert db_file.exists()
    db_file.write_bytes(b"CORRUPTED_GARBAGE_HEADER_DATA_1234567890\x00\xFF")

    # 3. Re-initialize broker: auto-recovery should isolate corrupted file and backfill from closed_trades
    broker_recovered = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 4. Attempt re-execution of KEY_BEFORE_CORRUPT
    ok_replay, msg_replay, _ = broker_recovered.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="KEY_BEFORE_CORRUPT",
    )

    # Defense-in-depth: Re-execution is STILL blocked!
    assert ok_replay is False
    assert "Duplicate order" in msg_replay


# =============================================================================
# TEST 10: RECOVERY WITHOUT ALLOWING DUPLICATE EXECUTION
# =============================================================================

def test_10_recovery_without_duplicate_execution(tmp_path):
    """Test 10: Complete lifecycle restart recovery test preventing duplicate fills."""
    state_file = tmp_path / "test10_state.json"
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, _, _ = b1.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP",
        idempotency_key="KEY_LIFECYCLE_TEST",
    )
    assert ok is True
    assert len(b1.closed_trades) == 0
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert len(b1.closed_trades) == 1

    # Simulate abrupt hard restart
    del b1

    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert len(b2.closed_trades) == 1

    # Try 5 repeated attempts to re-execute with KEY_LIFECYCLE_TEST
    for _ in range(5):
        ok_dup, msg_dup, _ = b2.open_simulated_trade(
            symbol="XAUTUSD",
            direction="LONG",
            entry_price=4000.0,
            stop_loss=3980.0,
            take_profit=4040.0,
            lots=1,
            strategy_name="VWAP",
            idempotency_key="KEY_LIFECYCLE_TEST",
        )
        assert ok_dup is False
        assert "Duplicate order" in msg_dup

    # Trade ledger remains strictly 1 trade
    assert len(b2.closed_trades) == 1


# =============================================================================
# TEST 11: POSITION-SIZING SAFETY GUARD INVARIANTS
# =============================================================================

def test_11_position_sizing_guard_unchanged():
    """Test 11: Position sizing safety invariants remain 100% impenetrable."""
    # 1 micro-lot permitted
    assert validate_paper_order_lots(1) == 1

    # 2 lots rejected deterministically
    with pytest.raises(PositionSizeViolation) as excinfo:
        validate_paper_order_lots(2)
    assert "SECURITY SIZING REJECTION" in str(excinfo.value)

    # 484 lots rejected deterministically
    with pytest.raises(PositionSizeViolation) as excinfo:
        validate_paper_order_lots(484)
    assert "SECURITY SIZING REJECTION" in str(excinfo.value)


# =============================================================================
# TEST 12: HISTORICAL INCIDENT RECORD INTACT
# =============================================================================

def test_12_historical_incident_record_intact():
    """Test 12: Historical incident PAPER_20261105_101000_LONG remains completely untouched."""
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
