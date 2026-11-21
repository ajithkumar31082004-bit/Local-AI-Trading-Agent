# Phase 3.34 — Production Deployment Rollback Plan

This document establishes the operational rollback protocol in the event that a future, authorized deployment of commit `2807757` encounters unexpected failures or regression behaviors.

> [!IMPORTANT]
> **READ-ONLY AUDIT NOTICE**:
> This rollback plan is documented for operational readiness only. Do NOT execute this rollback during Phase 3.34.

---

## 1. Commit Baseline Reference

| Environment | Current Active Commit | Role / Status |
| :--- | :--- | :--- |
| **EC2 Server (Active)** | `a7d7c9a` | Stage 1 Read-Only Observer Baseline |
| **Previous Tested Paper Base** | `d4c2498` | Phase 3.25 Controlled Validation Baseline |
| **Candidate Release Commit** | `2807757` | Phase 3.33 Operational Readiness Complete |

---

## 2. Emergency Stop Conditions (Tripwires)

The daemon must be stopped immediately (`systemctl stop delta-paper-trading.service`) if ANY of the following occur:

1. **Position Sizing Anomaly**: Any order requested or filled with $>1$ micro-lot (0.001 XAUT).
2. **LIVE Trading Attempt**: Any invocation attempting to place live exchange orders or any configuration where `LIVE_TRADING_ENABLED=true`.
3. **Database Integrity Fault**: Any occurrence of `IdempotencyRegistryError`, SQLite disk I/O error, or WAL corruption.
4. **Accounting Drift**: Any discrepancy between calculated equity and reported trade P&L ($\Delta \text{Cash} \neq \text{Net P\&L}$).
5. **Recovery Oscillation**: The daemon restarting or entering crash-recovery loops repeatedly within a 5-minute window.

---

## 3. Step-by-Step Rollback Execution Procedure

If an authorized deployment must be reverted:

### Phase 1: Service Halt
```bash
# 1. Stop the trading daemon immediately
sudo systemctl stop delta-paper-trading.service

# 2. Confirm the process has terminated
ps -ef | grep delta_paper | grep -v grep
```

### Phase 2: Application Code Rollback
```bash
cd /home/ubuntu/Local-AI-Trading-Agent

# 1. Stash or isolate any runtime logs
git status --porcelain

# 2. Revert working tree to known-good baseline commit
git checkout a7d7c9a

# 3. Verify HEAD commit
git log -1 --oneline
# Expected output: a7d7c9a docs(stage1): capture 1-hour EC2 public market data observation report and telemetry
```

### Phase 3: State & Database Restoration
```bash
# 1. Restore pre-deployment database and JSON state from verified backup
cp /path/to/pre_deploy_backup/paper_account_xautusd.json data/paper_account_xautusd.json
cp /path/to/pre_deploy_backup/paper_account_xautusd.idempotency.db data/paper_account_xautusd.idempotency.db

# 2. Clean temporary WAL / SHM files
rm -f data/paper_account_xautusd.idempotency.db-wal data/paper_account_xautusd.idempotency.db-shm

# 3. Confirm historical forward state file integrity
sha256sum data/paper_forward_24h_state.json
# Expected hash: dad10844b0e77ed19db058eec1e541aa858eb8e2d3ea1ada66d83a69390ced25
```

### Phase 4: Service Health Verification
```bash
# 1. Verify monitoring API is healthy
curl -s http://127.0.0.1:8088/health | grep '"status":"ok"'

# 2. Verify dashboard status
curl -s http://127.0.0.1:8501/ | grep -i "streamlit"

# 3. Verify trading service remains inactive until explicit human re-authorization
systemctl status delta-paper-trading.service --no-pager
```
