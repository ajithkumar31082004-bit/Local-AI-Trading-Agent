# PHASE 3.41 — RESOURCE BUDGET & OPERATIONAL LIMITS

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Target Instrument:** `XAUTUSD`  
**Mode:** STRICT READ-ONLY SHADOW / OFFLINE PLANNING ONLY.

---

## 1. Conservative Resource Budget Table

The 60-minute public feed dry run operates under strict, bounded resource allocations designed to prevent memory leaks, CPU saturation, disk flooding, or runaway network retries:

| Resource Dimension | Authorized Budget | Enforcement Mechanism | Behavior When Limit Reached |
| :--- | :---: | :--- | :--- |
| **Observation Duration** | **60 minutes** (3,600s) | Hard timer in `ShadowDryRunRunner` | Automatic clean shutdown with reason `MAX_DURATION_REACHED`. |
| **In-Memory Event Queue** | **1,000 items** | `collections.deque(maxlen=1000)` | Oldest event evicted; `queue_overflow_count` incremented; state degraded; runner halts. |
| **Reconnect Audit Log** | **100 records** | `collections.deque(maxlen=100)` | Oldest reconnect audit entry evicted; FIFO bounded ring buffer. |
| **Process Memory Limit** | **150.0 MB** | Sampled via `memory_check_fn` | Immediate runner shutdown with reason `MEMORY_BUDGET_EXCEEDED`. |
| **Disk Storage Limit** | **25.0 MB** | Sampled directory size check | Immediate runner shutdown with reason `DISK_BUDGET_EXCEEDED`. |
| **Max Retry Attempts** | **5 attempts** | Counter in `ReconnectController` | Permanent reconnect termination; runner stops with `MAX_RECONNECTS_EXCEEDED`. |
| **Max Backoff Delay** | **60.0 seconds** | Exponential backoff cap | Paced delay capped at 60.0s $\pm 10\%$ jitter. |

---

## 2. In-Depth Operational Behavior per Limit

### 2.1 Duration Limit (60 Minutes)
- **Clock:** Host monotonic / UTC timestamp.
- **Action:** At $t = t_0 + 3600.0\text{s}$, `runner.tick()` evaluates condition 1.
- **Result:** Halts the event ingestion loop, cancels socket subscriptions, flushes the final JSON report to disk, and transitions to terminal state `STOPPED`.

### 2.2 Event Queue Capacity (1,000 Items)
- **Structure:** Ring buffer holding `NormalizedTick` and `NormalizedCandle` objects.
- **Worst-case Size:** $1,000 \times 240\text{ bytes} \approx 234\text{ KB}$.
- **Action:** If consumer latency stalls and queue fills, item 1,001 evicts item 1.
- **Safety Tripwire:** Incrementing `queue_overflow_count` triggers automatic stop condition 4 (`QUEUE_OVERFLOW`), halting the observer so data loss is never ignored.

### 2.3 Process Memory Limit (150.0 MB)
- **Baseline:** Python interpreter + loaded modules typically consume $35\text{ MB} - 55\text{ MB}$.
- **Threshold:** Set conservatively at $150.0\text{ MB}$.
- **Action:** If garbage collection fails or an unexpected leak occurs, exceeding $150.0\text{ MB}$ aborts execution immediately.

### 2.4 Disk Storage Limit (25.0 MB)
- **Storage Area:** `data/shadow_observer/`
- **Expected Consumption:**
  - 60 minutes of tick logging: $\approx 1.0\text{ MB} - 3.0\text{ MB}$.
  - Completed candle summary JSON: $\approx 50\text{ KB}$.
- **Threshold:** 25.0 MB provides a $8\times$ safety buffer. Exceeding 25.0 MB halts the observer immediately.

### 2.5 Reconnection Attempts & Delays
- **Max Consecutive Failures:** 5.
- **Delay Schedule:** 2.0s, 4.0s, 8.0s, 16.0s, 32.0s (capped at 60.0s).
- **Action:** On attempt 6, reconnect logic returns `(False, 0.0s)`. The runner detects unrecoverable degradation and halts.

---

## 3. Test Verification Summary

All budget thresholds and behaviors were verified offline in [`tests/test_phase3_41_readiness_plan.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_phase3_41_readiness_plan.py):
- `test_05`: Timeout at 3600.0s verified.
- `test_07`: Stop on queue overflow verified.
- `test_08`: Stop on memory (>150MB) and disk (>25MB) violations verified.

---

## 4. Audit Verdict

**PASS**. Resource budgets are strictly defined, physically bounded, and safeguarded by automated stop tripwires.