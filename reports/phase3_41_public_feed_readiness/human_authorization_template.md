# Phase 3.41 — Formal Human Authorization Template for Future Public Market-Data Dry Run

**Document Purpose:** This template MUST be completed, signed, and recorded prior to any execution of a live public market-data dry-run observation. Execution is strictly unauthorized until this document is formally executed by both the Operator and an Independent Reviewer.

---

## 1. Dry-Run Authorization Scope

| Parameter | Mandated Setting | Operator Confirmation |
|---|---|---|
| **Target Instrument** | `XAUTUSD` ONLY | [ ] Confirmed |
| **Exchange** | Delta Exchange India | [ ] Confirmed |
| **Execution Mode** | STRICT READ-ONLY SHADOW (Zero Trading) | [ ] Confirmed |
| **Maximum Duration** | 3,600 Seconds (60 Minutes) | [ ] Confirmed |
| **Max Concurrent Sockets**| Exactly 1 Socket | [ ] Confirmed |
| **Target Host** | Local Workstation OR EC2 (Designate below) | [ ] Confirmed |
| **Approved Git Commit** | `feature/phase3-41-public-feed-readiness` (Hash: `_____________`) | [ ] Confirmed |

### Designated Host Details
- **Host Identifier:** `________________________________________________` (e.g., Local dev machine / EC2 instance id)
- **Host IP / Region:** `________________________________________________`
- **Execution Working Directory:** `________________________________________________`

---

## 2. Endpoint Authorization Allowlist & Ban Verification

### Allowed Endpoints (Public Only)
- [ ] **Public REST Base:** `https://api.india.delta.exchange`
  - Allowed routes: `/v2/tickers/XAUTUSD`, `/v2/products/XAUTUSD` (Initial snapshot check only)
- [ ] **Public WebSocket Feed:** `wss://socket.india.delta.exchange`
  - Allowed channels: `v2/ticker` (`XAUTUSD`), `candlestick_1m` (`XAUTUSD`)

### Strictly Prohibited Endpoints & Operations
- [ ] NO private REST routes (`/v2/orders*`, `/v2/positions*`, `/v2/wallet*`, `/v2/fills*`)
- [ ] NO authentication headers (`api-key`, `signature`, `timestamp`)
- [ ] NO order placement, cancellation, or modification
- [ ] NO broker database modifications (`broker.json`, `orders.db`, SQLite WAL files)
- [ ] NO trading daemon startup (`python -m bot ...` is prohibited)

---

## 3. Mandatory Preflight Safety Gates Checklist (12 Gates)

All 12 gates must be verified as PASS prior to launching the dry-run process:

| Gate # | Safety Requirement | Status |
|---|---|---|
| Gate 1 | `DELTA_API_KEY` and `DELTA_API_SECRET` are unset / empty in environment | [ ] PASS |
| Gate 2 | Clean namespace: zero broker modules or order classes loaded in process | [ ] PASS |
| Gate 3 | Clean Git working tree on approved Phase 3.41 commit | [ ] PASS |
| Gate 4 | Target symbol is strictly `XAUTUSD` | [ ] PASS |
| Gate 5 | Max duration configured to exactly <= 3,600 seconds | [ ] PASS |
| Gate 6 | Queue maximum bounded at <= 1,000 items | [ ] PASS |
| Gate 7 | Memory bound configured to <= 150 MB | [ ] PASS |
| Gate 8 | Disk output bound configured to <= 25 MB | [ ] PASS |
| Gate 9 | Restart policy configured strictly to `"never"` | [ ] PASS |
| Gate 10 | Output log directory writable and isolated in `reports/dry_run/` | [ ] PASS |
| Gate 11 | Outbound network test to `api.india.delta.exchange:443` succeeds | [ ] PASS |
| Gate 12 | System host has >= 500 MB free memory and >= 1 GB free disk | [ ] PASS |

---

## 4. Automatic Stop Triggers & Emergency Manual Halt

The operator acknowledges that the runner will automatically terminate immediately if ANY of the following occur:
1. Duration reaches 3,600 seconds (`DURATION_EXPIRED`).
2. Ticker feed has no update for > 60 seconds (`STALE_FEED`).
3. Internal queue overflows or drops even 1 frame (`QUEUE_OVERFLOW`).
4. Ingested payload contains a non-`XAUTUSD` symbol (`SYMBOL_MISMATCH`).
5. Ingested payload contains private or authenticated keys (`PRIVATE_KEY_IN_PAYLOAD`).
6. Process memory exceeds 150 MB (`RESOURCE_LIMIT_EXCEEDED`).
7. Output log size exceeds 25 MB (`RESOURCE_LIMIT_EXCEEDED`).
8. Operator issues stop signal (`SIGINT` / `Ctrl+C` / `SIGTERM`) (`MANUAL_HALT`).
9. Connection fails 5 consecutive times (`MAX_RECONNECTS_EXCEEDED`).
10. Unhandled exception occurs in background loop (`UNHANDLED_EXCEPTION`).

### Emergency Manual Intervention Command
If manual termination is required:
- Interactive: Press `Ctrl+C` in terminal session.
- Process ID termination: `kill -15 <PID>` (graceful teardown initiated within 5.0 seconds).
- Verify zero residual sockets: `netstat -ano | findstr 443` (local) or `ss -tpn | grep 443` (EC2).

---

## 5. Formal Sign-Off and Execution Authorization

By signing below, the Operator and Reviewer certify that:
1. The preflight safety gates have been inspected and confirmed.
2. The dry run will NOT execute trades, submit orders, or touch broker state.
3. The observation is strictly bounded to 60 minutes and public market data only.
4. Immediate halt procedures are operational and understood.

### Lead Operator Sign-Off
- **Name:** `________________________________________`
- **Signature:** `___________________________________`
- **Date / Time:** `_________________________________`

### Independent Safety Reviewer Sign-Off
- **Name:** `________________________________________`
- **Signature:** `___________________________________`
- **Date / Time:** `_________________________________`

*Notice: This authorization expires 24 hours after signatures are affixed. A new authorization document is required for any subsequent run.*
