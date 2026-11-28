# Phase 3.37 — Public Feed Connection Safety Review

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `13e8680`  
**Audit Branch:** `feature/phase3-37-authorization-readiness`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Static source-code review and offline safety inspection of `services/shadow_observer/market_data_reader.py` and related data-ingestion modules.

---

## 1. Executive Summary

As required by Phase 3.37 Section 5, this review evaluated the connection safety, error handling, credential isolation, and network architecture of the public market data reader without establishing any live network connections.

A critical finding of this review is the **separation between the data buffer/validation engine and the network transport runner**:
- The current implementation of [`services/shadow_observer/market_data_reader.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/market_data_reader.py) implements the **data buffer, validation, staleness tracking, and normalization engine**.
- It strictly defines public endpoints, operates 100% unauthenticated, and enforces data integrity.
- However, the active network client layer (socket connection, HTTP timeouts, automatic reconnection with backoff) has not yet been authored inside `services/shadow_observer/`. Any future authorization must require authors to implement these client-level transport safeguards in an observation runner before connecting to live feeds.

---

## 2. Detailed Technical Inspection

| Parameter / Feature | Inspection Target | Observed Implementation | Verification Status |
| :--- | :--- | :--- | :---: |
| **Connection Timeout** | Network socket connection timeout | No HTTP/WS transport client in module | **NOT VERIFIED / NOT IMPLEMENTED** |
| **Read Timeout** | HTTP/WS read inactivity timeout | No HTTP/WS transport client in module | **NOT VERIFIED / NOT IMPLEMENTED** |
| **Reconnect Behavior** | Auto-reconnect on socket drop | Handled conceptually via staleness flag | **NOT VERIFIED / NOT IMPLEMENTED** |
| **Max Reconnect / Backoff** | Exponential backoff algorithm | No transport client in module | **NOT VERIFIED / NOT IMPLEMENTED** |
| **Duplicate Prevention** | Single active socket guarantee | In-memory `seen_timestamps` prevents duplicate data | **NOT VERIFIED (Socket level)** |
| **Clean Shutdown** | Resource closure & socket cleanup | In-memory queue requires no socket teardown | **NOT VERIFIED (Socket level)** |
| **Rate-Limit Handling** | HTTP 429 backoff / WS throttle | No HTTP polling loop in module | **NOT VERIFIED / NOT IMPLEMENTED** |
| **Malformed Response Handling**| Rejection of corrupt ticks | Handled in `ingest_ticker_update` & `CandleValidator` | **VERIFIED (PASS)** |
| **Stale Ticker Detection** | Tracking elapsed silence (>60s) | Handled in `is_feed_stale()` with `stale_detections` counter | **VERIFIED (PASS)** |
| **Missing Market Data** | Detection of candle gaps | `CandleValidator` tracks `is_gap` and `missing_intervals` | **VERIFIED (PASS)** |
| **Prolonged Disconnection** | Warmup gating on stale data | Evaluator forces `NO_SIGNAL` on incomplete history | **VERIFIED (PASS)** |
| **No Private Fallback** | Rejection of private endpoints | Hardcoded `PUBLIC_REST_URL` & `PUBLIC_WS_URL` only | **VERIFIED (PASS)** |
| **No Order Endpoints** | Absence of order submission URLs | Zero references to `/v2/orders` or trading paths | **VERIFIED (PASS)** |
| **No Credential Loading** | Zero API keys or secrets loaded | Zero references to `DELTA_API_KEY` or `.env` | **VERIFIED (PASS)** |

---

## 3. Analysis of Verified Invariants

### 3.1 Strict Public-Only Endpoints
In [`services/shadow_observer/market_data_reader.py#L20-L23`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/market_data_reader.py#L20-L23):
```python
PUBLIC_REST_URL = "https://api.india.delta.exchange"
PUBLIC_WS_URL = "wss://socket.india.delta.exchange"
TARGET_INSTRUMENT = "XAUTUSD"
```
The module hardcodes Delta Exchange India's public REST and WebSocket URLs. It does not load API keys, API secrets, passphrase headers, or signature generators.

### 3.2 Error Handling for Malformed Responses
In `PublicMarketDataReader.ingest_ticker_update()`:
- Foreign symbols are ignored (`IGNORED: Foreign symbol ...`).
- Non-numeric prices raise `INVALID_PRICE` and return `(False, msg)` without raising unhandled exceptions.
- In `CandleValidator.validate_candle_structure()`:
  - Validates positive price values ($O, H, L, C > 0$).
  - Validates non-negative volume ($V \ge 0$).
  - Validates boundary axioms ($H \ge L$, $H \ge \max(O,C)$, $L \le \min(O,C)$).

### 3.3 Monotonic Ticker Timestamp Tracking & Staleness
In Phase 3.36, `ingest_ticker_update()` was hardened to preserve monotonicity:
```python
if self.last_ticker_time is not None and tick_epoch < self.last_ticker_time:
    self.out_of_order_count += 1
    logger.warning("Out of order ticker tick: %f < %f", tick_epoch, self.last_ticker_time)
else:
    self.last_ticker_time = tick_epoch
```
`is_feed_stale()` evaluates whether elapsed wall-clock time exceeds `stale_threshold_seconds = 60.0`. If stale, it increments `self.stale_detections`.

---

## 4. Unverified Network Transport Requirements

Before authorizing an active public-feed observation run, an execution runner must be authored that wraps `PublicMarketDataReader` with transport-level safeguards:
1. **HTTP/WebSocket Connection Timeouts:** Must configure explicit connect (e.g. 10s) and read timeouts (e.g. 30s) on all network requests.
2. **Reconnection Loop with Exponential Backoff:** Must retry disconnected WebSockets with backoff (e.g. 2s, 4s, 8s, up to 60s) and a maximum retry count before signaling a critical feed failure.
3. **HTTP 429 Rate-Limit Handling:** Must respect `Retry-After` headers on REST polling fallbacks.
4. **Graceful Socket Teardown:** Must ensure `SIGINT` / `SIGTERM` cleanly terminates WebSocket sessions without leaving dangling threads.
