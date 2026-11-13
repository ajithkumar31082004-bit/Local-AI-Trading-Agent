# Phase 3.31 — Recoverable Execution Transaction & Write-Ahead Intent Design

## 1. Context & Problem Statement

In Phase 3.30, an independent audit identified an asynchronous dual-store commit window (Boundary C.4):
The broker mutated cash in memory, committed the order to SQLite as `COMMITTED` via `commit_order_reservation()`, wrote JSON state with `open_position = None` and deducted fees, and only then assigned `self.open_position = pos` in a second write.
If a process crash or power loss occurred between these two writes:
1. SQLite held a permanent `COMMITTED` status.
2. JSON state had fees deducted from cash balance, but `open_position` remained `None`.
3. Same-key retries were permanently blocked as duplicates by SQLite.
4. Cash was lost without an open position, violating accounting invariants.

Furthermore, SQLite operated with `PRAGMA synchronous = NORMAL;`, meaning transactions were not guaranteed to be `fsync`'d across host power cuts, and Windows connection handles were not released deterministically during corrupt database recovery.

---

## 2. Architectural Evaluation: SQLite Authority vs Write-Ahead Journal

### Option A: Complete Migration of Broker State to SQLite
- **Concept**: Migrate cash balance, open position, closed trades ledger, and settings entirely into SQLite tables. Eliminate JSON state.
- **Evaluation**: While SQLite provides single-transaction ACID guarantees across all tables, multiple external components (including `dashboard_state_service.py`, monitoring endpoints, and test suites) depend on the published schema of `paper_account_xautusd.json`. Completely removing the JSON state would violate the strict constraint against breaking monitoring and external interfaces.

### Option B (Selected): Write-Ahead Execution Record (Intent) with Synchronized JSON Projection
- **Concept**: Maintain SQLite as the authoritative transactional Write-Ahead Log (WAL) with explicit lifecycle states (`RESERVED` -> `EXECUTING` -> `COMMITTED` / `ROLLED_BACK`), while maintaining `paper_account_xautusd.json` as the published atomic state snapshot.
- **Evaluation**: Fully satisfies all six Step 2 requirements:
  1. Complete broker state is durably staged and verified before commitment.
  2. Cash is never deducted in memory or on disk until after the execution intent is durably recorded.
  3. JSON persistence is unified into a single atomic write (`open_position` and deducted cash written simultaneously).
  4. Startup recovery deterministically reconciles interrupted intents via roll-forward or roll-back.
  5. Backwards compatibility is 100% preserved.

---

## 3. The 7-Stage Recoverable Execution Lifecycle

```
[1. Reservation]
      │  reserve_order_slot() -> status='RESERVED', token='RES_GEN{gen}_{hex}', TTL=30s
      ▼
[2. Calculation & Pre-Validation]
      │  Validate 1-lot cap, contract qty, fill price, taker fees, slippage, margin.
      │  *ZERO state mutation in memory or on disk*
      ▼
[3. Write-Ahead Intent Recording]
      │  record_execution_intent(token, intent_payload)
      │  SQLite ACID commit with PRAGMA synchronous = FULL
      │  status transitions 'RESERVED' -> 'EXECUTING'
      ▼
[4. Atomic In-Memory Mutation]
      │  Mutate self.cash_balance, self.total_fees, self.total_slippage,
      │  self.open_position = pos, clear self._pending_reservation
      ▼
[5. Atomic JSON State Persistence]
      │  _save_state() writes both open_position and deducted cash atomically via .tmp and os.replace
      ▼
[6. Durable SQLite Completion]
      │  commit_execution(token, pos)
      │  status transitions 'EXECUTING' -> 'COMMITTED'
      ▼
[7. Caller Acknowledgement]
         return (True, msg, dict(pos))
```

---

## 4. Crash Recovery Protocol (`_recover_pending_execution_intents`)

During broker initialization or state reload (`_load_state()`) under the cross-process lock:
The broker executes `SELECT * FROM idempotency_records WHERE status = 'EXECUTING'`.

For each pending intent:
- **Case 1 (Post-JSON Crash / Roll-Forward)**:
  - *Condition*: JSON state contains `open_position` matching the intent's `order_id` or `idempotency_key` (or it is in `closed_trades`).
  - *Inference*: Crash occurred after Stage 5 (JSON saved) but before Stage 6 (SQLite completion).
  - *Action*: Call `commit_execution(token, open_position)`. Transition status to `COMMITTED`.
  - *Result*: State is 100% consistent; caller retries are rejected as duplicate; position is safe.
- **Case 2 (Pre-JSON Crash / Roll-Back)**:
  - *Condition*: JSON state does NOT contain `open_position`.
  - *Inference*: Crash occurred before Stage 5 (JSON saved). JSON on disk holds pre-execution cash.
  - *Action*: Call `rollback_execution(token, reason="CRASH_RECOVERY_UNPERSISTED")`. Transition status to `ROLLED_BACK`. Clear stale pending reservation.
  - *Result*: Zero cash deducted. Zero phantom positions. The idempotency key is freed so caller retry can execute cleanly.

---

## 5. Failure Guarantees

| Invariant | Guarantee |
| :--- | :--- |
| **No Phantom Open Position** | Position is only open if persisted in JSON state. Unpersisted intents are rolled back. |
| **No Cash Deduction without Position** | Cash and position are written simultaneously in Stage 5. If Stage 5 is not reached, recovery rolls back intent, leaving pre-crash cash intact. |
| **No Missing Idempotency Key** | Executed keys are permanently recorded as `COMMITTED` in SQLite and cannot be pruned by in-memory FIFO. |
| **Duplicate Prevention** | Keys in `EXECUTING` or `COMMITTED` status reject duplicate reservations. |
| **Power-Loss Durability** | `PRAGMA synchronous = FULL;` ensures SQLite transactions are flushed to disk with `fsync()`. |
