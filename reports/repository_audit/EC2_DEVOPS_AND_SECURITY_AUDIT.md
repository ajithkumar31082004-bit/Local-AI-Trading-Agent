# EC2 DevOps, Infrastructure & Security Audit Report — XAUTUSD Trading Agent

**Document Identifier:** `REP-AUD-004-OPS`  
**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Audit Date:** 2026-11-11  
**Audit Scope:** EC2 Deployment Readiness, CI/CD Pipelines, Systemd Service Configuration, Containerization, Secrets Management, Application Security  
**Lead Auditor:** Senior AWS DevOps Engineer & Application Security Auditor  

---

## 1. Executive Summary

This audit assesses the deployment readiness, DevOps infrastructure, continuous integration, and application security posture for hosting the XAUTUSD derivatives agent on an AWS EC2 Ubuntu instance. 

While the codebase exhibits strong local security isolation (strict public/private feed segregation, absence of committed secrets, and bounded resource limits), the **DevOps and infrastructure layer has significant gaps**:
1. Zero containerization (no `Dockerfile` or `docker-compose.yml`).
2. An incomplete `requirements.txt` that omits critical production dependencies (`websockets`, `fastapi`, `uvicorn`, `httpx`).
3. An active defect in the GitHub Actions CI workflow (`AttributeError: 'ORBSettings' object has no attribute 'trading_mode'`).
4. An EC2 SSH private key file (`Super_Market.pem`) stored directly inside the local repository root directory.
5. Incomplete systemd service definitions (only the monitoring API has a service file; the core trading daemon and observer lack systemd units).

---

## 2. Infrastructure & Deployment Readiness Audit

### 2.1 Containerization Status
- **Dockerfile:** **ABSENT**. The repository contains no Dockerfile.
- **Docker Compose:** **ABSENT**. No multi-container orchestration is defined.
- **Impact:** Deployment onto EC2 relies entirely on manual bare-metal Python virtual environments (`.venv`), increasing configuration drift risk between local Windows and remote Ubuntu environments.

### 2.2 Dependency Configuration Gap
- **File:** [`requirements.txt`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/requirements.txt) (15 lines)
- **Deficiency:** `requirements.txt` specifies only 10 packages (`crewai`, `streamlit`, `requests`, `pydantic`, `python-dotenv`, `pandas`, `numpy`, `yfinance`, `plotly`, `pytest`).
- **Unlisted Production Dependencies:**
  - `websockets>=13.0` (required by `market_data/delta_live_feed.py` and `services/shadow_observer/transport/`)
  - `fastapi>=0.110.0` (required by `services/monitoring_api.py` and `scripts/delta-monitoring-api.service`)
  - `uvicorn>=0.28.0` (required to serve FastAPI)
  - `httpx>=0.27.0` (required by monitoring client and test suites)
- **Finding:** Running `pip install -r requirements.txt` on a clean EC2 Ubuntu instance will **fail** to start the monitoring API or the public WebSocket market data stream due to `ModuleNotFoundError`.

### 2.3 Systemd Service Configuration
- **Existing Unit:** [`scripts/delta-monitoring-api.service`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/scripts/delta-monitoring-api.service)
  - Purpose: Runs the read-only FastAPI monitoring daemon on port 8088.
  - Security Profile: Configured with `ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges=true`.
- **Missing Units:**
  - **No systemd service for the Trading Daemon:** No unit exists to supervise, restart, or auto-start the core algorithmic engine.
  - **No systemd service for the Shadow Observer:** No background daemon configuration exists to run the bounded public market data observer.

### 2.4 CI/CD Workflow Defect
- **File:** [`.github/workflows/deploy_and_track.yml`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/.github/workflows/deploy_and_track.yml)
- **Line 47 Defect:**
  ```python
  # In deploy_and_track.yml line 47:
  assert settings.trading_mode == 'PAPER', "Trading mode must be PAPER"
  ```
- **Code Reality:** In [`config/orb_settings.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/config/orb_settings.py), the dataclass attribute is named **`mode`**, NOT `trading_mode`:
  ```python
  # In config/orb_settings.py:
  class ORBSettings:
      mode: str = "PAPER"
  ```
- **Result:** Any execution of the GitHub Actions preflight check step will fail with:
  `AttributeError: 'ORBSettings' object has no attribute 'trading_mode'`.

---

## 3. Application Security Audit

### 3.1 Secrets Management & Git Exposure
- **`.env` File:** Strictly excluded via [`.gitignore`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/.gitignore) (`.env`). Verification confirmed `.env` is untracked and not present in Git index.
- **SSH Private Key Risk:** 
  - File: `Super_Market.pem` exists in the repository root directory (`C:\Users\ajith\Downloads\Local_AI_Agent\Super_Market.pem`).
  - Status: The file is currently ignored by Git due to `*.pem` in `.gitignore`.
  - Hazard: Keeping an active AWS EC2 SSH private key in the workspace directory creates a severe risk of accidental exposure, backup inclusion, or unintended staging.
  - **Remediation Required (P0):** Move `Super_Market.pem` out of the project directory to the user's secure SSH directory (`~/.ssh/` or Windows `%USERPROFILE%\.ssh\`) with restrictive permissions (`chmod 400` or Windows ACL inheritance removal).

### 3.2 Public vs Private Endpoint Isolation
- **Architecture File:** [`services/shadow_observer/transport/`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/)
- **Isolation Verification:**
  - The public market data transport adapter strictly connects to Delta Exchange India's public WebSocket (`wss://cdn.india.delta.exchange`) and public REST (`https://api.india.delta.exchange/v2/sparklines`).
  - The module contains **zero imports** of private API keys, secrets, or order submission routines.
  - Injected credentials cause immediate initialization failure.
  - Transport frames are strictly capped at 64 KB with maximum memory ceiling of 150 MB and exponential backoff (2s–60s, max 5 attempts).

### 3.3 LLM Credential Isolation
- Audit confirmed that the legacy CrewAI agents in `agents/` and Ollama integrations have zero access to Delta Exchange API credentials or private keys.

---

## 4. Observability & Monitoring Infrastructure

| Monitoring Component | Existing Implementation | Evaluation | Gap / Finding |
|---|---|:---:|---|
| **Health API** | `services/monitoring_api.py` | Partial | Exposes `/health`, `/state`, and `/metrics` (basic JSON) |
| **Prometheus Exporter** | None | Absent | Missing standard Prometheus client exporter |
| **CloudWatch Agent** | None | Absent | No `amazon-cloudwatch-agent.json` metrics configuration |
| **Log Rotation** | Bounded file logging | Partial | 25 MB file size guard present; missing `logrotate.d` system config |
| **Alert Notification** | None | Absent | No webhook or SNS alerting on fatal crashes or kill switch triggers |

---

## 5. Summary of Recommended Remediations

1. **Fix GitHub Actions Workflow (P1):** Correct `settings.trading_mode` to `settings.mode` in `.github/workflows/deploy_and_track.yml`.
2. **Relocate SSH Key (P0):** Move `Super_Market.pem` out of the workspace to `%USERPROFILE%\.ssh\`.
3. **Harmonize `requirements.txt` (P1):** Add `websockets>=13.0`, `fastapi>=0.110.0`, `uvicorn>=0.28.0`, and `httpx>=0.27.0`.
4. **Create Systemd Service for Trading Daemon (P1):** Author `scripts/delta-trading-daemon.service` with restart policies and user permissions.
5. **Containerize Application (P2):** Author `Dockerfile` and `docker-compose.yml` for reproducible Linux deployments.
6. **Implement Webhook / SNS Alerting (P2):** Send high-priority alerts to Discord/Telegram/Slack upon risk limits or unhandled exceptions.

