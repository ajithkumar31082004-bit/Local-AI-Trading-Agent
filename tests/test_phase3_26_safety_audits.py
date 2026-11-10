"""Phase 3.26 — Three Additional Trading Safety Audits (Local Testing Only).

Test Matrix:
Audit 1: Order Reservation TTL Expiration & Fencing (Token generation, stale commit, preemption)
Audit 2: Idempotency Persistence & Crash Recovery (State reload, restart survival, atomicity)
Audit 3: Multi-Process Concurrency (OS process lock limits) & Feed Stream Discrepancy Analysis
"""

import json
import math
import multiprocessing
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.orb_settings import ORBSettings
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from scripts.stage1_readonly_market_monitor import Stage1MarketObserver


# =============================================================================
# AUDIT 1: ORDER RESERVATION TTL EXPIRATION AND FENCING
# =============================================================================

def test_audit1_normal_reservation_and_commit(tmp_path):
    """Scenario 1: Worker reserves slot and commits within valid TTL."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    reserved, msg, token = broker.reserve_order_slot(
        idempotency_key="IDEMP_A",
        strategy_name="VWAP_EMA",
        direction="LONG",
        symbol="XAUTUSD",
        ttl_seconds=30.0,
    )
    assert reserved is True
    assert token is not None
    assert token.startswith("RES_")

    # Worker commits
    order_record = {"order_id": "ORD_123", "trade_id": "TRADE_123"}
    committed = broker.commit_order_reservation(token, order_record)
    assert committed is True
    assert broker._pending_reservation is None
    assert "IDEMP_A" in broker._processed_idempotency_keys


def test_audit1_stale_worker_preempted_by_worker_b(tmp_path):
    """Scenario 2: Worker A reaches TTL; Worker B preempts expired slot; Worker A commit fails."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Worker A reserves with very short TTL (0.1s)
    reserved_a, _, token_a = broker.reserve_order_slot(
        idempotency_key="KEY_A",
        strategy_name="VWAP_EMA",
        direction="LONG",
        symbol="XAUTUSD",
        ttl_seconds=0.1,
    )
    assert reserved_a is True

    # Delayed execution: Worker A takes longer than 0.1s
    time.sleep(0.15)

    # Note on broker implementation defect:
    # In delta_paper_broker.py, reserve_order_slot() does not store the lease's ttl_seconds
    # in _pending_reservation, so the caller must pass the threshold to clear expired slots.
    # When cleared at or below the expired threshold (0.1s):
    reserved_b, _, token_b = broker.reserve_order_slot(
        idempotency_key="KEY_B",
        strategy_name="ORB",
        direction="SHORT",
        symbol="XAUTUSD",
        ttl_seconds=0.1,
    )
    assert reserved_b is True
    assert token_b != token_a

    # Worker A now attempts to commit using expired/preempted token_a
    committed_a = broker.commit_order_reservation(token_a, {"order_id": "ORD_A"})
    assert committed_a is False, "Vulnerability: Stale Worker A must NOT commit when preempted by Worker B"

    # Worker B can still commit safely
    committed_b = broker.commit_order_reservation(token_b, {"order_id": "ORD_B"})
    assert committed_b is True


def test_audit1_ttl_parameter_coupling_defect(tmp_path):
    """Scenario 2b: Demonstrate TTL Parameter Coupling Defect in reserve_order_slot.
    
    FINDING:
    reserve_order_slot(..., ttl_seconds) evaluates:
        (now_epoch - res_time) < ttl_seconds
    where `ttl_seconds` is the argument supplied by the *incoming* caller, NOT the
    TTL originally requested by the *existing* reservation!
    
    Consequences:
    1. If Worker A reserved for 0.1s, Worker B requesting 30s is blocked for 30s (cannot preempt).
    2. If Worker A reserved for 60s, Worker B requesting 5s preempts Worker A prematurely after 5s!
    """
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Worker A requests 0.1s TTL
    reserved_a, _, _ = broker.reserve_order_slot("KEY_SHORT_A", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.1)
    assert reserved_a is True

    time.sleep(0.15)  # Worker A's 0.1s lease is now expired

    # Worker B arrives with standard 30s TTL.
    # Defect: Worker B is blocked because broker checks 0.15s against Worker B's 30s!
    reserved_b, msg_b, _ = broker.reserve_order_slot("KEY_B", "ORB", "SHORT", "XAUTUSD", ttl_seconds=30.0)
    assert reserved_b is False, "Documents defect: Worker B cannot preempt expired Worker A due to parameter coupling"
    assert "currently reserved" in msg_b


def test_audit1_uncontended_expired_lease_commit_defect(tmp_path):
    """Scenario 3: Audit whether commit_order_reservation enforces TTL when uncontended.
    
    FINDING: In current delta_paper_broker.py, commit_order_reservation() does NOT check
    whether the reservation has expired past its TTL if no other worker has called
    reserve_order_slot() to clear it!
    """
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    reserved, _, token = broker.reserve_order_slot(
        idempotency_key="KEY_SLOW",
        strategy_name="VWAP_EMA",
        direction="LONG",
        symbol="XAUTUSD",
        ttl_seconds=0.1,
    )
    assert reserved is True

    # Worker takes 0.2s (lease expired)
    time.sleep(0.2)

    # In current implementation, commit succeeds because commit_order_reservation
    # only checks token equality, NOT lease expiration!
    committed = broker.commit_order_reservation(token, {"order_id": "ORD_SLOW"})
    
    # Audit verification: This confirms the lack of TTL check on commit path
    has_commit_ttl_guard = False
    now_epoch = datetime.now(timezone.utc).timestamp()
    res_time = float(broker._pending_reservation.get("timestamp_epoch", 0.0)) if broker._pending_reservation else 0.0
    # In current broker: committed is True because it lacked TTL check
    assert committed is True  # Documents existing behavior / vulnerability


def test_audit1_release_after_exception(tmp_path):
    """Scenario 4: Test release after execution exception."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    reserved, _, token = broker.reserve_order_slot("KEY_EXC", "VWAP", "LONG", "XAUTUSD", 30.0)
    assert reserved is True

    # Simulate exception handling
    try:
        raise ValueError("Simulated unexpected order execution crash")
    except Exception:
        released = broker.release_order_reservation(token, reason="CRASH_CLEANUP")
        assert released is True

    assert broker._pending_reservation is None

    # Next worker can immediately obtain slot
    res2, _, _ = broker.reserve_order_slot("KEY_NEXT", "VWAP", "LONG", "XAUTUSD", 30.0)
    assert res2 is True


# =============================================================================
# AUDIT 2: IDEMPOTENCY PERSISTENCE AND CRASH RECOVERY
# =============================================================================

def test_audit2_in_memory_duplicate_rejection(tmp_path):
    """Audit 2.1: In-memory idempotency rejects duplicate within same process."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. Execute first trade with key
    ok1, _, pos1 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="IDEMP_TEST_1",
    )
    assert ok1 is True

    # Close trade so position is flat
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert broker.open_position is None

    # 2. Re-submit same idempotency key in same running process
    ok2, msg2, pos2 = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="IDEMP_TEST_1",
    )
    assert ok2 is False
    assert "Duplicate order" in msg2


def test_audit2_idempotency_lost_across_restart_vulnerability(tmp_path):
    """Audit 2.2: Verify whether idempotency keys survive process restarts.
    
    FINDING: In current delta_paper_broker.py:
    _save_state() does NOT save _processed_idempotency_keys.
    _load_state() does NOT load _processed_idempotency_keys.
    When a position is closed and the process restarts, the idempotency cache is wiped!
    A replayed signal with the same idempotency key executes as a DUPLICATE TRADE.
    """
    state_file = tmp_path / "broker_state.json"
    
    # Process 1: Open and close trade with KEY_REPLAY
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    ok1, _, _ = b1.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_REPLAY",
    )
    assert ok1 is True
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert b1.open_position is None
    assert "KEY_REPLAY" in b1._processed_idempotency_keys

    # Simulate Process Restart (Process 2 loads persisted state from disk)
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    
    # Inspect b2 in-memory idempotency cache:
    # VULNERABILITY CONFIRMATION: _processed_idempotency_keys is EMPTY on restart!
    is_idempotency_cached = "KEY_REPLAY" in b2._processed_idempotency_keys
    assert is_idempotency_cached is False, "Documents defect: idempotency cache is lost on restart"

    # Therefore, resubmitting KEY_REPLAY opens a duplicate trade!
    ok2, _, _ = b2.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_REPLAY",
    )
    assert ok2 is True, "Confirmed vulnerability: Duplicate order executed after restart!"


def test_audit2_state_file_lacks_idempotency_field(tmp_path):
    """Audit 2.3: Verify state JSON content on disk."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="VWAP_EMA",
        idempotency_key="KEY_DISK",
    )

    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "open_position" in data
    assert "closed_trades" in data
    assert "processed_idempotency_keys" not in data, "Defect: idempotency records not in persistent schema"


# =============================================================================
# AUDIT 3: MULTI-PROCESS CONCURRENCY & FEED CODE AUDIT
# =============================================================================

def test_audit3_multiprocess_concurrency_limitation(tmp_path):
    """Audit 3.1: Demonstrate threading.Lock() cannot guard across separate OS processes.
    
    FINDING: DeltaPaperBroker uses self._lock = threading.Lock().
    When two separate OS processes run concurrently against the same state_file,
    neither process sees the other's in-memory _pending_reservation!
    Both processes can reserve the slot simultaneously.
    """
    import subprocess
    state_file = str(tmp_path / "mp_state.json")
    
    # Initialize state file
    b_init = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    del b_init

    worker_code = f"""
import sys, json
from services.delta_paper_broker import DeltaPaperBroker
state_file = {repr(state_file)}
key = sys.argv[1]
broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
reserved, msg, token = broker.reserve_order_slot(
    idempotency_key=key,
    strategy_name='MP_TEST',
    direction='LONG',
    symbol='XAUTUSD',
    ttl_seconds=30.0,
)
print(json.dumps({{'reserved': reserved, 'token': token}}))
"""
    p1 = subprocess.run([sys.executable, "-c", worker_code, "P1_KEY"], capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    p2 = subprocess.run([sys.executable, "-c", worker_code, "P2_KEY"], capture_output=True, text=True, cwd=str(PROJECT_ROOT))

    res1 = json.loads(p1.stdout.strip().splitlines()[-1])
    res2 = json.loads(p2.stdout.strip().splitlines()[-1])

    # Both independent processes succeed in reserving because in-memory locks are not shared across processes!
    assert res1["reserved"] is True
    assert res2["reserved"] is True, "Confirmed finding: multi-process isolation requires OS-level file locking (fcntl/flock)"


def test_audit3_feed_out_of_order_stream_analysis():
    """Audit 3.2: Prove that interleaving v2/ticker and all_trades causes false out-of-order count.
    
    FINDING:
    Stage1MarketObserver uses a SINGLE variable `self.last_tick_ts` shared between:
    - process_ticker()
    - process_trade()
    
    When ticker snapshot arrives with timestamp T=1000, and trade fill arrives with
    timestamp T=995, comparing trade T=995 < last_tick_ts T=1000 falsely increments
    out_of_order_ticks!
    """
    obs = Stage1MarketObserver(duration_seconds=10)

    # Case A: Current shared clock implementation
    # Ticker at T = 1,000,000 us
    obs.process_ticker({"symbol": "XAUTUSD", "timestamp": 1_000_000, "close": "4000.0"})
    assert obs.out_of_order_ticks == 0

    # Trade execution at T = 999,500 us (500us earlier, dispatched concurrently by exchange)
    obs.process_trade({"symbol": "XAUTUSD", "timestamp": 999_500, "price": "4000.0", "size": 1})
    assert obs.out_of_order_ticks == 1, "Proves false out-of-order detection due to cross-channel clock sharing"

    # Ticker at T = 1,000,500 us
    obs.process_ticker({"symbol": "XAUTUSD", "timestamp": 1_000_500, "close": "4000.5"})
    assert obs.out_of_order_ticks == 1

    # Trade execution at T = 1,000,200 us
    obs.process_trade({"symbol": "XAUTUSD", "timestamp": 1_000_200, "price": "4000.5", "size": 1})
    assert obs.out_of_order_ticks == 2, "Confirmed: cross-stream comparison causes false out-of-order count"
