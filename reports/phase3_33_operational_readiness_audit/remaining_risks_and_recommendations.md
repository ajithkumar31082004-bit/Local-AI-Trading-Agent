# Phase 3.33 — Remaining Operational Risks & Deployment Recommendations

This document details residual operational risks, environmental considerations, and recommended preflight procedures prior to any subsequent human authorization for extended paper testing.

---

## 1. Operational Observations & Residual Risks

### 1.1 Process Termination Realism in Testing vs. EC2 Runtime
- **Observation**: Crash testing in unit suites injects ungraceful disk state (uncommitted SQLite rows, corrupt temporary files, un-renamed atomic files) followed by clean Python object initialization.
- **Risk Assessment**: **Very Low**. The state reconciliation logic (`_recover_pending_execution_intents`) executes unconditionally during broker startup `_load_state()`, operating on whatever state exists on disk. Whether the prior process died via SIGKILL, power cut, or simulated termination, the resulting disk state falls into one of the 9 tested boundaries.
- **Recommendation**: During initial daemon execution on EC2, run under systemd with standard restart policies (`Restart=on-failure`, `RestartSec=5s`).

### 1.2 SQLite Connection & WAL Hygiene on Linux
- **Observation**: On Windows, DEF-332-01 was resolved by explicitly closing connection handles in `checkpoint()` and `backup_to_file()`. On Linux (Ubuntu), file locking is advisory, but resource cleanup remains best practice.
- **Risk Assessment**: **Negligible**. Connection teardown has been verified deterministic across both platforms.
- **Recommendation**: Periodically execute `broker.checkpoint_db("TRUNCATE")` during low-activity intervals or upon daemon shutdown to keep WAL file sizes minimal.

### 1.3 State Backup Retention
- **Observation**: `DeltaPaperBroker.backup_state()` uses SQLite Online Backup API to safely copy state while running.
- **Risk Assessment**: **Low**. Without pruning, daily backup snapshots could accumulate over months.
- **Recommendation**: Maintain a rolling retention window of the last 14 daily backup snapshots.

---

## 2. Preflight Checklist for Future Human Decision

Before any human decision to launch forward paper validation on EC2, the following preflight checks must be verified:

1. **Git Isolation**: Ensure EC2 working tree is completely clean and checked out to the approved release tag or commit.
2. **Environment Validation**: Confirm `TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`, and `APPROVED_MAX_PAPER_LOTS=1` in environment configuration.
3. **Database Health**: Verify `.idempotency.db` initializes with `PRAGMA synchronous = FULL;` and WAL mode.
4. **Historical Ledger**: Confirm historical ledger `data/paper_forward_24h_state.json` retains `PAPER_20261105_101000_LONG` without alteration.
5. **Human Authorization**: Zero automated daemon launches are permitted without explicit user instruction.
