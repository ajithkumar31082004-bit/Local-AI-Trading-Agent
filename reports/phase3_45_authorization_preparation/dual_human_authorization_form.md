# Phase 3.45 — Dual-Human Operational Authorization Form

**Document Identifier:** `FORM-AUTH-PHASE3-45-SHADOW-DRYRUN`  
**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Instrument:** `XAUTUSD` | **Exchange:** Delta Exchange India  
**Execution Mode:** STRICT READ-ONLY SHADOW (Zero Trading, Zero Broker Order Execution)  
**Governance Requirement:** DUAL HUMAN SIGN-OFF REQUIRED PRIOR TO ANY RUNTIME OBSERVATION  

---

## 1. Dry-Run Operational Parameters & Limits

Prior to execution, the Lead Operator and Independent Safety Reviewer must independently inspect and confirm each operational boundary:

| Parameter | Required Specification | Operator Check | Reviewer Check |
|---|---|:---:|:---:|
| **Target Instrument** | `XAUTUSD` ONLY | [ ] Confirmed | [ ] Confirmed |
| **Exchange** | Delta Exchange India | [ ] Confirmed | [ ] Confirmed |
| **Approved REST Base** | `https://api.india.delta.exchange` (Public endpoints only) | [ ] Confirmed | [ ] Confirmed |
| **Approved WebSocket Base** | `wss://socket.india.delta.exchange` (Public channels only) | [ ] Confirmed | [ ] Confirmed |
| **Public WS Channels** | `v2/ticker` (`XAUTUSD`), `candlestick_5m` (`XAUTUSD`) | [ ] Confirmed | [ ] Confirmed |
| **Private Endpoints** | STRICTLY PROHIBITED (`/v2/orders*`, `/v2/positions*` banned) | [ ] Confirmed | [ ] Confirmed |
| **Authentication Headers** | NONE (`api-key`, `signature`, `timestamp` banned) | [ ] Confirmed | [ ] Confirmed |
| **Maximum Duration** | 3,600 Seconds (Exactly 60 Minutes) | [ ] Confirmed | [ ] Confirmed |
| **Maximum Memory Usage** | 150.0 MB RSS | [ ] Confirmed | [ ] Confirmed |
| **Maximum Disk / Log Usage** | 25.0 MB inside isolated `data/shadow_observer/` | [ ] Confirmed | [ ] Confirmed |
| **Maximum Event Queue** | 1,000 items (FIFO ring buffer) | [ ] Confirmed | [ ] Confirmed |
| **Max Reconnect Attempts** | 5 attempts (Exponential backoff capped at 60s) | [ ] Confirmed | [ ] Confirmed |
| **Restart Policy** | STRICTLY `"never"` (Auto-restart prohibited) | [ ] Confirmed | [ ] Confirmed |
| **Trading Daemon State** | STOPPED (`delta_paper_bot.py` not running) | [ ] Confirmed | [ ] Confirmed |
| **Order Execution** | ZERO (No broker imports, no orders submitted) | [ ] Confirmed | [ ] Confirmed |

---

## 2. Designated Host & Environment Specifications

The physical host designated for the proposed 60-minute observation must be recorded below:

- **Designated Execution Environment:** `[ ] Local Development Workstation` / `[ ] EC2 Cloud Instance`
- **Host Identifier / IP:** `________________________________________________`
- **Operating System & Python Version:** `________________________________________________`
- **Working Directory:** `________________________________________________`
- **Approved Audited Git Commit Hash:** `________________________________________________`

---

## 3. Mandatory Safety Invariant Declarations

Both signatories certify that the following invariants have been verified in the environment:
1. `TRADING_MODE=PAPER` and `LIVE_TRADING_ENABLED=false` are active in `.env`.
2. `APPROVED_MAX_PAPER_LOTS == 1` is hardcoded in `risk/position_size_guard.py`.
3. Historical trade record `PAPER_20261105_101000_LONG` (484 lots, net -$7.9472) is intact in `data/paper_forward_24h_state.json`.
4. Zero broker execution classes (`DeltaPaperBroker`, `order_manager`) are loaded in process.
5. All 12 Preflight Safety Gates in `dry_run_runner.py` must pass prior to opening network sockets.
6. All 10 Automatic Stop Conditions in `dry_run_runner.py` are active and monitored on every loop tick.

---

## 4. Dual Human Sign-Off and Authorization Execution

> [!WARNING]
> **SIGNATURE POLICY NOTICE:**  
> Both signatures and timestamps MUST remain blank until physically executed by the named individuals. Under no circumstances may AI assistants, automated scripts, or unauthorized personnel invent names, sign on behalf of another party, or pre-populate authorization timestamps.

### 4.1 Lead Operator Authorization
*I certify that I have reviewed the proposed observation scope, verified that the configuration is strictly read-only, confirmed that no order execution is enabled, and will personally monitor execution and the manual halt procedure (`Ctrl+C` / `SIGTERM`).*

- **Full Printed Name:** `__________________________________________________`
- **Role / Title:** Lead Systems Operator
- **Physical Signature:** `__________________________________________________`
- **Date & Time (IST / UTC):** `__________________________________________________`
- **Scope Confirmation:** `[ ] Confirmed`
- **Read-Only Invariant Confirmation:** `[ ] Confirmed`

---

### 4.2 Independent Safety Reviewer Authorization
*I certify that I have independently audited the safety controls, inspected the 10 automatic stop tripwires and rollback runbook, verified the zero-broker architectural isolation, and confirm that no order execution, live trading, or credential exposure is authorized.*

- **Full Printed Name:** `__________________________________________________`
- **Role / Title:** Independent Trading Safety Reviewer
- **Physical Signature:** `__________________________________________________`
- **Date & Time (IST / UTC):** `__________________________________________________`
- **Safety Controls Review Confirmation:** `[ ] Confirmed`
- **Zero Order Execution Confirmation:** `[ ] Confirmed`

---

## 5. Expiration & Revocation Policy

- **Validity Window:** This authorization is valid for a single observation window not exceeding 60 minutes and expires exactly **24 hours** after signatures are affixed.
- **Immediate Revocation:** Any trigger of an automatic stop condition (e.g. `STALE_FEED`, `QUEUE_OVERFLOW`, `MEMORY_BUDGET_EXCEEDED`, `PRIVATE_KEY_IN_PAYLOAD`) immediately and permanently revokes this authorization. A new authorization form is required for any subsequent run.
