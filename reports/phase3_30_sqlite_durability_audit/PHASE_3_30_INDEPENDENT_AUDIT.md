# PHASE 3.30 — SQLITE DURABILITY & RECOVERY INDEPENDENT AUDIT

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Working Directory:** `C:\Users\ajith\Downloads\Local_AI_Agent`  
**Base Commit:** `12b97c8` (Phase 3.29 Permanent Idempotency Remediation)  
**Audit Branch:** `feature/phase3-30-sqlite-durability-audit`  
**Instrument:** `XAUTUSD`  
**Trading Mode:** STRICT PAPER ONLY  
**Audit Date:** 2026-11-10  
**Audit Verdict:** **CONDITIONAL PASS**

---

## 1. Executive Summary

Phase 3.30 conducted an independent forensic audit of the permanent SQLite-backed idempotency architecture implemented in Phase 3.29 (`services/idempotency_registry.py`). The audit evaluated SQLite transaction durability, WAL behavior, corruption recovery, cross-store crash consistency between SQLite and JSON broker state, backup/restore fidelity, and the preservation of strict safety invariants.

### Key Audit Findings
1. **Durable Deduplication Verified**: The SQLite registry reliably blocks order replay across in-memory FIFO cache pruning (>10,000 keys), process restarts, and multi-process concurrency.
2. **Fail-Closed Compliance**: When SQLite database files or WAL files are severely corrupted, the broker fails closed by raising `IdempotencyRegistryError` and halting startup, preventing unvalidated execution.
3. **Dual-Store Commit Asynchrony Identified (Medium-High Risk)**: The broker writes to SQLite first, deducting fees in `commit_order_reservation()`, and persists `self.open_position` in a subsequent JSON write. A crash between these writes permanently marks the key `COMMITTED` without an open position, resulting in a lost trade and phantom fee deduction.
4. **Power-Loss Durability Tradeoff (Medium Risk)**: SQLite operates with `PRAGMA synchronous = NORMAL;`. In WAL mode, transactions survive application crashes via the OS page cache, but are not guaranteed to survive abrupt host power failure or hypervisor shutdown without explicit `fsync()`.
5. **Safety Invariants 100% Maintained**: The one-micro-lot position cap (`APPROVED_MAX_PAPER_LOTS == 1`), rejection of oversized orders (2 and 484 lots), LIVE trading hard block, and historical incident records (`PAPER_20261105_101000_LONG`, 484 lots, -$7.9472) remain fully intact.

---

## 2. Audit A — SQLite Durability Settings & WAL Analysis

### 2.1 Configuration Evaluation
The SQLite connection is initialized in `services/idempotency_registry.py` with:
- `PRAGMA journal_mode = WAL;`
- `PRAGMA synchronous = NORMAL;`
- `PRAGMA busy_timeout = 10000;` (10 seconds)
- Isolation level: `DEFERRED`

### 2.2 Durability vs Performance Tradeoff (`NORMAL` vs `FULL`)
| Parameter | Current Setting (`NORMAL`) | Recommended Setting (`FULL`) | Impact Analysis |
| :--- | :--- | :--- | :--- |
| **Process Crash** | Durability Guaranteed | Durability Guaranteed | OS buffer cache preserves committed WAL frames across process exits and crashes. |
| **Power Loss / Hard Reboot** | Vulnerable | Durability Guaranteed | `NORMAL` does not `fsync()` WAL frames on every commit. Uncheckpointed frames in OS cache are lost on hard power loss. |
| **Transaction Latency** | ~0.1 - 0.5 ms | ~1 - 5 ms | In a paper trading daemon with 1-lot order volume, order frequency is low (at most 1 order per candle). The 1-5ms fsync latency is negligible. |

### 2.3 WAL Checkpoint Behavior
Currently, no explicit checkpointing (`PRAGMA wal_checkpoint`) is scheduled in code. Checkpointing relies on SQLite's default passive threshold (1,000 pages / ~4MB).
- **Finding**: Until checkpointing runs, committed transactions live exclusively in `.idempotency.db-wal`.
- **Recommendation**: Trigger explicit `PRAGMA wal_checkpoint(TRUNCATE)` on broker shutdown and prior to backup operations.

---

## 3. Audit B — Corruption Recovery & Fail-Closed Behavior

The audit tested 6 corruption and recovery scenarios:

| Scenario | Condition | Result | Behavior & Safety Compliance |
| :--- | :--- | :--- | :--- |
| **B.1 Pre-Startup DB Corruption** | Header garbage bytes written to `.idempotency.db` | **FAIL-CLOSED** | SQLite raises `DatabaseError`. Open handle in `_get_connection()` triggers `PermissionError [WinError 32]` on rename attempt. Broker raises `IdempotencyRegistryError` and halts safely. |
| **B.2 Damaged WAL File** | Garbage bytes written to `.idempotency.db-wal` | **FAIL-CLOSED** | SQLite raises `OperationalError: disk I/O error`. Broker raises `IdempotencyRegistryError` and halts. |
| **B.3 Missing DB File** | DB file deleted, JSON state exists | **RECOVERED** | Fresh DB created; `sync_historical_keys()` repopulates all historical trades into SQLite. |
| **B.4 Keys in SQLite Only** | Key in SQLite, absent from in-memory cache | **DEDUPLICATED** | Query against durable SQLite detects `COMMITTED` status and rejects duplicate order. |
| **B.5 Store Disagreement** | JSON has key, SQLite missing | **RECONCILED** | Broker startup synchronizes all closed trades into SQLite registry. |
| **B.6 Unrecoverable Data Loss** | DB corrupted AND JSON state deleted | **FAIL-CLOSED** | Broker raises `IdempotencyRegistryError`, refusing to start with an unvalidated empty registry. |

---

## 4. Audit C — Cross-Store Crash Consistency & Lifecycle Tracing

### 4.1 6-Step Order Lifecycle
1. **Idempotency Reservation**: SQLite records `RESERVED` with unique token, generation counter, and 10s TTL.
2. **Broker Reservation**: `_pending_reservation` recorded in JSON state under cross-process lock.
3. **Execution Sizing & Calculations**: Fill price, slippage, taker fee calculated; sizing guard verified.
4. **SQLite Persistence**: `commit_order_reservation()` marks status `COMMITTED` in SQLite and saves state (deducting entry fee).
5. **JSON State Persistence**: `self.open_position = pos` assigned and saved in JSON state.
6. **Caller Ack**: Order execution confirmed.

### 4.2 Failure Injection Matrix
- **Crash before Step 4**: SQLite reservation expires via 10s TTL. Cash and equity are conserved. Zero phantom fills.
- **Crash between Step 4 and Step 5 (Boundary C.4)**:
  - SQLite is marked `COMMITTED`.
  - Cash balance reflects deducted taker fee.
  - JSON `open_position` is `None`.
  - On restart, caller retry is blocked by SQLite (`COMMITTED`). The trade cannot execute, leading to a lost order and unreversed fee deduction.
  - **Remediation**: Assign `self.open_position = pos` before `commit_order_reservation()` to persist both atomically in one state save.

---

## 5. Audit D — Backup and Restore

1. **SQLite Online Backup API (`conn.backup()`)**:
   - Captures 100% of committed transactions, including uncheckpointed WAL pages.
   - Restoring a backup captured via `conn.backup()` preserves all idempotency keys.
2. **Naive File Copy Vulnerability**:
   - Copying `.idempotency.db` alone without copying `.db-wal` or running a checkpoint leaves the backup database unreadable (`no such table: idempotency_records`).
3. **Restoration to New Directory**:
   - Fully tested: copying JSON state and `.idempotency.db` to a new directory preserves full deduplication; replaying historical keys is rejected.

---

## 6. Audit E — Safety Invariants Verification

- **Position Size Limit**: 1 micro-lot maximum (`APPROVED_MAX_PAPER_LOTS == 1`).
- **Oversized Order Rejections**: Both 2-lot and 484-lot orders are blocked with `PositionSizeViolation`.
- **LIVE Trading Barrier**: Calling `execute_live_order()` raises `PaperSafetyViolation`.
- **Historical Incident Intact**: Incident `PAPER_20261105_101000_LONG` (484 lots, -$7.9472) preserved intact in `data/paper_forward_24h_state.json`.

---

## 7. Audit Verdict & Remediation Roadmap

### Verdict: **CONDITIONAL PASS**
The durable SQLite idempotency registry successfully solves the key replay vulnerabilities of Phase 3.28 and guarantees deduplication across process crashes and concurrent workers.

### Prioritized Remediation Recommendations
1. **Atomic Dual-Store Save (High Priority)**:
   In `services/delta_paper_broker.py` (`open_simulated_trade`), assign `self.open_position = pos` before calling `commit_order_reservation()`.
2. **Power-Loss Durability (Medium Priority)**:
   Update `services/idempotency_registry.py` to use `PRAGMA synchronous = FULL;`.
3. **Explicit WAL Checkpoint on Shutdown & Backup (Medium Priority)**:
   Add `wal_checkpoint(TRUNCATE)` in broker teardown and backup utilities.
4. **Windows Handle Cleanup during Auto-Recovery (Low Priority)**:
   Ensure `conn.close()` is invoked in `_init_db()` before calling `os.replace` on corrupt database files.
