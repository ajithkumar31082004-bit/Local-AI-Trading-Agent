# Phase 3.45 — Gate 11: Network Preflight Connectivity Procedure

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Current Branch:** `feature/phase3-45-dual-human-authorization-prep`  
**Target Instrument:** `XAUTUSD` | **Exchange:** Delta Exchange India  
**Document Status:** OPERATIONAL SPECIFICATION ONLY — NO NETWORK CALLS PERFORMED IN PHASE 3.45  

---

## 1. Objective of Preflight Gate 11

When a future 60-minute public market-data dry run is explicitly authorized and initiated, **Preflight Gate 11** acts as the deterministic network barrier. It verifies that outbound network pathways to Delta Exchange India's public infrastructure are operational before initializing any WebSocket client loops.

> [!CAUTION]
> **OFFLINE AUDIT RESTRICTION:**  
> This procedure specifies the exact verification protocol for a future authorized run. **No network packets, DNS queries, or socket connections are initiated during Phase 3.45.**

---

## 2. Approved Public Endpoint Allowlist & Port Bindings

The preflight check is strictly confined to the following two public destinations:

| Host Domain | Destination Port | Protocol | Allowlisted Path / Operation | Prohibited Scope |
|---|:---:|:---:|---|---|
| `api.india.delta.exchange` | 443 | HTTPS | `GET /v2/tickers/XAUTUSD` | All private endpoints (`/v2/orders*`, `/v2/positions*`, `/v2/wallet*`) are blocked. |
| `socket.india.delta.exchange` | 443 | WSS | Public channel handshake (`v2/ticker`, `candlestick_5m`) | Private subscriptions and authenticated payloads are blocked. |

---

## 3. Step-by-Step Preflight Verification Sequence

```
              [Preflight Gate 11 Initialized]
                            │
                            ▼
      Step 1: Validate Outbound Host & Regex Confinement
                            │
                            ▼
      Step 2: TLS Socket Probe (api.india.delta.exchange:443)
           - Connect Timeout: 5.0 seconds
           - Handshake: TLS 1.2 or TLS 1.3
           - Certificate Validation: Subject alt name matches *.delta.exchange
                            │
                            ▼
      Step 3: Unauthenticated REST Snapshot Probe
           - Request: GET https://api.india.delta.exchange/v2/tickers/XAUTUSD
           - Headers: Standard User-Agent only (Zero API keys)
           - Response Code: Exactly HTTP 200 OK
           - Payload Validation: JSON parses successfully and contains symbol: "XAUTUSD"
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
     [All Probes Pass]             [Any Probe Fails]
              │                           │
              ▼                           ▼
      Gate 11 Result: PASS         Gate 11 Result: FAIL
     Permit Gate 12 Check         Raise SafetyViolation
                                  Abort Startup Immediately
                                  Zero Sockets Maintained
```

---

## 4. Timeout & Failure Behavior

### 4.1 Strict Timeout Thresholds
- **TCP Connection Timeout:** Exactly **5.0 seconds**.
- **TLS Handshake Timeout:** Exactly **5.0 seconds**.
- **HTTP Read Timeout:** Exactly **5.0 seconds**.
- **Action on Timeout:** The probe does not retry endlessly. If any socket operation stalls past 5.0 seconds, the operation is immediately aborted with `CONNECTIVITY_TIMEOUT`.

### 4.2 Failure Action & Progression Block
If any step fails (e.g. DNS resolution failure, network timeout, HTTP non-200, TLS verification error, or symbol mismatch):
1. `execute_preflight_safety_gates()` captures the failure string: `"Gate 11 Failed: Outbound connectivity probe failed: <reason>"`.
2. The gate suite returns `(False, [failures])`.
3. `ShadowDryRunRunner.start()` raises `ShadowObserverSafetyViolation(shutdown_reason)`.
4. Startup is aborted **before** any WebSocket connection is attempted.
5. All temporary probe sockets are closed in `finally` blocks.

---

## 5. Result Logging & Credential Exclusion Policy

### 5.1 Storage Destination
When executed in an authorized phase, Gate 11 probe results must be written strictly to:
- Output file: `reports/dry_run_preflight/gate11_network_probe.json`
- Path validation: Confined by `validate_shadow_write_path()`.

### 5.2 Strict Credential Redaction & Sanitization
1. **Zero Credential Transmission:** The probe request includes only standard HTTP headers (`User-Agent: LocalAI-ShadowObserver/1.0`, `Accept: application/json`).
2. **Banned Headers Guard:** The request pipeline asserts that `api-key`, `delta-signature`, and `authorization` are completely absent.
3. **Payload Sanitizer:** In the event of an unexpected server response containing credentials or tokens, the payload sanitizer redacts all matching patterns (`re.sub(r'("?(?:key|secret|token|password)"?\s*:\s*)"[^"]+"', r'\1"[REDACTED]"', text)`) prior to logging.
