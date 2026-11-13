"""Phase 3.27 — Local-Only Safety Remediation Verification Suite.

Validates the full closure of the three Phase 3.26 safety vulnerabilities:
1. Step 2: Order Reservation TTL Expiration & Fencing
2. Step 3: Persistent Idempotency Across Process Restarts
3. Step 4: Multi-Process Concurrency & OS-Level Locking
4. Step 5: Public Market Feed Independent Monotonic Clocks
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from scripts.stage1_readonly_market_monitor import Stage1MarketObserver


# =============================================================================
# STEP 2: TTL EXPIRATION AND FENCING TESTS
# =============================================================================

def test_short_ttl_followed_by_longer_ttl(tmp_path):
    """Worker A reserves with 0.1s TTL. Worker B requests slot with 30s TTL after 0.15s.
    
    Verifies that Worker B is NOT blocked by parameter coupling and successfully acquires
    the slot once Worker A's lease expires.
    """
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res_a, msg_a, token_a = broker.reserve_order_slot("KEY_SHORT_A", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.1)
    assert res_a is True
    assert token_a is not None

    time.sleep(0.15)  # Lease A expires

    # Worker B arrives with standard 30s TTL
    res_b, msg_b, token_b = broker.reserve_order_slot("KEY_B", "ORB", "SHORT", "XAUTUSD", ttl_seconds=30.0)
    assert res_b is True, f"Worker B must acquire expired slot, but got: {msg_b}"
    assert token_b != token_a
    assert "RES_GEN2_" in token_b or token_b.startswith("RES_")


def test_long_ttl_followed_by_shorter_ttl(tmp_path):
    """Worker A reserves with 30s TTL. Worker B requests slot with 0.1s TTL after 0.05s.
    
    Verifies that Worker B CANNOT preempt Worker A while Worker A is still within its active TTL.
    """
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res_a, _, token_a = broker.reserve_order_slot("KEY_LONG_A", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    assert res_a is True

    time.sleep(0.05)

    res_b, msg_b, token_b = broker.reserve_order_slot("KEY_B", "ORB", "SHORT", "XAUTUSD", ttl_seconds=0.1)
    assert res_b is False, "Worker B must NOT preempt Worker A while Worker A is within TTL"
    assert "currently reserved" in msg_b
    assert token_b is None


def test_expired_commit_without_competing_worker(tmp_path):
    """Uncontended expired commit must be rejected.
    
    Worker reserves with 0.1s TTL. No other worker arrives. Worker attempts to commit after 0.2s.
    Verifies that commit is rejected, position is NOT opened, and pending reservation is cleared.
    """
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res, _, token = broker.reserve_order_slot("KEY_SLOW", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.1)
    assert res is True

    time.sleep(0.2)  # Expired

    # Commit attempt without competitor
    committed = broker.commit_order_reservation(token, {"order_id": "ORD_SLOW", "trade_id": "TR_SLOW"})
    assert committed is False, "Uncontended expired reservation commit must be rejected"
    assert broker._pending_reservation is None


def test_worker_a_expires_worker_b_reserves_worker_a_commit_fails(tmp_path):
    """Worker A expires; Worker B reserves (generation advances); Worker A attempts to commit.
    
    Verifies fencing generation protects against late commits from preempted workers.
    """
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Worker A reserves with 0.1s TTL
    res_a, _, token_a = broker.reserve_order_slot("KEY_A", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.1)
    assert res_a is True

    time.sleep(0.15)  # A expires

    # Worker B acquires slot
    res_b, _, token_b = broker.reserve_order_slot("KEY_B", "ORB", "SHORT", "XAUTUSD", ttl_seconds=30.0)
    assert res_b is True
    assert token_b != token_a

    # Stale Worker A attempts commit
    committed_a = broker.commit_order_reservation(token_a, {"order_id": "ORD_A", "trade_id": "TR_A"})
    assert committed_a is False, "Stale Worker A commit must be fenced off"

    # Worker B commits successfully
    committed_b = broker.commit_order_reservation(token_b, {"order_id": "ORD_B", "trade_id": "TR_B"})
    assert committed_b is True


def test_failed_commit_leaves_broker_state_unchanged(tmp_path):
    """Verify that a failed commit in open_simulated_trade leaves open_position None,
    cash unchanged, ledger unchanged, and rolls back fees.
    """
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    initial_cash = broker.cash_balance

    # Manually reserve and let it expire to force commit failure
    res, _, token = broker.reserve_order_slot("KEY_FAIL", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.05)
    time.sleep(0.1)

    # Attempt commit directly
    pos_dummy = {
        "trade_id": "TR_TEST",
        "order_id": "ORD_TEST",
        "lots": 1,
        "entry_price": 4000.0,
    }
    commit_ok = broker.commit_order_reservation(token, pos_dummy)
    assert commit_ok is False

    # State must be completely unmutated
    assert broker.open_position is None
    assert broker.cash_balance == initial_cash
    assert len(broker.closed_trades) == 0
    assert broker.total_fees == 0.0
    assert broker.total_slippage == 0.0


def test_concurrent_reservation_attempts_in_memory(tmp_path):
    """Concurrent threads attempting reservation -> exactly one acquires the slot."""
    import threading

    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    results = []

    def worker_attempt(idx):
        res, msg, tok = broker.reserve_order_slot(f"KEY_THREAD_{idx}", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
        results.append((res, tok))

    threads = [threading.Thread(target=worker_attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r[0] is True]
    assert len(successes) == 1, f"Expected exactly 1 successful reservation, got {len(successes)}"


def test_reservation_cleanup_and_recovery(tmp_path):
    """Verify safe release after exception and subsequent worker acquisition."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res1, _, tok1 = broker.reserve_order_slot("KEY_CLEAN", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    assert res1 is True

    # Simulate exception & cleanup
    released = broker.release_order_reservation(tok1, reason="SIMULATED_ABORT")
    assert released is True
    assert broker._pending_reservation is None

    # Next worker can acquire immediately
    res2, _, tok2 = broker.reserve_order_slot("KEY_NEXT", "ORB", "SHORT", "XAUTUSD", ttl_seconds=30.0)
    assert res2 is True
    assert tok2 != tok1


# =============================================================================
# STEP 3: PERSISTENT IDEMPOTENCY TESTS
# =============================================================================

def test_same_key_submitted_twice_in_one_process(tmp_path):
    """Same idempotency key submitted twice in a single process -> second rejected."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok1, _, _ = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_DUAL")
    assert ok1 is True

    # Close trade so position is flat
    broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert broker.open_position is None

    # Resubmit identical key
    ok2, msg2, _ = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_DUAL")
    assert ok2 is False
    assert "Duplicate order" in msg2


def test_same_key_submitted_after_process_restart(tmp_path):
    """Key executed in Process 1, position closed, Process 2 loads from disk -> duplicate rejected."""
    state_file = tmp_path / "broker_state.json"

    # Process 1
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    ok1, _, _ = b1.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_RESTART_REPLAY")
    assert ok1 is True
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert b1.open_position is None
    del b1

    # Process 2 (simulating fresh process startup reading disk state)
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert "KEY_RESTART_REPLAY" in b2._processed_idempotency_keys

    ok2, msg2, _ = b2.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_RESTART_REPLAY")
    assert ok2 is False, "Duplicate order across restart must be REJECTED"
    assert "Duplicate order" in msg2


def test_restart_after_trade_closure(tmp_path):
    """Verify that multiple closed trades retain their idempotency records after restart."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    for i in range(3):
        k = f"KEY_MULTI_{i}"
        ok, _, _ = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key=k)
        assert ok is True
        broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")

    # Restart
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    for i in range(3):
        assert f"KEY_MULTI_{i}" in b2._processed_idempotency_keys


def test_crash_recovery_simulation_around_state_persistence(tmp_path):
    """Verify state file atomic replace and temporary file resilience."""
    state_file = tmp_path / "broker_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_CRASH_TEST")

    # Leave a dangling .tmp file
    tmp_file = state_file.with_suffix(".tmp")
    with open(tmp_file, "w") as f:
        f.write("{corrupt_json")

    # Restart broker
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert b2.open_position is not None
    assert "KEY_CRASH_TEST" in b2._processed_idempotency_keys


def test_missing_or_corrupt_idempotency_storage(tmp_path):
    """Verify broker handles corrupted JSON state file safely without unhandled crashes."""
    state_file = tmp_path / "corrupt_state.json"
    with open(state_file, "w") as f:
        f.write("NOT_A_VALID_JSON{{{")

    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert broker.cash_balance == 10000.0
    assert broker.open_position is None


def test_existing_ledger_and_accounting_invariants_preserved():
    """Verify that the official historical state file preserves PAPER_20261105_101000_LONG intact."""
    official_file = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    if not official_file.exists():
        pytest.skip("Historical state file data/paper_forward_24h_state.json not present")

    with open(official_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    closed = data.get("closed_trades", [])
    incident_trade = next((t for t in closed if t.get("trade_id") == "PAPER_20261105_101000_LONG"), None)
    assert incident_trade is not None, "Historical trade PAPER_20261105_101000_LONG is missing!"
    assert incident_trade.get("requested_lots") == 484
    assert incident_trade.get("filled_lots") == 484
    assert incident_trade.get("net_pnl") == -7.9472


# =============================================================================
# STEP 4: MULTI-PROCESS CONCURRENCY TESTS
# =============================================================================

def test_two_independent_processes_request_same_reservation_simultaneously(tmp_path):
    """Two independent OS processes race to reserve order slot.
    
    Verifies that OS-level file locking prevents concurrent reservations across processes:
    exactly 1 process succeeds, 1 is rejected.
    """
    state_file = str(tmp_path / "mp_race_state.json")
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
print(json.dumps({{'reserved': reserved, 'token': token, 'msg': msg}}))
"""
    p1 = subprocess.Popen([sys.executable, "-c", worker_code, "PROC_1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))
    p2 = subprocess.Popen([sys.executable, "-c", worker_code, "PROC_2"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))

    out1, _ = p1.communicate(timeout=10)
    out2, _ = p2.communicate(timeout=10)

    res1 = json.loads(out1.strip().splitlines()[-1])
    res2 = json.loads(out2.strip().splitlines()[-1])

    successes = sum([res1["reserved"], res2["reserved"]])
    assert successes == 1, f"Expected exactly 1 process to acquire reservation, got: p1={res1}, p2={res2}"


def test_ten_process_stress_test(tmp_path):
    """Ten independent OS processes concurrently request reservations.
    
    Verifies that exactly 1 process acquires the slot, 9 are rejected.
    """
    state_file = str(tmp_path / "ten_proc_state.json")
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
    strategy_name='STRESS',
    direction='LONG',
    symbol='XAUTUSD',
    ttl_seconds=30.0,
)
print(json.dumps({{'reserved': reserved}}))
"""
    procs = [
        subprocess.Popen([sys.executable, "-c", worker_code, f"PROC_{i}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))
        for i in range(10)
    ]

    results = []
    for p in procs:
        out, _ = p.communicate(timeout=15)
        results.append(json.loads(out.strip().splitlines()[-1]))

    successes = sum(1 for r in results if r["reserved"] is True)
    assert successes == 1, f"Expected exactly 1 success out of 10 concurrent processes, got {successes}"


def test_process_termination_while_holding_lock(tmp_path):
    """Verify that when a process terminates abruptly (os._exit), OS releases file lock
    and subsequent process can acquire it without deadlocking.
    """
    state_file = str(tmp_path / "term_lock_state.json")
    broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    del broker

    crash_code = f"""
import os, portalocker
lock_path = {repr(str(Path(state_file).with_suffix('.lock')))}
l = portalocker.Lock(lock_path, timeout=5.0)
l.acquire()
os._exit(1)  # Hard abrupt termination
"""
    p = subprocess.run([sys.executable, "-c", crash_code], cwd=str(PROJECT_ROOT))
    assert p.returncode == 1

    # Process 2 attempts to use broker
    b2 = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    res, msg, tok = b2.reserve_order_slot("KEY_POST_CRASH", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    assert res is True, f"Subsequent process must acquire lock after crash, got: {msg}"


def test_duplicate_idempotency_keys_across_processes(tmp_path):
    """Process 1 commits trade with key; Process 2 attempts same key -> rejected."""
    state_file = str(tmp_path / "proc_idemp_state.json")
    
    # Process 1 executes trade
    b1 = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    ok1, _, _ = b1.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="CROSS_PROC_KEY")
    assert ok1 is True
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    del b1

    # Subprocess runs with same key
    check_code = f"""
import sys, json
from services.delta_paper_broker import DeltaPaperBroker
state_file = {repr(state_file)}
broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
ok, msg, _ = broker.open_simulated_trade('XAUTUSD', 'LONG', 4000.0, 3980.0, 4040.0, 1, 'VWAP', idempotency_key='CROSS_PROC_KEY')
print(json.dumps({{'ok': ok, 'msg': msg}}))
"""
    p = subprocess.run([sys.executable, "-c", check_code], capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["ok"] is False
    assert "Duplicate order" in res["msg"]


# =============================================================================
# STEP 5: FEED CLOCK MONOTONICITY TESTS
# =============================================================================

def test_interleaved_feed_channels_no_false_out_of_order():
    """Verify that interleaved ticker and trade messages do NOT trigger false out-of-order ticks.
    
    Delta Exchange dispatches quotes (v2/ticker) and trade executions (all_trades)
    via separate internal pipelines. When quote T=1000us is followed by trade T=995us,
    the observer must evaluate each stream against its own clock, yielding 0 out-of-order ticks.
    """
    obs = Stage1MarketObserver(duration_seconds=10)

    # 1. Ticker at T=1,000,000 us
    obs.process_ticker({"symbol": "XAUTUSD", "timestamp": 1_000_000, "close": "4000.0"})
    assert obs.out_of_order_ticks == 0

    # 2. Trade execution at T=999,500 us (500us earlier exchange print, arrived right after quote)
    obs.process_trade({"symbol": "XAUTUSD", "timestamp": 999_500, "price": "4000.0", "size": 1})
    assert obs.out_of_order_ticks == 0, "Independent stream clocks must NOT trigger false out-of-order tick"

    # 3. Ticker at T=1,000,500 us
    obs.process_ticker({"symbol": "XAUTUSD", "timestamp": 1_000_500, "close": "4000.5"})
    assert obs.out_of_order_ticks == 0

    # 4. Trade execution at T=1,000,200 us
    obs.process_trade({"symbol": "XAUTUSD", "timestamp": 1_000_200, "price": "4000.5", "size": 1})
    assert obs.out_of_order_ticks == 0, "Trade stream progressed monotonically (999500 -> 1000200)"


def test_independent_stream_monotonicity():
    """Verify that true out-of-order ticks within a single channel ARE properly flagged."""
    obs = Stage1MarketObserver(duration_seconds=10)

    # Ticker at T=100
    obs.process_ticker({"symbol": "XAUTUSD", "timestamp": 100, "close": "4000.0"})
    assert obs.out_of_order_ticks == 0
    assert obs.ticker_out_of_order_ticks == 0

    # Ticker at T=90 (true out-of-order in ticker stream)
    obs.process_ticker({"symbol": "XAUTUSD", "timestamp": 90, "close": "4000.0"})
    assert obs.out_of_order_ticks == 1
    assert obs.ticker_out_of_order_ticks == 1

    # Trade at T=200
    obs.process_trade({"symbol": "XAUTUSD", "timestamp": 200, "price": "4000.0", "size": 1})
    assert obs.out_of_order_ticks == 1
    assert obs.trade_out_of_order_ticks == 0

    # Trade at T=150 (true out-of-order in trade stream)
    obs.process_trade({"symbol": "XAUTUSD", "timestamp": 150, "price": "4000.0", "size": 1})
    assert obs.out_of_order_ticks == 2
    assert obs.trade_out_of_order_ticks == 1
