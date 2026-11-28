# Future Runtime Authorization Checklist: Public Feed Observation

**Document Purpose:** Mandatory gatekeeper checklist that MUST be reviewed, executed, and formally signed off by the human operator before authorizing any runtime execution against live Delta Exchange India public feeds.

**Current Phase Status:** TRANSPORT ADAPTER IMPLEMENTED & TESTED OFFLINE — RUNTIME EXECUTION **STRICTLY UNAUTHORIZED**.

---

## 1. Human Approval & Scope Verification

- [ ] **1.1 Formal Operator Authorization:** Formal written sign-off from project lead authorizing an initial, time-boxed **60-minute local public observation dry run**.
- [ ] **1.2 Target & Timeframe Confinement:** Verified target instrument is strictly `XAUTUSD` on `5m` execution and `15m` trend filter.
- [ ] **1.3 Execution Mode Confinement:** Confirmed execution mode is strictly **READ-ONLY SHADOW**, with zero order placement, zero simulated fills, and zero broker mutations.
- [ ] **1.4 Base Commit Lock:** Approved source commit locked to `feature/phase3-38-public-feed-transport` branch.

---

## 2. Environment Invariants & Tripwires

- [ ] **2.1 Live Trading Guard:** `LIVE_TRADING_ENABLED` is confirmed to be `"false"` in `.env` and host environment.
  ```bash
  grep "^LIVE_TRADING_ENABLED=" .env
  # Expected: LIVE_TRADING_ENABLED=false
  ```
- [ ] **2.2 Trading Mode Guard:** `TRADING_MODE` is confirmed to be `"PAPER"`.
  ```bash
  grep "^TRADING_MODE=" .env
  # Expected: TRADING_MODE=PAPER
  ```
- [ ] **2.3 Micro-Lot Guard:** `APPROVED_MAX_PAPER_LOTS == 1` constant verified in `risk/position_size_guard.py`.
- [ ] **2.4 Historical Incident Preservation:** Confirm `data/paper_forward_24h_state.json` contains `PAPER_20261105_101000_LONG` with 484 lots and net PnL -$7.9472.
  ```bash
  python -c "import json; d=json.load(open('data/paper_forward_24h_state.json')); t=next(x for x in d['closed_trades'] if x['trade_id']=='PAPER_20261105_101000_LONG'); assert t['lots']==484 and t['net_pnl']==-7.9472; print('HISTORICAL RECORD VERIFIED')"
  ```

---

## 3. Network & Transport Isolation

- [ ] **3.1 Zero Private Credentials:** Execution environment contains zero API keys or secrets (`DELTA_API_KEY`, `DELTA_API_SECRET`).
- [ ] **3.2 Public-Only Endpoints:** Network traffic restricted to public endpoints:
  - REST: `https://api.india.delta.exchange`
  - WebSocket: `wss://socket.india.delta.exchange`
- [ ] **3.3 No Order Endpoints:** Zero requests targeting `/v2/orders`, `/v2/positions`, or authenticated paths.
- [ ] **3.4 Configured Timeouts:** Connect timeout $\le 10.0\text{s}$, read timeout $\le 30.0\text{s}$, max retries $\le 5$, max backoff $\le 60.0\text{s}$.

---

## 4. Host OS & Resource Verification

- [ ] **4.1 System Clock Synchronization:** Host clock synchronized via NTP with under 500ms offset.
- [ ] **4.2 Disk Space Availability:** Minimum 1.0 GB free disk space in volume hosting output reports.
  ```bash
  python -c "import shutil; print('Free GB:', shutil.disk_usage('.').free / (1024**3))"
  ```
- [ ] **4.3 Service Isolation:** Trading daemon (`trading_daemon.service`) confirmed stopped/inactive if deployed to server.
- [ ] **4.4 Write Path Sandboxing:** Output directory locked to `reports/shadow_validation/`.

---

## 5. Automated Pre-Flight Test Pass

- [ ] **5.1 45-Test Shadow Suite Pass:** All 45 shadow observer and transport tests pass:
  ```bash
  pytest tests/test_phase3_38_transport_adapter.py tests/test_phase3_36_independent_shadow_audit.py tests/test_phase3_35_shadow_validation.py -v
  # Expected: 45 passed
  ```
- [ ] **5.2 Full Repository Regression Pass:** Full test suite passes without regressions:
  ```bash
  pytest -q
  # Expected: 480 passed
  ```

---

## 6. Formal Sign-Off Protocol

| Role | Name | Status | Timestamp |
| :--- | :--- | :---: | :---: |
| **Audit Agent** | Antigravity Phase 3.38 | **APPROVED (OFFLINE VERIFIED)** | 2026-11-11 |
| **Project Lead / Operator** | *[Pending Human Review]* | *PENDING* | *PENDING* |

> [!CAUTION]
> No public network socket, REST query, or background observer runner may be started until this checklist is formally signed off by the human operator.
