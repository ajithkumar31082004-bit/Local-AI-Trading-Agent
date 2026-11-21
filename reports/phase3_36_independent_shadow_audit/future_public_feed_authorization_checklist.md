# Future Public Feed Authorization Checklist

**Document Purpose:** Mandatory gatekeeper checklist that MUST be completed and formally signed off by the human operator before authorizing any runtime execution of the shadow observer against public Delta Exchange India feeds.

**Current Phase Status:** PREPARATION & AUDIT COMPLETE — RUNTIME EXECUTION **NOT AUTHORIZED**.

---

## 1. Human Approval & Scope Verification

- [ ] **1.1 Explicit Human Authorization:** Formal written authorization received from the project lead specifying exact start time, duration, and target instrument (`XAUTUSD`).
- [ ] **1.2 Mode Confinement:** Confirmed that execution mode is strictly **READ-ONLY SHADOW OBSERVER**, with zero order placement, zero position opening, and zero account mutation.
- [ ] **1.3 Base Commit Lock:** Approved source commit locked to `feature/phase3-36-independent-shadow-audit` branch.

---

## 2. Environment Invariants & Safety Tripwires

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

## 3. Network & Credential Isolation

- [ ] **3.1 Zero Private Credentials:** Shadow observer execution environment does NOT load `DELTA_API_KEY` or `DELTA_API_SECRET`.
- [ ] **3.2 Public-Only Endpoints:** Network traffic restricted to public endpoints:
  - REST: `https://api.india.delta.exchange`
  - WebSocket: `wss://socket.india.delta.exchange`
- [ ] **3.3 No Order Endpoints:** Public market reader contains zero URLs targeting `/v2/orders`, `/v2/positions`, or authenticated paths.

---

## 4. Host OS & System Readiness

- [ ] **4.1 System Clock Synchronization:** Host OS NTP daemon (`chrony` or `systemd-timesyncd`) synchronized with under 500ms offset.
  ```bash
  chronyc tracking || timedatectl status
  ```
- [ ] **4.2 Disk Space Availability:** Minimum 1 GB free disk space in partition hosting `reports/shadow_validation/`.
- [ ] **4.3 Process Isolation:** Trading daemon (`trading_daemon.service`) is STOPPED and confirmed inactive.
  ```bash
  systemctl is-active trading_daemon.service || true
  # Expected: inactive
  ```
- [ ] **4.4 Write Path Confinement:** Output directory locked to `reports/shadow_validation/` or designated test folder.

---

## 5. Pre-Run Automated Test Verification

- [ ] **5.1 28-Test Shadow Suite Pass:** All 28 offline shadow validation and adversarial audit tests pass:
  ```bash
  pytest tests/test_phase3_35_shadow_validation.py tests/test_phase3_36_independent_shadow_audit.py -v
  # Expected: 28 passed
  ```
- [ ] **5.2 Full Repository Regression Pass:** Full test suite passes without regressions:
  ```bash
  pytest -q
  # Expected: 463 passed
  ```

---

## 6. Sign-Off Authorization Block

| Role | Name | Status | Date & Timestamp |
| :--- | :--- | :---: | :---: |
| **Audit Agent** | Antigravity Phase 3.36 | **APPROVED (AUDIT PASS)** | 2026-11-11 |
| **Project Lead / Operator** | *[Pending Human Review]* | *PENDING* | *PENDING* |

> [!CAUTION]
> No background service, daemon, or public WebSocket connection may be initiated until this checklist is formally signed off by the human operator.
