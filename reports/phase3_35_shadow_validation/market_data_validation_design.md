# Phase 3.35 — Public Market Data Validation Design

This document details the public feed specifications, candle finalization rules, and data quality validation protocols designed for the XAUTUSD Read-Only Shadow Observer.

---

## 1. Verified Public Endpoints & Channels

All future observer market data connections utilize strictly public, unauthenticated exchange endpoints:

| Layer | Protocol | URL / Endpoint | Authentication | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **Public REST API** | HTTPS | `https://api.india.delta.exchange` | None | Initial candle history backfill (`/v2/history/candles`). |
| **Public WebSocket** | WSS | `wss://socket.india.delta.exchange` | None | Streaming public live market updates. |
| **Ticker Channel** | WS | `v2/ticker` (symbol: `XAUTUSD`) | None | Mark price, last traded price, 24h volume, bid/ask spread. |
| **5m Candle Channel** | WS | `candlestick_5m` (symbol: `XAUTUSD`) | None | 5-minute OHLCV bar stream. |
| **15m Candle Channel** | WS | `candlestick_15m` (symbol: `XAUTUSD`) | None | 15-minute higher-timeframe trend context. |

> [!IMPORTANT]
> **ZERO PRIVATE ENDPOINT ACCESS**:
> The observer configuration does not accept or transmit API keys, secrets, or passphrase headers. Private endpoints (`/v2/orders`, `/v2/positions`, `/v2/wallet/balances`) are structurally excluded from the observer codebase.

---

## 2. Candle Validation & Data Quality Engine

### 2.1 The Incomplete Candle Barrier
Strategies must never execute on in-progress or forming candles, as intra-candle ticks can trigger premature, spurious breakout signals that later evaporate before bar close.

**Enforcement Protocol**:
1. **Explicit Flag Check**: Incoming candle feeds from Delta Exchange indicate closed bars via `is_closed` or `completed`. If `completed == false`, the candle is rejected immediately with code `INCOMPLETE_CANDLE`.
2. **Wall-Clock Completion Guard**: The validator calculates $\text{Bar Close Time} = \text{Bar Start Epoch} + \Delta t_{\text{interval}}$. If $\text{Bar Close Time} > \text{Current Wall Clock Time}$, the candle is rejected as unfinalized.

### 2.2 Monotonic Timestamp Ordering & Duplicate Prevention
- **Monotonicity**: Each finalized candle must have a timestamp $T_{n} > T_{n-1}$. Any candle arriving with $T_{n} \le T_{n-1}$ is rejected with code `OUT_OF_ORDER`.
- **Deduplication**: Candle timestamps are recorded in an in-memory `seen_timestamps` hash set. Identical timestamp arrivals are discarded with code `DUPLICATE_CANDLE`.

### 2.3 Gap & Missing Interval Detection
- For 5-minute candles, the expected interval $\Delta t = 300\text{ seconds}$.
- If $T_{n} - T_{n-1} > 300\text{s}$, the validator flags a gap:
  $$\text{Missing Intervals} = \left\lfloor \frac{T_{n} - T_{n-1}}{300} \right\rfloor - 1$$
- Gaps trigger an automatic warning log, update data quality metrics, and enforce strategy warm-up re-evaluation before signals are approved.

### 2.4 Stale Ticker Detection
- The `PublicMarketDataReader` monitors the arrival time of public ticker updates.
- If elapsed time since the last tick exceeds **60.0 seconds**, the feed status transitions to `STALE_FEED`.
- Strategy evaluation is blocked while market data is stale.

---

## 3. Reconnection & Resubscription Protocol

In the event of a public WebSocket disconnection during future execution:
1. **State Reset**: Mark connection status as `RECONNECTING`.
2. **Exponential Backoff**: Reconnect attempts back off from 1s to 30s.
3. **Resubscription**: Re-subscribe strictly to public channels (`v2/ticker`, `candlestick_5m`).
4. **History Reconciliation**: Query public REST endpoint (`/v2/history/candles`) to backfill any candles missed during the disconnect window.
5. **Validation Re-run**: Validate backfilled candles through `CandleValidator` to ensure gapless transition before unblocking strategy evaluation.
