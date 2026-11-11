# PHASE 3.41 — FUTURE OBSERVATION SCOPE & PROTOCOL SPECIFICATION

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Target Instrument:** `XAUTUSD`  
**Target Exchange:** Delta Exchange India  
**Execution Mode:** STRICT READ-ONLY SHADOW / OFFLINE PLANNING ONLY  
**Current Authorization Status:** PLANNING ONLY — LIVE RUNTIME EXECUTION PROHIBITED.

---

## 1. Operational Parameters & Boundaries

If authorized in a separate future phase, the proposed public market-data dry-run observation will operate under the following non-negotiable boundaries:

| Parameter | Operational Boundary | Enforcement Mechanism |
| :--- | :--- | :--- |
| **Maximum Duration** | **60 minutes** (3,600 seconds) | Automatic countdown timer in `ShadowDryRunRunner`; triggers immediate shutdown at $t \ge 3600\text{s}$. |
| **Instrument** | **`XAUTUSD` only** | Hardcoded symbol assertion; foreign frames (`BTCUSD`, `ETHUSD`, etc.) are dropped at the transport edge. |
| **Exchange** | **Delta Exchange India** | Base URLs restricted to `api.india.delta.exchange` and `socket.india.delta.exchange`. |
| **Authentication** | **Zero (0) credentials** | Operates strictly unauthenticated; `api-key`, `signature`, and `auth` headers trigger critical exceptions. |
| **Order Placement** | **Zero (0) orders** | No order endpoints, no broker imports, AST isolation verified. |
| **Broker State** | **Inaccessible** | `DeltaPaperBroker` is not imported; SQLite and JSON databases are untouched. |
| **Trading Daemon** | **Stopped (Inactive)** | Preflight safety gate asserts trading daemon process is not running. |
| **Strategy Engine** | **Read-Only / Shadow** | Signals are recorded to memory/JSON for observation only; zero execution intent created. |
| **File Output** | **Isolated** | Writes strictly confined to `data/shadow_observer/`. |

---

## 2. Public Endpoint Specifications

### 2.1 Allowlisted Public REST Endpoints
Base URL: `https://api.india.delta.exchange`
- **GET `/v2/tickers/{symbol}`**: Fetches snapshot ticker data for `XAUTUSD`.
  - Request: `GET https://api.india.delta.exchange/v2/tickers/XAUTUSD`
  - Headers: Standard `User-Agent` only (no API keys, no secrets).
  - Expected Response Schema:
    ```json
    {
      "success": true,
      "result": {
        "symbol": "XAUTUSD",
        "mark_price": "4050.25",
        "close": "4050.00",
        "timestamp": 1731300000000
      }
    }
    ```
- **GET `/v2/chart/history`**: Fetches historical OHLCV candles for warmup.
  - Query Params: `symbol=XAUTUSD&resolution=5m&from={start}&to={end}`
  - Expected Response Schema:
    ```json
    {
      "success": true,
      "result": {
        "o": [4000.0],
        "h": [4010.0],
        "l": [3990.0],
        "c": [4005.0],
        "v": [100.0],
        "t": [1731300000]
      }
    }
    ```

### 2.2 Allowlisted Public WebSocket Channel
Endpoint: `wss://socket.india.delta.exchange`

- **Subscription Message (Outbound):**
  ```json
  {
    "type": "subscribe",
    "payload": {
      "channels": [
        {"name": "v2/ticker", "symbols": ["XAUTUSD"]},
        {"name": "candlestick_5m", "symbols": ["XAUTUSD"]}
      ]
    }
  }
  ```
- **Expected Ticker Frame (Inbound):**
  ```json
  {
    "type": "v2/ticker",
    "symbol": "XAUTUSD",
    "mark_price": "4050.50",
    "timestamp": 1731300015000
  }
  ```
- **Expected Completed Candle Frame (Inbound):**
  ```json
  {
    "channel": "candlestick_5m",
    "candle": {
      "symbol": "XAUTUSD",
      "open": 4000.0,
      "high": 4010.0,
      "low": 3990.0,
      "close": 4005.0,
      "volume": 12.5,
      "timestamp": 1731300000,
      "is_complete": true
    }
  }
  ```

---

## 3. Heartbeat & Unsubscribe Protocol

- **Heartbeat Protocol:**
  - Delta Exchange India emits periodic heartbeat frames: `{"type": "heartbeat"}` or `{"type": "ping"}`.
  - The transport client inspects `msg_type` and silently updates `self.last_heartbeat_time` without emitting spurious ticks.
- **Unsubscribe / Clean Teardown:**
  - Upon scheduled completion (60m) or automatic stop trigger:
    ```json
    {
      "type": "unsubscribe",
      "payload": {
        "channels": [
          {"name": "v2/ticker", "symbols": ["XAUTUSD"]},
          {"name": "candlestick_5m", "symbols": ["XAUTUSD"]}
        ]
      }
    }
    ```
  - The socket client sends the unsubscription payload, flushes in-flight events, invokes `_socket.close()`, and transitions to `STOPPED`.

---

## 4. Operational Readiness Verdict

The observation scope is completely specified, bounded, and ready for offline gate validation. Zero execution capabilities exist.