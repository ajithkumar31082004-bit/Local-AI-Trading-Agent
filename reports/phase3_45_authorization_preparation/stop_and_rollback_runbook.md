# Phase 3.45 — Stop & Rollback Operational Runbook

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Current Branch:** `feature/phase3-45-dual-human-authorization-prep`  
**Target Instrument:** `XAUTUSD` | **Exchange:** Delta Exchange India  
**Document Status:** OPERATIONAL PROCEDURE REFERENCE  

---

## 1. Runbook Architecture

This runbook defines the exact operational protocols for every automatic tripwire and incident scenario. For every condition, six mandatory lifecycle steps are established:

```
[1. Trigger] ──────► [2. Immediate Response] ──────► [3. Ingestion Shutdown]
                           │
                           ▼
[4. Cleanup Behavior] ──► [5. Restart Prohibition] ──► [6. Evidence Preservation]
```

---

## 2. Procedure Cards per Stop & Rollback Condition

### 2.1 Stale Market Data (`STALE_FEED`)
1. **Trigger:** No public ticker update received for $>60.0\text{ seconds}$ during an active observation window.
2. **Immediate Response:** `ShadowDryRunRunner.tick()` evaluates condition 2; sets `shutdown_reason = "STALE_FEED: No ticker update received for Xs"`.
3. **Ingestion Shutdown:** Closes WebSocket subscription and socket descriptor immediately; stops event queue processing.
4. **Cleanup Behavior:** Calls `supervisor.stop()`; confirms transport state is `ConnectionState.STOPPED`.
5. **Restart Prohibition:** Sets `self.has_terminated = True`. Re-invoking `start()` raises `ShadowObserverSafetyViolation`.
6. **Evidence to Preserve:** Flushes `data/shadow_observer/shadow_dry_run_report_<epoch>.json` with `stale_feed_incidents: 1` and timestamp of last received frame.

---

### 2.2 Queue Overflow (`QUEUE_OVERFLOW`)
1. **Trigger:** Internal event queue fills to capacity (1,000 events) and drops $\ge 1$ frame (`queue_overflow_count > 0`).
2. **Immediate Response:** Evaluated on loop tick; flags data loss condition.
3. **Ingestion Shutdown:** Closes public socket connection; ceases reading ticks.
4. **Cleanup Behavior:** Tears down supervisor; flushes telemetry.
5. **Restart Prohibition:** `has_terminated = True` locks runner permanently.
6. **Evidence to Preserve:** Logs exact `queue_overflow_count` and queue consumption rate in summary JSON.

---

### 2.3 Memory Threshold Exceeded (`MEMORY_BUDGET_EXCEEDED`)
1. **Trigger:** Host process RSS memory exceeds $150.0\text{ MB}$.
2. **Immediate Response:** Loop detects memory breach; sets shutdown reason.
3. **Ingestion Shutdown:** Immediately aborts message deserialization and drops in-flight buffers.
4. **Cleanup Behavior:** Unsubscribes from WebSocket channels; forces garbage collection (`gc.collect()`); closes sockets.
5. **Restart Prohibition:** Runner permanently marked terminated.
6. **Evidence to Preserve:** Records `peak_memory_mb` in `resource_utilization` section of readiness report.

---

### 2.4 Disk / Log Threshold Exceeded (`DISK_BUDGET_EXCEEDED`)
1. **Trigger:** Cumulative size of files in `data/shadow_observer/` exceeds $25.0\text{ MB}$.
2. **Immediate Response:** Loop halts before disk volume can be filled.
3. **Ingestion Shutdown:** Stops tick/candle logging immediately.
4. **Cleanup Behavior:** Closes file handles; terminates transport supervisor.
5. **Restart Prohibition:** Permanent restart lockout enforced.
6. **Evidence to Preserve:** Records `peak_disk_mb` and list of generated artifacts.

---

### 2.5 Repeated Reconnect Failures (`MAX_RECONNECTS_EXCEEDED`)
1. **Trigger:** WebSocket transport encounters 5 consecutive connection drops or handshake failures.
2. **Immediate Response:** `ReconnectController.on_connection_failure()` reaches max attempt ceiling (5); signals unrecoverable degradation.
3. **Ingestion Shutdown:** Closes retry timers; stops reconnection loop.
4. **Cleanup Behavior:** Releases socket descriptors; transitions transport state to `STOPPED`.
5. **Restart Prohibition:** Runner locks permanently; revival blocked.
6. **Evidence to Preserve:** Preserves full 100-entry ring buffer of reconnect attempt timestamps, backoff intervals, and error reasons in JSON report.

---

### 2.6 Malformed or Unexpected Payloads (`SYMBOL_MISMATCH` / `MALFORMED_DATA`)
1. **Trigger:** Received message contains non-`XAUTUSD` symbol, corrupted JSON, NaN/Inf values, or negative prices.
2. **Immediate Response:** Ingestion edge (`CandleValidator` or `PublicWebSocketClient`) increments `malformed_frames_count` and drops payload. If symbol is mismatched, trips `SYMBOL_MISMATCH` stop condition.
3. **Ingestion Shutdown:** Terminates feed to prevent pipeline contamination.
4. **Cleanup Behavior:** Tears down network client and clears raw frame buffer.
5. **Restart Prohibition:** Permanent lockout enforced.
6. **Evidence to Preserve:** Records raw schema defect, rejection code, and rejected candle count in `market_data_metrics.rejected_candles`.

---

### 2.7 Manual Halt (`MANUAL_HALT`)
1. **Trigger:** Human operator presses `Ctrl+C` in console or sends `kill -15 <PID>`.
2. **Immediate Response:** Signal handler catches `SIGINT` / `SIGTERM` / `KeyboardInterrupt`; sets `stop_requested = True`.
3. **Ingestion Shutdown:** Gracefully halts ingestion within 5.0 seconds.
4. **Cleanup Behavior:** Closes sockets cleanly (`assert supervisor.state == ConnectionState.STOPPED`); writes summary report.
5. **Restart Prohibition:** Permanent lockout enforced; signal is re-raised to exit cleanly.
6. **Evidence to Preserve:** Captures `shutdown_reason = "MANUAL_HALT: Stop requested by operator"` and session duration.

---

### 2.8 Unexpected Process Termination (`UNHANDLED_EXCEPTION`)
1. **Trigger:** Unhandled exception or fatal crash occurs in the supervision thread.
2. **Immediate Response:** `tick()` exception boundary catches `BaseException`.
3. **Ingestion Shutdown:** Immediately executes emergency socket closure in `finally` block.
4. **Cleanup Behavior:** Sets transport state to `STOPPED`; prevents orphaned sockets.
5. **Restart Prohibition:** `has_terminated = True` blocks any automatic restart attempt.
6. **Evidence to Preserve:** Records full exception class, traceback, and error message to stderr and JSON summary.

---

### 2.9 Suspected Credential Exposure (`PRIVATE_KEY_IN_PAYLOAD`)
1. **Trigger:** Ingested frame or log message contains sensitive keywords (`api-key`, `delta-signature`, `private_key`, `secret`).
2. **Immediate Response:** Payload safety scanner halts runner immediately with `PRIVATE_KEY_IN_PAYLOAD`.
3. **Ingestion Shutdown:** Aborts all transport activity instantly.
4. **Cleanup Behavior:** Closes all connections; redacts secret values before recording.
5. **Restart Prohibition:** Runner permanently terminated.
6. **Evidence to Preserve & Rotate:**
   - Record keyword name (e.g. `"api-key"`) without recording secret values.
   - Operator immediately clears shell environment and rotates API keys on Delta Exchange India console.

---

## 3. Operational Rollback Verification

In all 9 scenarios:
- **Broker State Immunity:** Broker files (`paper_account_xautusd.json` and SQLite database) are not imported and remain completely untouched.
- **Socket Zeroing:** No dangling sockets remain open after shutdown.
- **Single Cleanup Pass:** Rollback executes exactly once and does not loop.
