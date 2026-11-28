# Phase 3.30 — SQLite Durability & Recovery: Remaining Risks & Edge Cases

## Executive Summary

Phase 3.30 conducted an independent forensic audit of the SQLite durability and recovery implementation introduced in Phase 3.29. The evaluation revealed that while normal execution, concurrent multi-process deduplication, and FIFO pruning replays are strictly prevented, several architectural and durability edge cases remain.

---

## Identified Risks & Architectural Gaps

### 1. Dual-Store Commit Asynchrony (Partial Execution Gap)
- **Component**: `services/delta_paper_broker.py` (`open_simulated_trade`)
- **Severity**: MEDIUM-HIGH
- **Description**: The broker updates two distinct persistence stores sequentially:
  1. `self._idempotency_registry.commit_reservation(...)` marks the key as `COMMITTED` in SQLite and invokes `self._save_state()` where entry fees are deducted from cash. At this instant, `self.open_position` is still `None`.
  2. Subsequently, `self.open_position = pos` is assigned in memory and saved in a second `self._save_state()` call.
- **Impact under Crash**:
  If a crash, SIGKILL, or power loss occurs between step 1 and step 2:
  - On restart, SQLite contains a durable `COMMITTED` record.
  - JSON state reflects deducted entry fee, but `open_position` remains `None`.
  - Re-attempting the order with the same idempotency key is rejected as duplicate by SQLite.
  - The trade is effectively lost, causing phantom cash deduction without an open position.
- **Proposed Remediation**:
  Assign `self.open_position = pos` prior to invoking `commit_order_reservation()`, ensuring that the single atomic JSON state write persists both the position and the fee deduction simultaneously. Long-term, all broker state should be migrated to SQLite in a single transaction.

---

### 2. Host Power-Loss Durability Tradeoff (`PRAGMA synchronous = NORMAL`)
- **Component**: `services/idempotency_registry.py` (`_get_connection`)
- **Severity**: MEDIUM
- **Description**: The connection initializes with `PRAGMA synchronous = NORMAL;`. In WAL mode, `NORMAL` guarantees durability against application and process crashes because the operating system retains dirty buffer cache pages. However, it does **not** call `fsync()` on each transaction commit.
- **Impact under Power Loss**:
  If the host server experiences an abrupt power loss, hypervisor crash, or hard kernel reboot, uncheckpointed WAL frames lingering in OS page cache may be lost.
- **Proposed Remediation**:
  Switch to `PRAGMA synchronous = FULL;`. In a paper trading environment with low transaction volume (1 order per 5m-15m candle bar), the 1-5ms `fsync()` latency overhead is negligible and eliminates power-loss data loss risk.

---

### 3. Windows File-Lock Leak during Corrupt Database Auto-Recovery
- **Component**: `services/idempotency_registry.py` (`_init_db`)
- **Severity**: LOW (Fail-closed invariant is maintained)
- **Description**: When `_get_connection()` encounters a corrupted database file, `conn.execute("PRAGMA journal_mode = WAL;")` raises `sqlite3.DatabaseError: file is not a database`. The connection object is not explicitly closed before catching the exception. On Windows, Python's SQLite wrapper holds an OS file lock on the open file handle. Consequently, `self.db_path.replace(corrupt_backup)` fails with `PermissionError: [WinError 32]`.
- **Impact**:
  The broker raises `IdempotencyRegistryError: Database initialization and recovery failed`, failing closed and halting startup. While failing closed is the safe behavior, the intended auto-recovery backup cannot proceed on Windows without explicitly closing `conn`.
- **Proposed Remediation**:
  Wrap connection creation in a `try...finally` block to ensure `conn.close()` is called before `os.replace`.

---

### 4. Naive File Copy Backup Vulnerability
- **Component**: Operations & Deployment
- **Severity**: LOW-MEDIUM
- **Description**: In SQLite WAL mode, committed transactions reside in `.idempotency.db-wal` until a checkpoint occurs. A file backup script copying only `.idempotency.db` captures an empty or outdated schema.
- **Impact**:
  Restoring from a `.db`-only snapshot fails with `sqlite3.OperationalError: no such table: idempotency_records`.
- **Proposed Remediation**:
  Use the official SQLite Online Backup API (`src_conn.backup(dest_conn)`) or execute `PRAGMA wal_checkpoint(TRUNCATE);` before file snapshotting.
