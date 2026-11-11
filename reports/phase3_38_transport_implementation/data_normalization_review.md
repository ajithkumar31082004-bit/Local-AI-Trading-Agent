# Phase 3.38 — Data Normalization & Ingestion Integrity Review

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `4455308`  
**Feature Branch:** `feature/phase3-38-public-feed-transport`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Review of market-data payload normalization, dual timestamp tracking, completed-candle validation integration, and zero-execution guarantees.

---

## 1. Executive Summary

Public WebSocket and REST feeds deliver heterogeneous JSON payloads with varying field naming conventions (`o` vs `open`, `t` vs `timestamp`, `mark_price` vs `price`). Furthermore, relying solely on exchange timestamps introduces vulnerability to exchange clock drift and delayed network packets.

This review evaluated how the transport adapter normalizes raw feeds into deterministic, validated domain structures:
1. **Dual Timestamping:** Every market event records both the upstream `exchange_timestamp` and the host `local_receipt_timestamp`.
2. **Schema Standardization:** Raw payloads are converted into strongly-typed `NormalizedTick` and `NormalizedCandle` dataclasses.
3. **Data Integrity Integration:** Candles are passed directly into the audited [`CandleValidator`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/candle_validator.py), enforcing monotonicity, duplicate detection, and completed-interval barriers.
4. **Zero Execution Artifacts:** Events contain purely descriptive market metrics with zero simulated fills, cash deductions, or trading artifacts.

---

## 2. Normalized Data Schemas

In [`services/shadow_observer/transport/transport_interface.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/transport_interface.py):

### 2.1 NormalizedTick Schema
```python
@dataclass
class NormalizedTick:
    symbol: str
    price: float
    exchange_timestamp: Optional[float]
    local_receipt_timestamp: float
    raw_payload: Dict[str, Any] = field(default_factory=dict)
```
- **`exchange_timestamp`**: Extracted from Delta Exchange tick payload (in seconds epoch).
- **`local_receipt_timestamp`**: High-resolution host epoch recorded at the exact moment the socket receives the frame.
- **`price`**: Verified float $> 0$.

### 2.2 NormalizedCandle Schema
```python
@dataclass
class NormalizedCandle:
    symbol: str
    timeframe: str  # '5m' or '15m'
    open: float
    high: float
    low: float
    close: float
    volume: float
    candle_start_epoch: int
    candle_end_epoch: int
    is_complete: bool = True
    exchange_timestamp: Optional[float] = None
    local_receipt_timestamp: float = 0.0
    raw_payload: Dict[str, Any] = field(default_factory=dict)
```
- Standardizes both REST array formats (`[o, h, l, c, v, t]`) and WebSocket object formats (`{"open": ..., "high": ...}`).
- Sets `candle_end_epoch = candle_start_epoch + 300` (for 5m) or `+ 900` (for 15m).

---

## 3. Data Integrity & Validation Pipeline

When an incoming candle reaches `TransportSupervisor.on_candle_received()`:
1. **Structural Validation:** `CandleValidator.validate_candle_structure()` verifies $H \ge L$, $H \ge \max(O,C)$, $L \le \min(O,C)$, $V \ge 0$, and positive prices.
2. **Completed-Candle Barrier:** `CandleValidator.validate_candle_completion()` verifies the interval has elapsed in wall-clock time relative to `local_receipt_timestamp`.
3. **Monotonicity & Duplicate Check:** In-memory timestamp hashes prevent duplicate evaluation and reject out-of-order bars.
4. **Buffer Ingestion:** Only candles passing all validation checks are buffered in `PublicMarketDataReader` for strategy consumption.

---

## 4. Test Evidence & Malformed Frame Defense

The normalization layer was subjected to comprehensive adversarial unit tests:
- `test_08_duplicate_and_out_of_order_market_events`: Verified that duplicate candle frames are rejected (`DUPLICATE_CANDLE`) while out-of-order ticks update `out_of_order_count` without regressing `last_ticker_time`.
- `test_09_malformed_market_data_messages`: Verified that non-JSON strings, negative prices, and non-numeric fields increment `malformed_frames_count` and are safely dropped without throwing unhandled exceptions.
- `test_10_foreign_product_symbol_rejection`: Verified that foreign symbols (e.g. `BTCUSD`, `ETHUSD`) are rejected at the transport boundary.
