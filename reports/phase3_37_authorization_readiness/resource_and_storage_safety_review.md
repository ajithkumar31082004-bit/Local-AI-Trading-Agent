# Phase 3.37 — Resource & Storage Safety Review

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `13e8680`  
**Audit Branch:** `feature/phase3-37-authorization-readiness`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Quantitative resource modeling, disk storage growth projection, memory exhaustion analysis, and failure behavior under resource constraints.

---

## 1. Executive Summary

A common failure mode in background monitoring services is resource exhaustion (unbounded memory growth, runaway disk log files, or unhandled disk-full exceptions).

This audit conducted a quantitative evaluation of resource usage in [`services/shadow_observer/`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/):
1. **Memory Bound:** The candle ingestion buffer is strictly bounded by `deque(maxlen=500)` in [`PublicMarketDataReader`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/market_data_reader.py#L39), limiting memory usage to under 100 KB.
2. **Disk Growth Rate:** Under 5m candle evaluations, the append-only JSONL stream grows at approximately **432 KB per day** (6.05 MB over a 14-day observation period).
3. **Atomic File Persistence:** Summary metrics are persisted via temporary sibling replacement (`.tmp` $\rightarrow$ `.json`), preventing file corruption during power loss or abrupt termination.
4. **Disk-Full Failure Mode:** Standard file operations raise `OSError` if disk space is exhausted. A pre-flight disk check ($\ge 1\text{ GB}$) is required prior to runtime authorization.

---

## 2. Quantitative Storage Growth Modeling

In [`services/shadow_observer/signal_recorder.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/signal_recorder.py), evaluations are streamed to `shadow_signals.jsonl`.

### 2.1 Cadence and Payload Sizing
- **Candle Interval:** 5 minutes (300 seconds).
- **Events per Hour:** 12 evaluations.
- **Events per 24 Hours:** 288 evaluations.
- **Single Event JSON Payload:** Approximately 1,500 bytes (1.5 KB), encompassing timestamp, regime classification, winning signal, diagnostic codes, and sub-strategy states.

### 2.2 Projected Disk Footprint

$$\text{Daily Growth} = 288 \times 1.5\text{ KB} = 432\text{ KB/day}$$

| Observation Duration | Total Events Recorded | Projected JSONL Size | Projected Summary Size | Total Disk Footprint |
| :--- | :---: | :---: | :---: | :---: |
| **1 Hour** | 12 | 18 KB | 0.5 KB | ~18.5 KB |
| **24 Hours (1 Day)** | 288 | 432 KB | 0.5 KB | ~432.5 KB |
| **7 Days (1 Week)** | 2,016 | 3.02 MB | 0.5 KB | ~3.02 MB |
| **14 Days (2 Weeks)** | 4,032 | 6.05 MB | 0.5 KB | ~6.05 MB |
| **30 Days (1 Month)** | 8,640 | 12.96 MB | 0.5 KB | ~12.96 MB |

**Conclusion:** The disk growth rate is negligible relative to standard host storage (e.g. EC2 `gp3` 20 GB volume or local 180 GB drive). Extended observation will not exhaust host disk capacity under normal operation.

---

## 3. In-Memory Buffering & Leak Protection

### 3.1 Market Data Buffer
In `PublicMarketDataReader.__init__`:
```python
self._candle_buffer: deque = deque(maxlen=max_buffer_candles)
```
- The `deque` structure automatically evicts the oldest candle when 500 candles are buffered.
- 500 candle dictionaries in Python occupy roughly 50 to 100 KB of RAM.
- There are no accumulators, historical tick lists, or memory caches that scale with runtime duration.

### 3.2 Strategy Evaluator & Recorder
- `ReadOnlyStrategyEvaluator` is stateless between evaluation calls.
- `ShadowSignalRecorder` maintains only scalar and small-cardinality counters:
  - `total_events: int`
  - `signals_by_type: Dict[str, int]` (3 keys: BUY, SELL, NO_SIGNAL)
  - `signals_by_strategy: Dict[str, int]` (6 keys)
  - `signals_by_regime: Dict[str, int]` (5 keys)
  - `warmup_events: int`
- Total process memory consumption is projected to remain stable at **under 50 MB** indefinitely.

---

## 4. Temporary File & Disk Failure Handling

### 4.1 Atomic Summary Replacement
In `ShadowSignalRecorder._save_summary()`:
```python
tmp_summary = self.summary_path.with_suffix(".tmp")
with open(tmp_summary, "w", encoding="utf-8") as f:
    json.dump(summary_data, f, indent=2)
tmp_summary.replace(self.summary_path)
```
- `replace()` is an atomic filesystem operation on POSIX and modern Windows NTFS.
- Temporary files are immediately renamed; zero abandoned `.tmp` files accumulate over time.

### 4.2 Behavior Under Disk-Full Conditions
- If the host volume fills to 100%, `open()` or `write()` will raise `OSError: [Errno 28] No space left on device` (Linux) or `[WinError 112]` (Windows).
- The observer does not currently contain active disk pre-checks in `signal_recorder.py`.
- **Operational Requirement:** A runtime wrapper must verify that host disk space exceeds 1.0 GB before initiating observation, and monitoring should alert if free space drops below 500 MB.
