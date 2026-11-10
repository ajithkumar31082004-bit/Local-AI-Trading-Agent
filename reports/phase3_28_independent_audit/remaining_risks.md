# Phase 3.28 — Remaining Risks & Hardening Recommendations

This document outlines the remaining architectural risks identified during the Phase 3.28 independent audit of the `Local-AI-Trading-Agent` execution engine.

---

## 1. Identified Risks

### Risk 1: Idempotency Key Pruning Replay Vulnerability (Audit A)

- **Classification:** Architectural Idempotency Boundary
- **Component:** `services/delta_paper_broker.py` (`commit_order_reservation`, `reserve_order_slot`)
- **Condition:**
  `_processed_idempotency_keys` maintains a bounded cache of the last 1,000 processed keys. Committing an order beyond 1,000 keys triggers FIFO sorting and drops the oldest keys from memory and `state.json`.
- **Observed Behavior:**
  When an order is executed with `idempotency_key = "K"`, and subsequently 1,000 other orders are filled, key `"K"` is purged from the cache. If an upstream system or network retry replays a signal with `"K"` after the account is flat, `reserve_order_slot()` does not find `"K"` in `_processed_idempotency_keys` and does not check `self.closed_trades`. Consequently, the trade is accepted and executed again.
- **Severity:** Medium
- **Operational Reality:** In micro-lot forward testing (1 trade at a time), generating 1,000 trades spans several days to weeks. The 1-lot maximum position cap prevents oversized exposure. However, true idempotency requires permanent deduplication.
- **Proposed Minimal Remediation:**
  ```python
  # In reserve_order_slot():
  if clean_key in self._processed_idempotency_keys:
      return False, f"Duplicate order: idempotency key '{clean_key}' already processed in active cache", None

  if any(t.get("idempotency_key") == clean_key for t in self.closed_trades):
      return False, f"Duplicate order: idempotency key '{clean_key}' already executed in historical ledger", None
  ```
  Or maintain an append-only set of 32-byte SHA-256 hashes (`self._tombstones = set()`).

---

### Risk 2: Absence of Explicit `os.fsync()` Prior to Atomic Rename (Audit C)

- **Classification:** Storage Crash Durability
- **Component:** `services/delta_paper_broker.py` (`_save_state`)
- **Condition:**
  `_save_state()` uses `temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")` followed immediately by `temp_file.replace(self.state_file)`.
- **Observed Behavior:**
  Atomic replacement safely protects against user-space process crashes (`SIGTERM`, `SIGKILL`, `os._exit()`, or Python unhandled exceptions). However, `write_text` leaves data in the OS page cache. If the underlying EC2 instance experiences immediate hardware power loss or hypervisor crash before the dirty pages flush to disk, the directory entry could point to an incomplete file.
- **Severity:** Low
- **Existing Defense:** `_load_state()` includes a `try...except (json.JSONDecodeError, OSError)` fallback block that catches corrupt files and initializes a safe default baseline.
- **Proposed Minimal Remediation:**
  ```python
  with open(temp_file, "w", encoding="utf-8") as f:
      json.dump(payload, f, indent=2)
      f.flush()
      os.fsync(f.fileno())
  temp_file.replace(self.state_file)
  ```

---

### Risk 3: Single-Host Lock Boundary (Audit B)

- **Classification:** Distributed Concurrency Scope
- **Component:** `services/delta_paper_broker.py` (`CrossProcessReentrantLock`)
- **Condition:**
  The broker utilizes `portalocker.Lock`, which operates on OS kernel file locks (`LockFileEx` on Windows, `fcntl.flock` on Linux).
- **Observed Behavior:**
  Multi-process serialization on a single machine is 100% effective (verified by 5-process concurrent stress tests). However, file locks do not synchronize across separate physical servers or separate EC2 instances sharing an NFS/EFS mount.
- **Severity:** Informational (Current architecture is strictly single-instance).
- **Recommendation:** If future scaling introduces multiple worker nodes, migrate locking to Redis Redlock or PostgreSQL advisory locks.

---

## 2. Invariants Confirmed 100% Intact

| Safety Invariant | Implementation Mechanism | Status |
| :--- | :--- | :--- |
| **Strict 1-Micro-Lot Position Limit** | Central `position_size_guard.py` hard error on >1 lot | **VERIFIED** |
| **Silent Clamping Prohibited** | Deterministic `PositionSizeViolation` exception | **VERIFIED** |
| **LIVE Trading Hard Barrier** | `execute_live_order()` raises `PaperSafetyViolation` | **VERIFIED** |
| **Zero Exchange Network I/O** | All network calls blocked in tests; strictly local | **VERIFIED** |
| **Historical Incident Preservation** | `PAPER_20261105_101000_LONG` (484 lots, -$7.9472) intact | **VERIFIED** |
| **Cross-Process Serialization** | `portalocker` kernel lock protects all state mutations | **VERIFIED** |
| **Fencing Lease TTL Validation** | Expired, duplicate, and stale tokens rejected | **VERIFIED** |
