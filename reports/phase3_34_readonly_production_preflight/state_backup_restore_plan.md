# Phase 3.34 — State Integrity & Backup/Restore Architecture Plan

This document details the state storage architecture, WAL/SHM handling, online backup procedures, and restore verification protocols for the Local AI Trading Agent in PAPER trading mode.

---

## 1. State Storage Architecture & File Locations

| Component | Path / Location | Storage Engine | Purpose |
| :--- | :--- | :--- | :--- |
| **Broker State** | `data/paper_account_xautusd.json` | JSON (Atomic Replacement) | Active cash balance, open position, realized P&L, fees, slippage, and last order status. |
| **Idempotency Registry** | `data/paper_account_xautusd.idempotency.db` | SQLite (WAL mode, FULL sync) | Permanent durable record of all idempotency keys, execution tokens, and transaction statuses (`RESERVED`, `EXECUTING`, `COMMITTED`, `ROLLED_BACK`). |
| **WAL File** | `data/paper_account_xautusd.idempotency.db-wal` | SQLite Write-Ahead Log | Uncheckpointed transaction frames flushed via `fsync()` on commit. |
| **SHM File** | `data/paper_account_xautusd.idempotency.db-shm` | Shared-Memory Index | Read/write lock coordinator for WAL pages. |
| **Cross-Process Lock** | `data/paper_account_xautusd.lock` | OS File Lock (`portalocker`) | Reentrant mutual exclusion across threads and operating system processes. |
| **Historical Ledger** | `data/paper_forward_24h_state.json` | JSON (Read-Only Archive) | Permanent baseline record containing the historical 484-lot anomaly trade (`PAPER_20261105_101000_LONG`). |

---

## 2. Critical WAL & SHM File Handling Rules

> [!WARNING]
> **CRITICAL SQLite WAL BACKUP VULNERABILITY**:
> In SQLite WAL mode, uncheckpointed pages live exclusively in `.idempotency.db-wal`. Copying only `.idempotency.db` without `-wal` results in an incomplete or corrupt database replica where recent transactions are completely missing, or tables fail to initialize (`no such table: idempotency_records`).
>
> **MANDATORY POLICY**:
> Never perform naive file copies of an active `.db` file. Always use SQLite's Online Backup API (`conn.backup()`) or trigger an explicit `PRAGMA wal_checkpoint(TRUNCATE)` prior to copying.

---

## 3. Online Backup Procedure (Zero-Downtime)

The paper broker exposes a hardened online backup API (`DeltaPaperBroker.backup_state()`):

```python
# Programmatic Online Backup
broker = DeltaPaperBroker(...)
json_path, db_path = broker.backup_state("/path/to/backup/snapshot_20261111_0750")
```

### Execution Steps
1. **Acquire Cross-Process Lock**: Ensures no concurrent threads or processes mutate state during the snapshot.
2. **Flush JSON State**:
   - Write updated in-memory state to `state.json.tmp`.
   - Call `os.fsync()` to force physical drive write.
   - Atomically replace via `os.replace()`.
   - Copy `state.json` to backup destination.
3. **Flush & Copy SQLite via Online Backup API**:
   - Call `DurableIdempotencyRegistry.backup_to_file(dest_path)`.
   - Opens destination database connection.
   - Executes `src_conn.backup(dest_conn)`.
   - SQLite flushes active WAL pages and copies pages page-by-page into a single, self-contained, crash-consistent target database file.
   - Closes source and destination handles cleanly.

---

## 4. Restore Procedure

If recovery or restoration to a clean environment or prior snapshot is required:

### Step 1: Pre-Restoration Safeguards
1. Stop the paper trading service:
   ```bash
   sudo systemctl stop delta-paper-trading.service
   ```
2. Verify no lingering Python trading processes are holding locks:
   ```bash
   ps -ef | grep delta_paper | grep -v grep
   ```

### Step 2: Restore Files
1. Copy the restored `paper_account_xautusd.json` to the target `data/` directory.
2. Copy the self-contained restored `.idempotency.db` to `data/paper_account_xautusd.idempotency.db`.
3. If any stale `.idempotency.db-wal` or `.idempotency.db-shm` exist from a crashed run, remove them:
   ```bash
   rm -f data/paper_account_xautusd.idempotency.db-wal data/paper_account_xautusd.idempotency.db-shm
   ```

### Step 3: Startup Reconciliation & Verification
1. Initialize `DeltaPaperBroker`.
2. Broker loads JSON state, connects to SQLite, and automatically executes:
   - Fail-closed check: Ensures database is not empty if historical trades exist.
   - `sync_historical_keys()`: Backfills all historical closed trade keys into SQLite as `COMMITTED`.
   - `_recover_pending_execution_intents()`: Reconciles any in-flight `EXECUTING` intents.
3. Run integrity check:
   ```python
   with reg._get_connection() as conn:
       res = conn.execute("PRAGMA integrity_check;").fetchone()[0]
       assert res == "ok"
   ```
4. Verify cash balance equals $\text{Initial Equity} + \text{Net Realized P\&L}$.
