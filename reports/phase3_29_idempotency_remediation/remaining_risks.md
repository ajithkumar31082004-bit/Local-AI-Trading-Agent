# Phase 3.29 — Remaining Risks & Architecture Assessment

This document provides a comprehensive operational risk review following the implementation and dynamic verification of the permanent idempotency remediation in Phase 3.29.

---

## 1. Status of Previously Identified Vulnerabilities

| Risk Identifier | Prior Status (Phase 3.28) | Phase 3.29 Remediation | Current Status |
| :--- | :--- | :--- | :--- |
| **Risk 1: 1,000-Key Pruning Replay** | Vulnerable (Keys evicted from memory could be replayed once flat) | Implemented `DurableIdempotencyRegistry` (SQLite WAL) with immutable `COMMITTED` primary keys | **RESOLVED** |
| **Risk 2: Storage fsync Durability** | Low Risk (atomic `replace()` without explicit `os.fsync()`) | Added `f.flush()` and `os.fsync(f.fileno())` prior to atomic file rename | **RESOLVED** |
| **Risk 3: Corrupt Storage Handling** | Catch-and-reset fallback | Auto-recovery isolates damaged DB to `.corrupt.<ts>` and backfills from historical ledgers | **HARDENED** |
| **Risk 4: Oversized Position Execution** | Blocked | 1-micro-lot central safety guard deterministically rejects any order >1 lot | **VERIFIED** |
| **Risk 5: Historical Incident Integrity** | Preserved | `PAPER_20261105_101000_LONG` (484 lots, -$7.9472) intact | **VERIFIED** |

---

## 2. Operational Considerations & Remaining Boundaries

### Boundary 1: Single-Host Lock Scope
- **Classification:** Distributed Infrastructure Scope
- **Description:** `portalocker` kernel locks and SQLite databases operate on local operating system file handles.
- **Assessment:** For the current single-instance EC2 server deployment, this is optimal, zero-dependency, and extremely fast (<1ms transaction time). If the trading architecture ever evolves into a multi-server active-active cluster, locking must be migrated to a distributed lock manager (e.g., Redis Redlock or Postgres advisory locks).
- **Current Severity:** Informational (Current setup is single-instance).

### Boundary 2: Long-Term SQLite Registry Growth
- **Classification:** Disk Footprint Maintenance
- **Description:** Each executed trade generates one record in `idempotency_records` (~200 bytes per record).
- **Assessment:** 10,000 trades consume approximately 2.5 MB of disk space. Even after 1,000,000 trades, the database would only require ~250 MB.
- **Recommendation:** No pruning is necessary or desirable for `COMMITTED` records, as they serve as permanent tombstones preventing replay. If desired in future years, a background job can prune non-executed records (`status = 'RELEASED'` or `status = 'EXPIRED'`) older than 90 days.

### Boundary 3: Public Market Data Network Fluctuations
- **Classification:** Exchange Connectivity
- **Description:** Delta Exchange India public WebSocket feeds occasionally disconnect or experience heartbeat timeouts.
- **Assessment:** The market data feed includes automatic REST polling fallback and heartbeat monitors.

---

## 3. Core Safety Invariants Confirmed

| Invariant | Value / Mechanism | Verification Status |
| :--- | :--- | :--- |
| **Trading Mode** | `STRICT PAPER ONLY` | **ENFORCED** |
| **Live Trading Barrier** | `execute_live_order()` raises `PaperSafetyViolation` | **ENFORCED** |
| **Position Sizing Ceiling** | Maximum 1 micro-lot (`0.001 XAUT`) | **ENFORCED** |
| **Oversized Order Action** | Deterministic `PositionSizeViolation` (No clamping) | **ENFORCED** |
| **Idempotency Durability** | Unbounded SQLite WAL primary key unique constraint | **ENFORCED** |
| **Historical Incident** | Trade `PAPER_20261105_101000_LONG` unchanged | **ENFORCED** |
