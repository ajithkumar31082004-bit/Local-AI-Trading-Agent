# Phase 4.1 — GitHub Actions CI Verification & systemd Service Engineering Report

**Document ID:** `REP-PHASE-4-1-SYSTEMD-001`  
**Date:** 2026-11-12  
**Target Instrument:** `XAUTUSD` Perpetual Futures (Delta Exchange India, Product ID `131253`)  
**Active Working Branch:** `feature/phase4-1-systemd-services`  
**Source Commit (Phase 4.0):** [`c323ecdcc859b1ef384328babca585a135a9c14a`](file:///C:/Users/ajith/Downloads/Local_AI_Agent)  
**Phase 4.1 Commit SHA:** `bc9a4b7a151b1f8ef672e38c207da615ebcc1ef5` (short: `bc9a4b7`)  
**Lead Architect:** Senior Python Quantitative Trading Systems Architect & AWS DevOps Engineer  
**Operational Status:** **OFFLINE VALIDATED & COMMITTED (ZERO EC2 OR EXCHANGE CONNECTIONS)**

---

## Executive Summary

Phase 4.1 accomplishes two primary engineering objectives:
1. **GitHub Actions Real-Environment Verification:** Empirically validated that the Phase 4.0 CI/CD settings attribute bug fix (`settings.mode` and `settings.trading_mode` alias) and production dependency remediation (`requirements.txt`) pass in GitHub's hosted runner environment (`ubuntu-latest`, Python 3.11).
2. **Hardened systemd Service Engineering:** Designed, implemented, and comprehensively validated offline systemd unit files for the core trading daemon (`scripts/delta-trading-daemon.service`) and read-only shadow observer (`scripts/delta-shadow-observer.service`), matching the architectural standard established by `scripts/delta-monitoring-api.service`.

All safety constraints were strictly preserved throughout:
- `TRADING_MODE=PAPER`
- `LIVE_TRADING_ENABLED=false`
- `APPROVED_MAX_PAPER_LOTS == 1`
- Zero live EC2 connections or remote systemd service activations.
- Zero network requests to Delta Exchange live REST or WebSocket endpoints.
- Historical defect records (notably `PAPER_20261105_101000_LONG` 484-lot incident) remain fully preserved and untouched.

---

## 1. Task A: Phase 4.0 GitHub Actions CI Verification Evidence

### 1.1 Trigger & Environment Details
The Phase 4.0 remediated commit `c323ecdcc859b1ef384328babca585a135a9c14a` on branch `feature/phase4-ci-dependency-fix` was pushed to origin and triggered via GitHub Actions `workflow_dispatch`.

- **Workflow File:** [`.github/workflows/deploy_and_track.yml`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/.github/workflows/deploy_and_track.yml)
- **Workflow Run URL:** [https://github.com/ajithkumar31082004-bit/Local-AI-Trading-Agent/actions/runs/43137090321](https://github.com/ajithkumar31082004-bit/Local-AI-Trading-Agent/actions/runs/43137090321)
- **Workflow Run ID:** `43137090321`
- **Target Branch:** `feature/phase4-ci-dependency-fix`
- **Commit SHA:** `c323ecdcc859b1ef384328babca585a135a9c14a`
- **Runner Environment:** GitHub-hosted `ubuntu-26.04.1 LTS` (`ubuntu-latest`), Python `3.11.18`

### 1.2 CI Job Breakdown and Verification Results

| Job Name | Job ID | Result | Duration | Key Verification Finding |
|---|:---:|:---:|:---:|---|
| **Run Automated Tests and Safety Checks** | `130914923394` | **SUCCESS (PASSED)** | 1m 28s | All 41 core tests passed. Preflight safety check executed successfully with zero `AttributeError`. |
| **Security and Credential Leak Scan** | `130914923635` | **FAILED** | 4s | Flagged synthetic mock tokens in test files (`test_monitoring_api.py:166` and `test_phase3_42_safety_audit.py:117`). |
| **Record CI Build Verification** | `130915477422` | **SKIPPED** | 0s | Dependent on `security_audit` success. |

### 1.3 Step Execution Log: `Verify Strict PAPER Trading Safety`
In the previously failing CI run (`42069130768`), this step crashed fatally with:
```
assert settings.trading_mode == 'PAPER', 'FATAL: TRADING_MODE must be PAPER'
AttributeError: 'ORBSettings' object has no attribute 'trading_mode'
```
In run `43137090321` on commit `c323ecd`, the exact GitHub Actions runner log proves the fix:
```
Run python -c "
from config.orb_settings import settings
assert settings.mode == 'PAPER', 'FATAL: TRADING_MODE must be PAPER'
assert settings.trading_mode == 'PAPER', 'FATAL: TRADING_MODE alias must be PAPER'
assert settings.live_trading_enabled is False, 'FATAL: LIVE_TRADING_ENABLED must be False'
print('Safety verification PASSED: Strict PAPER mode enforced.')
"
Safety verification PASSED: Strict PAPER mode enforced.
```
**Conclusion:** `GAP-03` (settings attribute mismatch) is 100% resolved in the real GitHub Actions cloud runner environment.

---

## 2. Task B: Production Daemon & Observer Entrypoint Inspection

A comprehensive audit was performed across all entry points, process lifecycles, and environment requirements:

### 2.1 Core Trading Daemon (`delta-trading-daemon`)
- **Entry Point:** [`cli.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/cli.py) subcommand `paper-live`
- **Execution Command:**
  ```bash
  /home/ubuntu/Local-AI-Trading-Agent/.venv/bin/python cli.py paper-live --strategy PORTFOLIO
  ```
- **Required Environment Variables:**
  ```ini
  TRADING_MODE=PAPER
  LIVE_TRADING_ENABLED=false
  PAPER_TRADING_ACKNOWLEDGED=YES
  LIVE_TRADING_ACKNOWLEDGED=NO
  APPROVED_MAX_PAPER_LOTS=1
  DELTA_SYMBOL=XAUTUSD
  DELTA_PRODUCT_ID=131253
  DELTA_WS_URL=wss://socket.india.delta.exchange
  ```
- **Shutdown Handling:** The main loop wraps execution in a `try...except KeyboardInterrupt:` block which calls `bot.stop()`, cleanly disconnecting WebSockets and preserving simulated balances. Configured in systemd via `KillSignal=SIGINT` and `TimeoutStopSec=30`.
- **Safety Gates:**
  1. `ExecStartPre` pre-start verification asserting `settings.mode == 'PAPER'`, `settings.trading_mode == 'PAPER'`, and `settings.live_trading_enabled is False`.
  2. In-code runtime guard at [`cli.py:361`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/cli.py#L361): `if settings.mode != "PAPER": raise SystemExit(...)`.

### 2.2 Read-Only Shadow Observer (`delta-shadow-observer`)
- **Entry Point:** Dedicated runner script [`scripts/run_shadow_observer.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/scripts/run_shadow_observer.py) invoking [`services.shadow_observer.dry_run_runner.ShadowDryRunRunner`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/dry_run_runner.py)
- **Execution Command:**
  ```bash
  /home/ubuntu/Local-AI-Trading-Agent/.venv/bin/python scripts/run_shadow_observer.py
  ```
- **Required Environment Variables:**
  ```ini
  TRADING_MODE=PAPER
  LIVE_TRADING_ENABLED=false
  DELTA_SYMBOL=XAUTUSD
  ```
- **Shutdown Handling:** Signal traps for `SIGINT` and `SIGTERM` invoke `runner.request_stop()` and `runner.save_readiness_report()`. Configured in systemd via `KillSignal=SIGTERM` and `TimeoutStopSec=30`.
- **Safety Gates:**
  1. 12 Runtime Preflight Safety Gates (`execute_preflight_safety_gates`).
  2. 10 Automatic Stop Conditions (`evaluate_stop_conditions`).
  3. Absolute absence of order placement capability (`place_order`, `submit_order`).

### 2.3 Monitoring API Service (`delta-monitoring-api`)
- **Entry Point:** Existing file [`scripts/delta-monitoring-api.service`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/scripts/delta-monitoring-api.service)
- **Execution Command:**
  ```bash
  /home/ubuntu/Local-AI-Trading-Agent/.venv/bin/uvicorn services.monitoring_api:app --host 127.0.0.1 --port 8088 --workers 1
  ```
- **Configuration:** Strictly binds to `127.0.0.1` (localhost only), runs under `User=ubuntu`, and enforces strict filesystem sandboxing.

---

## 3. Task C: systemd Service Unit Definitions

### 3.1 Trading Daemon Service Unit
Location: [`scripts/delta-trading-daemon.service`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/scripts/delta-trading-daemon.service)

```ini
[Unit]
Description=Delta Exchange India Paper Trading Daemon (XAUTUSD Perpetual Futures)
Documentation=https://github.com/ajithkumar31082004-bit/Local-AI-Trading-Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/Local-AI-Trading-Agent
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/home/ubuntu/Local-AI-Trading-Agent/.env

# Strict Pre-Start Safety Gate: Refuses to start if TRADING_MODE is not PAPER or LIVE_TRADING_ENABLED is true
ExecStartPre=/home/ubuntu/Local-AI-Trading-Agent/.venv/bin/python -c "\
import os, sys; \
from config.orb_settings import settings; \
assert settings.mode == 'PAPER', 'FATAL: TRADING_MODE must be PAPER'; \
assert settings.trading_mode == 'PAPER', 'FATAL: trading_mode alias must be PAPER'; \
assert settings.live_trading_enabled is False, 'FATAL: LIVE_TRADING_ENABLED must be False'; \
assert os.getenv('TRADING_MODE', 'PAPER') == 'PAPER', 'FATAL: Environment TRADING_MODE != PAPER'; \
assert os.getenv('LIVE_TRADING_ENABLED', 'false').lower() == 'false', 'FATAL: Environment LIVE_TRADING_ENABLED is true'; \
print('Pre-start safety verification PASSED: Strict PAPER mode enforced.')"

# Production Entry Point: Continuous paper trading runloop
ExecStart=/home/ubuntu/Local-AI-Trading-Agent/.venv/bin/python cli.py paper-live --strategy PORTFOLIO

# Graceful Shutdown: Sends SIGINT so bot.stop() cleanly closes WebSockets and persists simulated state
KillSignal=SIGINT
TimeoutStopSec=30
FinalKillSignal=SIGKILL

# Controlled Restart Limits: On-failure restart with 10s backoff, bounded to 3 restarts per 300s window
Restart=on-failure
RestartSec=10

StandardOutput=append:/home/ubuntu/Local-AI-Trading-Agent/data/trading_daemon.log
StandardError=append:/home/ubuntu/Local-AI-Trading-Agent/data/trading_daemon.log

# Security Sandboxing & Least Privilege Restrictions
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/Local-AI-Trading-Agent/data /home/ubuntu/Local-AI-Trading-Agent/logs
ReadOnlyPaths=/home/ubuntu/Local-AI-Trading-Agent
NoNewPrivileges=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictRealtime=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
```

### 3.2 Shadow Observer Service Unit
Location: [`scripts/delta-shadow-observer.service`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/scripts/delta-shadow-observer.service)

```ini
[Unit]
Description=Delta Exchange India Shadow Observer (Read-Only Public Telemetry & Strategy Evaluation)
Documentation=https://github.com/ajithkumar31082004-bit/Local-AI-Trading-Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/Local-AI-Trading-Agent
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/home/ubuntu/Local-AI-Trading-Agent/.env

# Pre-Start Safety Gate: Verify read-only observer isolation and absence of order placement capability
ExecStartPre=/home/ubuntu/Local-AI-Trading-Agent/.venv/bin/python -c "\
import os, sys; \
from services.shadow_observer.observer_safety_guard import verify_observer_isolation; \
from services.shadow_observer.dry_run_runner import ShadowDryRunRunner; \
verify_observer_isolation(); \
runner = ShadowDryRunRunner(); \
assert not hasattr(runner, 'place_order'), 'FATAL: Shadow runner has order placement capability'; \
assert not hasattr(runner, 'submit_order'), 'FATAL: Shadow runner has order submission capability'; \
assert os.getenv('LIVE_TRADING_ENABLED', 'false').lower() == 'false', 'FATAL: LIVE_TRADING_ENABLED is true'; \
print('Pre-start verification PASSED: Strict read-only observer isolation verified.')"

# Production Entry Point: Dedicated read-only shadow observer runner
ExecStart=/home/ubuntu/Local-AI-Trading-Agent/.venv/bin/python scripts/run_shadow_observer.py

# Graceful Shutdown: Sends SIGTERM so runner gracefully closes public streams and writes telemetry report
KillSignal=SIGTERM
TimeoutStopSec=30
FinalKillSignal=SIGKILL

# Controlled Restart Limits: On-failure restart with 10s backoff, bounded to 3 restarts per 300s window
Restart=on-failure
RestartSec=10

StandardOutput=append:/home/ubuntu/Local-AI-Trading-Agent/data/shadow_observer.log
StandardError=append:/home/ubuntu/Local-AI-Trading-Agent/data/shadow_observer.log

# Security Sandboxing & Least Privilege Restrictions
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/Local-AI-Trading-Agent/data /home/ubuntu/Local-AI-Trading-Agent/reports
ReadOnlyPaths=/home/ubuntu/Local-AI-Trading-Agent
NoNewPrivileges=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictRealtime=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
```

---

## 4. Task D: Offline Test Validation Evidence

An offline automated test suite was authored at [`tests/test_systemd_services.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_systemd_services.py).

### 4.1 Systemd Test Suite Results
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_systemd_services.py -v
```
**Output:**
```
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.1.1, pluggy-1.7.0
rootdir: C:\Users\ajith\Downloads\Local_AI_Agent
configfile: pytest.ini
collected 8 items

tests/test_systemd_services.py::test_01_unit_files_exist_and_parse PASSED [ 12%]
tests/test_systemd_services.py::test_02_entry_point_scripts_exist PASSED [ 25%]
tests/test_systemd_services.py::test_03_deployment_paths_and_service_account PASSED [ 37%]
tests/test_systemd_services.py::test_04_trading_daemon_exec_start_pre_paper_guard PASSED [ 50%]
tests/test_systemd_services.py::test_05_shadow_observer_order_prohibition PASSED [ 62%]
tests/test_systemd_services.py::test_06_bounded_restarts_and_lockout PASSED [ 75%]
tests/test_systemd_services.py::test_07_no_embedded_secrets_in_service_files PASSED [ 87%]
tests/test_systemd_services.py::test_08_sandboxing_and_least_privilege_directives PASSED [100%]

============================== 8 passed in 3.97s ==============================
```

### 4.2 Comprehensive Integration Regression Suite
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_systemd_services.py \
                                    tests/test_ci_cd_settings_regression.py \
                                    tests/test_dashboard_state_consistency.py \
                                    tests/test_paper_trade_accounting.py \
                                    tests/test_monitoring_api.py \
                                    tests/risk/test_position_size_guard.py -v
```
**Output:**
```
============================= 94 passed in 17.28s =============================
```
- **Total Tests Passed:** 94
- **Total Tests Failed:** 0
- **Regressions Introduced:** 0

---

## 5. Security and Restart Policy Analysis

| Directive | Trading Daemon | Shadow Observer | Security / Operational Purpose |
|---|---|---|---|
| `User` / `Group` | `ubuntu` / `ubuntu` | `ubuntu` / `ubuntu` | Non-root service account isolation |
| `ProtectSystem` | `strict` | `strict` | Read-only `/usr`, `/boot`, and `/etc` |
| `ProtectHome` | `read-only` | `read-only` | Protects `/home` from arbitrary write mutation |
| `ReadWritePaths` | `.../data .../logs` | `.../data .../reports` | Least-privilege confined write zones |
| `NoNewPrivileges` | `true` | `true` | Disallows `setuid`/`setgid` privilege escalation |
| `PrivateTmp` | `true` | `true` | Isolated `/tmp` and `/var/tmp` namespaces |
| `CapabilityBoundingSet` | *(empty)* | *(empty)* | Zero Linux root capabilities granted |
| `StartLimitIntervalSec` | `300s` | `300s` | Sliding evaluation window for restart bursts |
| `StartLimitBurst` | `3` | `3` | Maximum 3 restarts per window; locks out on 4th crash |
| `Restart` | `on-failure` | `on-failure` | Prevents restarting on clean `exit 0` shutdown |
| `RestartSec` | `10s` | `10s` | Linear backoff preventing fast-spinning CPU burn |
| `KillSignal` | `SIGINT` | `SIGTERM` | Graceful shutdown triggering application teardown |
| `TimeoutStopSec` | `30s` | `30s` | Upper bound before systemd delivers `SIGKILL` |

---

## 6. Known Limitations & Deployment Prerequisites

1. **Deployment Status:** In strict accordance with user safety requirements, **NO service units were deployed, copied, or started on AWS EC2**. This phase is offline validation and repository engineering only.
2. **EC2 Directory Prerequisite:** Before deploying to EC2, `/home/ubuntu/Local-AI-Trading-Agent/data`, `/home/ubuntu/Local-AI-Trading-Agent/logs`, and `/home/ubuntu/Local-AI-Trading-Agent/reports` must exist and be owned by `ubuntu:ubuntu`.
3. **Log Rotation (`logrotate`):** Append logs (`trading_daemon.log` and `shadow_observer.log`) will grow over continuous multi-day runs. A standard `/etc/logrotate.d/delta-trading` rule should be configured upon actual deployment.
4. **CI Credential Scanner Test Strings:** The GitHub Actions `security_audit` job failed because test files (`test_monitoring_api.py:166` and `test_phase3_42_safety_audit.py:117`) included literal synthetic strings like `DELTA_API_KEY=<token>` to test redaction. These are remediated in Phase 4.1.1 to use clearly synthetic dynamic fixtures.

---

## 7. Confirmation of Safety Constraints

- [x] `TRADING_MODE=PAPER` strictly preserved.
- [x] `LIVE_TRADING_ENABLED=false` strictly preserved.
- [x] `APPROVED_MAX_PAPER_LOTS == 1` strictly preserved.
- [x] Zero real exchange orders or brokerage connections executed.
- [x] Zero EC2 SSH commands or remote systemd service activations executed.
- [x] Zero credential leaks or private keys committed.
- [x] Historical defect record `PAPER_20261105_101000_LONG` (484 lots) preserved untouched.
- [x] Working tree clean; all changes isolated to `feature/phase4-1-systemd-services`.
