# Phase 3.38 — Public Endpoint Allowlist & Forbidden Route Guard

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `4455308`  
**Feature Branch:** `feature/phase3-38-public-feed-transport`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Formal definition of permitted public endpoints, strict route allowlisting, and rejection mechanisms for execution/account APIs.

---

## 1. Executive Summary

To ensure total isolation from order routing, private accounts, and financial mutations, the public feed transport adapter enforces a **positive allowlist** (whitelist) for both REST and WebSocket endpoints. Any attempt to request an endpoint, query string, or WebSocket channel outside this allowlist is trapped and raises a `ShadowObserverSafetyViolation`.

---

## 2. Permitted Public Endpoints Allowlist

Only unauthenticated public market-data endpoints on Delta Exchange India are authorized:

### 2.1 Public REST Endpoints (`https://api.india.delta.exchange`)

| Permitted Path | HTTP Method | Permitted Query Parameters | Purpose | Authentication |
| :--- | :---: | :--- | :--- | :---: |
| `/v2/tickers/{symbol}` | `GET` | `symbol=XAUTUSD` | Ticker mark price, 24h high/low | **NONE (Public)** |
| `/v2/chart/history` | `GET` | `symbol=XAUTUSD`, `resolution=5m\|15m`, `from`, `to` | Historical candle backfill | **NONE (Public)** |
| `/v2/products` | `GET` | *None* | Contract specifications | **NONE (Public)** |

### 2.2 Public WebSocket Channels (`wss://socket.india.delta.exchange`)

| Channel Name | Permitted Symbols | Payload Structure | Purpose | Authentication |
| :--- | :--- | :--- | :--- | :---: |
| `v2/ticker` | `["XAUTUSD"]` | `{"name": "v2/ticker", "symbols": ["XAUTUSD"]}` | Real-time market tick stream | **NONE (Public)** |
| `candlestick_5m` | `["XAUTUSD"]` | `{"name": "candlestick_5m", "symbols": ["XAUTUSD"]}` | Real-time completed 5m OHLCV bars | **NONE (Public)** |
| `candlestick_15m` | `["XAUTUSD"]` | `{"name": "candlestick_15m", "symbols": ["XAUTUSD"]}` | Real-time completed 15m OHLCV bars | **NONE (Public)** |

---

## 3. Forbidden Route & Keyword Denylist

The transport configuration and client layers enforce an explicit ban on any keyword associated with trading, account management, authentication, or fund transfers:

### 3.1 Forbidden Route Keywords
If any of the following substrings appear in a requested REST URL or WebSocket channel name, execution is aborted:
- `order`, `orders` (Order placement, cancellation, or querying)
- `position`, `positions` (Open positions, margin, leverage)
- `wallet`, `account` (Balances, equity, deposits)
- `fill`, `fills`, `trade`, `trades` (Execution history, user fills)
- `private`, `auth` (Private authenticated endpoints)
- `transfer`, `withdraw` (Asset transfers)

### 3.2 Forbidden Authentication Headers
If any HTTP request or WebSocket handshake contains any of the following headers, `ShadowObserverSafetyViolation` is raised:
- `api-key`
- `delta-signature`
- `delta-timestamp`
- `authorization`
- `signature`
- `secret`

---

## 4. Enforcement Implementation & Test Evidence

### 4.1 Path Validation in `PublicRestClient`
In [`services/shadow_observer/transport/public_rest_client.py#L60-L75`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/public_rest_client.py#L60-L75):
```python
def _validate_path_safety(self, path: str) -> None:
    p_lower = path.lower()
    for forbidden in FORBIDDEN_ENDPOINT_KEYWORDS:
        if forbidden in p_lower:
            raise ShadowObserverSafetyViolation(f"CRITICAL SECURITY VIOLATION: REST client attempted forbidden path: {path}")

    is_allowed = any(p_lower.startswith(allowed) for allowed in ALLOWED_REST_PATHS)
    if not is_allowed:
        raise ShadowObserverSafetyViolation(f"SECURITY VIOLATION: Path '{path}' not in public allowlist: {ALLOWED_REST_PATHS}")
```

### 4.2 Subscription Validation in `PublicWebSocketClient`
In [`services/shadow_observer/transport/public_websocket_client.py#L125-L138`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/public_websocket_client.py#L125-L138):
```python
def _validate_subscription_safety(self, payload: Dict[str, Any]) -> None:
    channels = payload.get("payload", {}).get("channels", [])
    for ch in channels:
        name = ch.get("name", "").lower()
        for forbidden in FORBIDDEN_ENDPOINT_KEYWORDS:
            if forbidden in name:
                raise ShadowObserverSafetyViolation(f"SECURITY VIOLATION: Forbidden channel '{name}' requested in subscription.")
        if name not in ALLOWED_PUBLIC_CHANNELS:
            raise ShadowObserverSafetyViolation(f"SECURITY VIOLATION: Channel '{name}' not in public allowlist: {ALLOWED_PUBLIC_CHANNELS}")
```

### 4.3 Test Proof
- `test_01_transport_config_validation_and_safety`: Verified that attempting to configure URLs containing `orders`, `positions`, or `wallet` raises `ShadowObserverSafetyViolation`.
- `test_15_no_order_endpoint_access`: Verified that calling `_execute_request("POST", "/v2/orders")` raises `ShadowObserverSafetyViolation`.
