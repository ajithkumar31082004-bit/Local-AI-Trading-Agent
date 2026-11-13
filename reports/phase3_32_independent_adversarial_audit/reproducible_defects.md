# Phase 3.32 — Reproducible Defects & Remediation Status

This document catalogs the findings, vulnerability tests, and defect verifications performed during the Phase 3.32 Independent Adversarial Broker Audit.

---

## 1. Prior Findings Verification Status (from Phase 3.30 & 3.31)

| Defect ID | Description | Pre-Remediation Behavior | Current Status in Phase 3.32 | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **DEF-330-01** | Dual-Store Asynchronous Crash Window (Boundary C.4) | SQLite marked `COMMITTED` before JSON persisted `open_position`. Process crash permanently lost order while locking out retry and stranding deducted fees. | **RESOLVED & VERIFIED** | `test_boundary_2_after_intent_insertion`, `test_boundary_4_after_json_fsync_before_atomic_replace` |
| **DEF-330-02** | Power-Loss Durability Gap (`PRAGMA synchronous = NORMAL`) | SQLite in `NORMAL` synchronous mode relied on OS page cache; transactions were vulnerable to abrupt power loss or hypervisor termination. | **RESOLVED & VERIFIED** | `test_inspect_sqlite_pragmas_and_connections` confirms `synchronous = 2` (`FULL`). |
| **DEF-330-03** | Naive File-Copy Backup Missing Dirty WAL Frames | Naive file copying of `.idempotency.db` missed dirty WAL frames, resulting in empty or corrupt restored registries. | **RESOLVED & VERIFIED** | `test_backup_during_active_writes` verifies `conn.backup()` captures all WAL frames online. |
| **DEF-330-04** | Fail-Open Risk on Complete Data Loss | Corrupt SQLite DB accompanied by missing/empty JSON state would initialize an empty database, opening a window for duplicate executions. | **RESOLVED & VERIFIED** | `test_fail_closed_when_history_unrecoverable` verifies broker raises `IdempotencyRegistryError` and halts. |

---

## 2. Phase 3.32 Adversarial Findings & Hardening

### Defect ID: DEF-332-01
- **Severity**: Low (Windows File-Handle Hygiene)
- **Title**: Python `sqlite3.Connection` Context Manager Non-Closure on Windows
- **Component**: `services/idempotency_registry.py` (`checkpoint()`, `backup_to_file()`)
- **Observed Behavior**:
  In standard Python, `with conn:` only manages the SQL transaction (`BEGIN ... COMMIT / ROLLBACK`); it does not close the connection file descriptor. In Windows, leaving connection handles open prevents atomic file replacement or deletion (`[WinError 32]`) until Python garbage collection cycles run.
- **Root Cause**:
  `reg.checkpoint()` and `reg.backup_to_file()` utilized `with self._get_connection() as conn:` without explicit `conn.close()` in `finally` blocks.
- **Remediation**:
  Updated `checkpoint()` and `backup_to_file()` in `services/idempotency_registry.py` to wrap connection lifecycles in explicit `try ... finally: conn.close()` blocks, and introduced `reg.close()` with explicit TRUNCATE checkpoint and garbage collection triggers.
- **Verification**:
  `test_inspect_connection_closure_safety` executes clean file rename immediately following `reg.close()`, passing with zero `PermissionError`.

---

## 3. Summary of Outstanding Reproducible Defects

- **Critical Defects**: 0
- **High Defects**: 0
- **Medium Defects**: 0
- **Low Defects**: 0
- **Total Outstanding Defects**: **0**

All 21 independent adversarial tests and all 435 full regression tests pass cleanly. Zero defect conditions remain reproducible in the repository.
