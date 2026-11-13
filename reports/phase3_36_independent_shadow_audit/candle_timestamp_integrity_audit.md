# Phase 3.36 — Candle & Timestamp Integrity Audit

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `f3432c8`  
**Audit Branch:** `feature/phase3-36-independent-shadow-audit`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Technical verification of candle validation, incomplete-candle barriers, monotonic sequencing, gap detection, and public feed staleness tracking in `services/shadow_observer/candle_validator.py` and `market_data_reader.py`.

---

## 1. Executive Summary

A critical vulnerability in algorithmic trading systems is **look-ahead bias** and **premature signal generation** caused by evaluating incomplete (forming) candles or processing malformed/out-of-order market data.

This audit verified that [`services/shadow_observer/candle_validator.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/candle_validator.py) and [`services/shadow_observer/market_data_reader.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/market_data_reader.py) enforce strict data-integrity barriers before any strategy can consume market data.

Key audit findings:
1. **Completed-Candle Barrier:** Prevents evaluation of forming candles via dual verification (feed completion flags + wall-clock interval completion).
2. **Strict Monotonicity:** Out-of-order candles arriving backwards in time are rejected immediately.
3. **Duplicate Rejection:** Timestamp hashing prevents re-evaluation of identical intervals.
4. **Gap Accounting:** Missing intervals (>300s for 5m) are accurately measured and logged without crashing the pipeline.
5. **Structural OHLC Validation:** High/Low bounds, positive prices, and non-negative volume are strictly enforced.
6. **Feed Staleness Detection:** Market reader detects dead or disconnected feeds when elapsed time exceeds 60 seconds.

---

## 2. Incomplete Candle Barrier Verification

### 2.1 Wall-Clock Boundary Logic

In `CandleValidator.validate_candle_completion()`:
```python
if now_epoch is not None:
    # candle_start_epoch extracted from candle timestamp
    if candle_start_epoch + self.expected_interval_seconds > now_epoch:
        return False, "INCOMPLETE_CANDLE: Candle interval has not yet closed in real time."
```

For a 5m candle starting at epoch $T_0$ (duration 300 seconds):
- At $T_0 + 299\text{s}$: The interval has 1 second remaining. Evaluated as **INCOMPLETE** (`rep.is_valid == False`).
- At $T_0 + 300\text{s}$: The interval has reached completion. Evaluated as **COMPLETED** (`rep.is_valid == True`).
- At $T_0 + 600\text{s}$: Future timestamp relative to $now\_epoch$. Evaluated as **INCOMPLETE** (`rep.is_valid == False`).

### 2.2 Feed Completion Flags

If the exchange sends explicit flags (`completed`, `is_complete`, `is_closed`) and any is `False`, the candle is rejected even if wall-clock time has passed. This protects against delayed exchange closing procedures.

---

## 3. Timestamp Ordering & Duplicate Defense

### 3.1 Monotonic Sequencing
`CandleValidator` maintains `self.last_candle_epoch`.
```python
delta = epoch - self.last_candle_epoch
if delta <= 0:
    return CandleValidationReport(
        is_valid=False,
        rejection_reason=f"OUT_OF_ORDER: Candle timestamp {epoch} <= last seen {self.last_candle_epoch}."
    )
```
Any candle with timestamp less than or equal to the prior candle is rejected.

### 3.2 Duplicate Rejection
`CandleValidator` maintains `self.seen_timestamps: Set[int]`. If a candle's epoch has already been processed, it is rejected as `DUPLICATE_CANDLE` and `duplicate_count` is incremented.

---

## 4. Gap Detection & Accounting

When `delta > self.expected_interval_seconds`:
$$\text{missing\_intervals} = \left\lfloor \frac{\text{delta}}{\text{interval\_seconds}} \right\rfloor - 1$$

Adversarial testing verified:
- **1-Hour Gap (3600s):** Accurately flags 11 missing 5m candles.
- **24-Hour Weekend Gap (86400s):** Accurately flags 287 missing 5m candles.
- Pipeline logs warnings with exact missing counts and continues safe execution without buffer corruption.

---

## 5. Structural OHLC Integrity

`validate_candle_structure()` tests every record against fundamental market axioms:
1. **$Open, High, Low, Close > 0$:** Rejects non-positive or zero prices.
2. **$Volume \ge 0$:** Rejects negative volume.
3. **$High \ge Low$:** Rejects inverted price ranges.
4. **$High \ge \max(Open, Close)$:** Rejects wicks below candle body.
5. **$Low \le \min(Open, Close)$:** Rejects lower wicks above candle body.
6. **Required Keys:** Ensures all 6 required fields (`open`, `high`, `low`, `close`, `volume`, `timestamp`) exist.

---

## 6. Feed Staleness & Monotonic Ticker Tracking

### 6.1 Finding and Hardening in `market_data_reader.py`

During Phase 3.36 adversarial testing, an inspection of `PublicMarketDataReader.ingest_ticker_update()` revealed that an out-of-order tick (arriving with a delayed timestamp) logged a warning but then overwrote `self.last_ticker_time = tick_epoch`, regressing the reader's latest timestamp backwards.

**Phase 3.36 Remediation:**
The logic was hardened so that `self.last_ticker_time` only updates if `tick_epoch >= self.last_ticker_time`:
```python
if self.last_ticker_time is not None and tick_epoch < self.last_ticker_time:
    self.out_of_order_count += 1
    logger.warning("Out of order ticker tick: %f < %f", tick_epoch, self.last_ticker_time)
else:
    self.last_ticker_time = tick_epoch
```
This guarantees that `last_ticker_time` is monotonically non-decreasing, and `is_feed_stale()` accurately computes elapsed wall-clock time from the newest observed tick.

### 6.2 Staleness Threshold
- When $T_{\text{now}} - T_{\text{last\_ticker}} \le 60\text{s}$: Feed is **HEALTHY** (`stale == False`).
- When $T_{\text{now}} - T_{\text{last\_ticker}} > 60\text{s}$: Feed is **STALE** (`stale == True`), triggering `stale_detections` counter.

---

## 7. Audit Test Summary

| Test ID | Test Name | Target Behavior | Result |
| :--- | :--- | :--- | :---: |
| `test_adv_03` | In-Progress Candle Boundary | Rejects $T_0+299$s, accepts $T_0+300$s | **PASSED** |
| `test_adv_04` | Future Candle Rejection | Rejects timestamps ahead of current wall clock | **PASSED** |
| `test_adv_05` | Monotonicity & Duplicate | Rejects duplicates and backwards timestamps | **PASSED** |
| `test_adv_06` | Extreme Candle Gaps | Accurately accounts for 1h and 24h gaps | **PASSED** |
| `test_adv_07` | Corrupted OHLCV | Rejects inverted prices, negative volume, NaN | **PASSED** |
| `test_adv_14` | Market Data Staleness | Detects feed stagnation >60s and tracks out-of-order ticks | **PASSED** |

---

## 8. Audit Verdict

**PASS**. Candle validation and timestamp integrity safeguards are robust, fail-safe, and thoroughly verified against edge-case timing anomalies.
