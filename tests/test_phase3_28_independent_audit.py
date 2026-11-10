"""Phase 3.28 — Independent Post-Remediation Safety Audit Test Suite.

Audits:
- Audit A: Idempotency Retention & Pruning Replay Analysis
- Audit B: Lock Coverage Across All State Mutations & Multi-Process Races
- Audit C: Crash Durability, Atomic Persistence & Recovery
- Audit D: Fencing Boundary Verification
- Audit E: Safety Invariants & Historical Incident Preservation
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
from risk.position_size_guard import (
    validate_paper_order_lots,
    validate_contract_quantity,
    PositionSizeViolation,
    APPROVED_MAX_PAPER_LOTS,
)


# =============================================================================
# AUDIT A: IDEMPOTENCY RETENTION & PRUNING REPLAY ANALYSIS
# =============================================================================

def test_audit_a_pruning_drops_old_keys(tmp_path):
    """Audit A.1: Verify that generating >1,000 keys prunes the oldest keys."""
    state_file = tmp_path / "prune_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=100000.0)

    # Directly insert 1,005 dummy idempotency keys to simulate 1,005 historical fills
    for i in range(1005):
        key = f"KEY_{i:04d}"
        broker._processed_idempotency_keys[key] = {
            "token": f"TOK_{i}",
            "order_id": f"ORD_{i}",
            "trade_id": f"TR_{i}",
            "status": "FILLED",
            "timestamp": f"2026-11-10T10:{i//60:02d}:{i%60:02d}Z",
        }
    broker._save_state()

    # Trigger commit of a 1,006th key to execute pruning logic
    broker.reserve_order_slot("KEY_TRIGGER", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    tok = broker._pending_reservation["token"]
    broker.commit_order_reservation(tok, {"order_id": "ORD_TRIG", "trade_id": "TR_TRIG"})

    # Check cache size
    assert len(broker._processed_idempotency_keys) == 1000, "Pruning must enforce <= 1000 keys"

    # The oldest keys (KEY_0000 to KEY_0005) must be pruned from cache
    assert "KEY_0000" not in broker._processed_idempotency_keys, "Oldest key must be pruned"
    assert "KEY_TRIGGER" in broker._processed_idempotency_keys, "Newest key must be retained"


def test_audit_a_pruned_key_replay_vulnerability(tmp_path):
    """Audit A.2: Confirm whether a pruned key can be re-executed after restart.
    
    FINDING:
    When a key is pruned from _processed_idempotency_keys:
    1. It no longer exists in self._processed_idempotency_keys.
    2. reserve_order_slot does not inspect closed_trades.
    3. Therefore, an old signal replayed after 1,000 subsequent orders WILL EXECUTE AGAIN.
    """
    state_file = tmp_path / "replay_state.json"
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=100000.0)

    # 1. Execute initial trade with KEY_TARGET
    ok1, _, _ = b1.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_TARGET")
    assert ok1 is True
    b1.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    assert b1.open_position is None
    assert "KEY_TARGET" in b1._processed_idempotency_keys

    # Set KEY_TARGET to an older timestamp so subsequent 1,005 keys are genuinely newer
    b1._processed_idempotency_keys["KEY_TARGET"]["timestamp"] = "2026-11-01T00:00:00Z"

    # 2. Simulate 1,005 subsequent trades triggering pruning of KEY_TARGET
    for i in range(1005):
        k = f"KEY_FILL_{i:04d}"
        b1._processed_idempotency_keys[k] = {
            "token": f"TOK_{i}",
            "order_id": f"ORD_{i}",
            "trade_id": f"TR_{i}",
            "status": "FILLED",
            "timestamp": f"2026-11-02T12:{i//60:02d}:{i%60:02d}Z",
        }
    b1._save_state()
    b1.reserve_order_slot("KEY_FORCE_PRUNE", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    tok = b1._pending_reservation["token"]
    b1.commit_order_reservation(tok, {"order_id": "ORD_FP", "trade_id": "TR_FP"})
    b1._save_state()

    # Verify KEY_TARGET is no longer in cache
    assert "KEY_TARGET" not in b1._processed_idempotency_keys
    del b1

    # 3. Process restart
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=100000.0)
    assert "KEY_TARGET" not in b2._processed_idempotency_keys

    # 4. Attempt replay of KEY_TARGET
    ok_replay, msg_replay, _ = b2.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "VWAP", idempotency_key="KEY_TARGET")

    # Documenting the architectural boundary:
    # A pruned key is permitted to execute because the cache is bounded to 1,000 keys.
    assert ok_replay is True, "Documents architectural finding: pruned keys past 1,000 window can be re-executed"


# =============================================================================
# AUDIT B: LOCK COVERAGE ACROSS ALL BROKER STATE MUTATIONS
# =============================================================================

def test_audit_b_simultaneous_reservation_across_processes(tmp_path):
    """Audit B.1: Two independent OS processes attempt simultaneous reservation."""
    state_file = str(tmp_path / "b1_state.json")
    b_init = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    del b_init

    script = f"""
import sys, json
from services.delta_paper_broker import DeltaPaperBroker
state_file = {repr(state_file)}
key = sys.argv[1]
broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
res, msg, tok = broker.reserve_order_slot(key, 'STRAT', 'LONG', 'XAUTUSD', 30.0)
print(json.dumps({{'reserved': res, 'token': tok}}))
"""
    p1 = subprocess.Popen([sys.executable, "-c", script, "KEY_1"], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))
    p2 = subprocess.Popen([sys.executable, "-c", script, "KEY_2"], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))

    out1, _ = p1.communicate(timeout=10)
    out2, _ = p2.communicate(timeout=10)

    r1 = json.loads(out1.strip().splitlines()[-1])
    r2 = json.loads(out2.strip().splitlines()[-1])

    successes = sum([r1["reserved"], r2["reserved"]])
    assert successes == 1, f"Lock must allow exactly 1 reservation, got: r1={r1}, r2={r2}"


def test_audit_b_simultaneous_open_trade_across_processes(tmp_path):
    """Audit B.2: Two independent OS processes attempt simultaneous open_simulated_trade."""
    state_file = str(tmp_path / "b2_state.json")
    b_init = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    del b_init

    script = f"""
import sys, json
from services.delta_paper_broker import DeltaPaperBroker
state_file = {repr(state_file)}
key = sys.argv[1]
broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
ok, msg, pos = broker.open_simulated_trade('XAUTUSD', 'LONG', 4000.0, 3980.0, 4040.0, 1, 'TEST', idempotency_key=key)
print(json.dumps({{'ok': ok}}))
"""
    p1 = subprocess.Popen([sys.executable, "-c", script, "PROC_A"], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))
    p2 = subprocess.Popen([sys.executable, "-c", script, "PROC_B"], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))

    out1, _ = p1.communicate(timeout=10)
    out2, _ = p2.communicate(timeout=10)

    r1 = json.loads(out1.strip().splitlines()[-1])
    r2 = json.loads(out2.strip().splitlines()[-1])

    successes = sum([r1["ok"], r2["ok"]])
    assert successes == 1, f"Only 1 trade can open concurrently, got {successes}"


def test_audit_b_simultaneous_close_across_processes(tmp_path):
    """Audit B.3: Two processes attempt simultaneous close on the same open position."""
    state_file = str(tmp_path / "b3_state.json")
    b_init = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    b_init.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "TEST", idempotency_key="KEY_OPEN")
    del b_init

    script = f"""
import sys, json
from services.delta_paper_broker import DeltaPaperBroker
state_file = {repr(state_file)}
broker = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
ok, msg, closed = broker.close_simulated_trade(exit_price=4040.0, exit_reason='TP')
print(json.dumps({{'ok': ok}}))
"""
    p1 = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))
    p2 = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT))

    out1, _ = p1.communicate(timeout=10)
    out2, _ = p2.communicate(timeout=10)

    r1 = json.loads(out1.strip().splitlines()[-1])
    r2 = json.loads(out2.strip().splitlines()[-1])

    # Exactly 1 process should close the position; the other must find position is already closed
    successes = sum([r1["ok"], r2["ok"]])
    assert successes == 1, f"Position must be closed exactly once, got {successes}"


def test_audit_b_concurrent_equity_persistence(tmp_path):
    """Audit B.4: Verify cash and equity conservation under rapid sequential operations."""
    state_file = tmp_path / "b4_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    for i in range(5):
        ok_open, _, _ = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "TEST", idempotency_key=f"K_{i}")
        assert ok_open is True
        ok_close, _, c = broker.close_simulated_trade(exit_price=4020.0, exit_reason="TP")
        assert ok_close is True

    summary = broker.get_account_summary()
    total_net = sum(t["net_pnl"] for t in broker.closed_trades)
    assert summary["cash_balance"] == pytest.approx(10000.0 + total_net, abs=0.0001)
    assert summary["equity"] == pytest.approx(summary["cash_balance"], abs=0.0001)


def test_audit_b_recovery_after_process_crash_while_locked(tmp_path):
    """Audit B.5: Hard process termination releases file lock, allowing recovery."""
    state_file = str(tmp_path / "crash_state.json")
    b_init = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    del b_init

    crash_cmd = f"""
import os, portalocker
lock_file = {repr(str(Path(state_file).with_suffix('.lock')))}
l = portalocker.Lock(lock_file, timeout=5.0)
l.acquire()
os._exit(42)
"""
    res = subprocess.run([sys.executable, "-c", crash_cmd], cwd=str(PROJECT_ROOT))
    assert res.returncode == 42

    # Process 2 immediately acquires
    b2 = DeltaPaperBroker(state_file=state_file, initial_equity=10000.0)
    res_slot, msg, _ = b2.reserve_order_slot("POST_CRASH_KEY", "VWAP", "LONG", "XAUTUSD")
    assert res_slot is True


# =============================================================================
# AUDIT C: CRASH DURABILITY & ATOMIC STATE PERSISTENCE
# =============================================================================

def test_audit_c_atomic_state_replacement(tmp_path):
    """Audit C.1: Verify atomic .tmp file replacement mechanism."""
    state_file = tmp_path / "atomic_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "TEST", idempotency_key="KEY_ATOMIC")
    assert state_file.exists()
    assert not state_file.with_suffix(".tmp").exists(), "Temporary file must not remain after successful save"

    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["open_position"] is not None
    assert "KEY_ATOMIC" in data["processed_idempotency_keys"]


def test_audit_c_corrupt_state_recovery(tmp_path):
    """Audit C.2: Verify recovery from incomplete or corrupt state file."""
    state_file = tmp_path / "corrupt_state.json"

    # Write truncated/invalid JSON
    with open(state_file, "w", encoding="utf-8") as f:
        f.write('{"open_position": {"symbol": "XAUTUSD"')  # Incomplete JSON

    # Broker must handle gracefully and initialize fresh state
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    assert broker.cash_balance == 10000.0
    assert broker.open_position is None
    assert len(broker.closed_trades) == 0


def test_audit_c_state_data_consistency(tmp_path):
    """Audit C.3: Verify exact mathematical consistency between position, cash, ledger, and idempotency."""
    state_file = tmp_path / "consistent_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # 1. Open trade
    broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, 1, "TEST", idempotency_key="KEY_CONSISTENT")
    summary1 = broker.get_account_summary()
    assert summary1["has_open_position"] is True
    assert summary1["margin_used"] > 0
    assert "KEY_CONSISTENT" in broker._processed_idempotency_keys

    # 2. Close trade
    _, _, closed = broker.close_simulated_trade(exit_price=4040.0, exit_reason="TP")
    summary2 = broker.get_account_summary()
    assert summary2["has_open_position"] is False
    assert summary2["margin_used"] == 0.0
    assert summary2["cash_balance"] == pytest.approx(10000.0 + closed["net_pnl"], abs=0.0001)
    assert summary2["equity"] == pytest.approx(summary2["cash_balance"], abs=0.0001)


# =============================================================================
# AUDIT D: FENCING BOUNDARIES & COMMIT REJECTION
# =============================================================================

def test_audit_d_expired_reservation_commit_rejected(tmp_path):
    """Audit D.1: Uncontended expired reservation commit must be rejected."""
    state_file = tmp_path / "fence_expired.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res, _, tok = broker.reserve_order_slot("KEY_EXP", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.05)
    assert res is True
    time.sleep(0.10)

    committed = broker.commit_order_reservation(tok, {"order_id": "ORD_EXP", "trade_id": "TR_EXP"})
    assert committed is False, "Expired reservation must not commit"
    assert broker._pending_reservation is None


def test_audit_d_stale_worker_commit_after_generation_advance_rejected(tmp_path):
    """Audit D.2: Stale worker commit after new generation created must be rejected."""
    state_file = tmp_path / "fence_gen.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    # Worker A reserves with 0.1s TTL
    res_a, _, tok_a = broker.reserve_order_slot("KEY_A", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.1)
    time.sleep(0.15)

    # Worker B reserves (advances generation)
    res_b, _, tok_b = broker.reserve_order_slot("KEY_B", "ORB", "SHORT", "XAUTUSD", ttl_seconds=30.0)
    assert res_b is True
    assert tok_b != tok_a

    # Stale Worker A attempts commit
    committed_a = broker.commit_order_reservation(tok_a, {"order_id": "ORD_A"})
    assert committed_a is False, "Stale worker commit must be rejected"


def test_audit_d_duplicate_commit_rejected(tmp_path):
    """Audit D.3: Duplicate commit with same token must be rejected on second call."""
    state_file = tmp_path / "fence_dup.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res, _, tok = broker.reserve_order_slot("KEY_DUP", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    committed1 = broker.commit_order_reservation(tok, {"order_id": "ORD_DUP"})
    assert committed1 is True

    # Re-committing same token must fail
    committed2 = broker.commit_order_reservation(tok, {"order_id": "ORD_DUP_2"})
    assert committed2 is False


def test_audit_d_commit_after_release_rejected(tmp_path):
    """Audit D.4: Commit after reservation release must be rejected."""
    state_file = tmp_path / "fence_rel.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res, _, tok = broker.reserve_order_slot("KEY_REL", "VWAP", "LONG", "XAUTUSD", ttl_seconds=30.0)
    released = broker.release_order_reservation(tok, reason="TEST_RELEASE")
    assert released is True

    # Attempt commit
    committed = broker.commit_order_reservation(tok, {"order_id": "ORD_REL"})
    assert committed is False


def test_audit_d_commit_following_process_restart(tmp_path):
    """Audit D.5: Commit attempt across process restart when lease expired."""
    state_file = tmp_path / "fence_restart.json"
    b1 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    res, _, tok = b1.reserve_order_slot("KEY_RES_REST", "VWAP", "LONG", "XAUTUSD", ttl_seconds=0.1)
    time.sleep(0.15)  # Expired
    del b1

    # Restart
    b2 = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)
    committed = b2.commit_order_reservation(tok, {"order_id": "ORD_REST"})
    assert committed is False


# =============================================================================
# AUDIT E: SAFETY INVARIANTS & HISTORICAL INCIDENT PRESERVATION
# =============================================================================

def test_audit_e_position_size_guard_rejection():
    """Audit E.1: Verify central position-sizing guard rejects oversized orders."""
    # 1 lot valid
    assert validate_paper_order_lots(1) == 1

    # >1 lot must raise PositionSizeViolation
    with pytest.raises(PositionSizeViolation) as excinfo:
        validate_paper_order_lots(2)
    assert "SECURITY SIZING REJECTION" in str(excinfo.value)
    assert "exceeds the authorized forward-test maximum" in str(excinfo.value)

    # 484 lots must raise PositionSizeViolation
    with pytest.raises(PositionSizeViolation) as excinfo:
        validate_paper_order_lots(484)
    assert "SECURITY SIZING REJECTION" in str(excinfo.value)
    assert "exceeds the authorized forward-test maximum" in str(excinfo.value)


def test_audit_e_broker_rejects_oversized_orders(tmp_path):
    """Audit E.2: Verify broker rejects 2-lot order at boundary before reservation."""
    state_file = tmp_path / "guard_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade("XAUTUSD", "LONG", 4000.0, 3980.0, 4040.0, lots=2, strategy_name="TEST")
    assert ok is False
    assert "Position Size Guard Rejection" in msg
    assert pos is None
    assert broker.open_position is None
    assert broker._pending_reservation is None


def test_audit_e_live_trading_hard_barrier():
    """Audit E.3: Verify broker execute_live_order hard exception barrier."""
    broker = DeltaPaperBroker()
    with pytest.raises(PaperSafetyViolation, match="CRITICAL SECURITY VIOLATION"):
        broker.execute_live_order()


def test_audit_e_historical_484_lot_incident_intact():
    """Audit E.4: Verify PAPER_20261105_101000_LONG remains completely unchanged."""
    historical_file = PROJECT_ROOT / "data" / "paper_forward_24h_state.json"
    if not historical_file.exists():
        pytest.skip("Historical file data/paper_forward_24h_state.json not present")

    with open(historical_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    closed = data.get("closed_trades", [])
    trade = next((t for t in closed if t.get("trade_id") == "PAPER_20261105_101000_LONG"), None)
    assert trade is not None, "PAPER_20261105_101000_LONG must exist in official historical state"
    assert trade["requested_lots"] == 484
    assert trade["filled_lots"] == 484
    assert trade["contract_qty"] == 0.484
    assert trade["entry_price"] == 4127.9
    assert trade["net_pnl"] == -7.9472
