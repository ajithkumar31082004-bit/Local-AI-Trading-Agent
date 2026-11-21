# Phase 3.38 — Public Feed Transport Adapter Architecture

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `4455308`  
**Feature Branch:** `feature/phase3-38-public-feed-transport`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Architectural specification, component breakdown, and data-flow topology of the public market-data transport adapter in `services/shadow_observer/transport/`.

---

## 1. Executive Summary

Phase 3.37 identified that while the Phase 3.35 shadow observer possessed an in-memory buffer, validation barrier, and strategy evaluator, it lacked a dedicated, testable network transport adapter.

Phase 3.38 implements this missing architectural layer in [`services/shadow_observer/transport/`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/) strictly offline. The new transport adapter:
1. Operates **100% unauthenticated**, completely devoid of API keys, secrets, or signature headers.
2. Interacts **exclusively with public market-data endpoints** (`https://api.india.delta.exchange` and `wss://socket.india.delta.exchange`).
3. Employs **injectable network adapters** allowing complete testability without connecting to the internet.
4. Manages a **formal 6-state connection lifecycle** (`DISCONNECTED`, `CONNECTING`, `CONNECTED`, `DEGRADED`, `STOPPING`, `STOPPED`).
5. Enforces **exponential backoff with jitter and retry bounds**, preventing infinite tight reconnect loops.
6. Feeds validated, normalized market data directly into the existing `PublicMarketDataReader` and `CandleValidator`.

---

## 2. Component Breakdown

The transport package is modularized into seven purpose-built components:

```
services/shadow_observer/transport/
├── __init__.py                  # Public exports and interface definitions
├── transport_config.py          # Strict configuration validation & endpoint rules
├── transport_interface.py       # Normalized data structures & lifecycle states
├── reconnect_controller.py      # Exponential backoff, jitter, and retry limiter
├── public_rest_client.py        # Public chart history & ticker REST client
├── public_websocket_client.py   # Public streaming ticker & candle WS client
└── transport_supervisor.py      # Top-level orchestrator & pipeline integration
```

### 2.1 TransportConfig ([`transport_config.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/transport_config.py))
- Validates base URLs against public Delta Exchange India domains.
- Enforces an absolute block on execution keywords (`order`, `position`, `wallet`, `account`, `private`, `auth`).
- Blocks forbidden authentication headers (`api-key`, `delta-signature`, `authorization`).
- Enforces positive numerical limits on connection timeouts, read timeouts, and retry parameters.

### 2.2 Normalized Event Schemas ([`transport_interface.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/transport_interface.py))
- **`NormalizedTick`**: Represents an atomic price tick with separate `exchange_timestamp` and `local_receipt_timestamp`.
- **`NormalizedCandle`**: Represents a completed OHLCV bar with start epoch, end epoch, timeframe (`5m` or `15m`), and structural bounds.
- **`ConnectionState`**: Explicit enum tracking the operational state of transport components.

### 2.3 ReconnectController ([`reconnect_controller.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/reconnect_controller.py))
- Computes exponential backoff:
  $$\text{Delay} = \min\left(\text{max\_backoff},\ \text{initial\_backoff} \times \text{factor}^{(\text{attempt}-1)}\right) \pm \text{jitter}$$
- Caps maximum backoff at 60.0s.
- Enforces a hard stop after `max_reconnect_attempts` (default 5) consecutive failures.
- Maintains a strictly bounded in-memory audit log of reconnection attempts (`deque(maxlen=100)`).

### 2.4 PublicRestClient ([`public_rest_client.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/public_rest_client.py))
- Provides historical candle backfill via `/v2/chart/history` and snapshot pricing via `/v2/tickers/{symbol}`.
- Rejects any non-allowlisted path with a `ShadowObserverSafetyViolation`.
- Injects HTTP adapters for offline mock testing.

### 2.5 PublicWebSocketClient ([`public_websocket_client.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/public_websocket_client.py))
- Connects to public WebSocket feed and subscribes only to `v2/ticker` and `candlestick_5m` for `XAUTUSD`.
- Prevents duplicate connection attempts if already in `CONNECTED` or `CONNECTING` states.
- Rejects malformed JSON and non-numeric price frames without raising unhandled exceptions.

### 2.6 TransportSupervisor ([`transport_supervisor.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/transport_supervisor.py))
- Orchestrates REST and WebSocket clients, managing failover, backoff, and health degradation.
- Directly routes validated ticks to `PublicMarketDataReader.ingest_ticker_update()` and candles through `CandleValidator.validate_incoming_candle()`.
- Transitions state to `DEGRADED` if feed staleness exceeds 60 seconds.

---

## 3. Data-Flow Architecture

```mermaid
flowchart TD
    subgraph External Public World (Offline Mocks in Phase 3.38)
        PUB_WS["Delta Public WS (wss://socket.india.delta.exchange)"]
        PUB_REST["Delta Public REST (https://api.india.delta.exchange)"]
    end

    subgraph Transport Adapter Layer
        CFG[TransportConfig] -->|Enforce Public Only| RC[PublicRestClient]
        CFG -->|Enforce Public Only| WS[PublicWebSocketClient]
        WS -->|Lifecycle / Reconnect| RECON[ReconnectController]
        
        PUB_REST -.->|Mocked JSON| RC
        PUB_WS -.->|Mocked Frames| WS
        
        RC -->|NormalizedCandle| SUP[TransportSupervisor]
        WS -->|NormalizedTick / Candle| SUP
    end

    subgraph Existing Shadow Observer (Phase 3.35 Core)
        SUP -->|ingest_ticker_update| MDR[PublicMarketDataReader]
        SUP -->|validate_incoming_candle| CV{CandleValidator}
        CV -->|Completed 5m Bar| MDR
        MDR -->|get_candle_dataframe| EVAL[ReadOnlyStrategyEvaluator]
        EVAL -->|HYPOTHETICAL — NOT AN ORDER| REC[ShadowSignalRecorder]
        REC -->|JSONL Stream| DISK[(reports/shadow_validation/)]
    end

    classDef blocked fill:#f9f,stroke:#333,stroke-width:2px;
    class PUB_WS,PUB_REST blocked;
```

---

## 4. Architectural Isolation Proof

1. **Terminal Integration:** The transport supervisor only calls read/ingest methods on existing observer classes. It has zero reference to `DeltaPaperBroker` or order endpoints.
2. **Deterministic Injectability:** All network operations use injectable socket factories and HTTP adapters, guaranteeing zero internet access during tests.
3. **Fail-Closed Design:** Any path traversal, unknown channel subscription, or authentication attempt raises `ShadowObserverSafetyViolation` and aborts immediately.
