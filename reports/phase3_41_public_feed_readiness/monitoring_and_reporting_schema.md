# PHASE 3.41 — MONITORING & REPORTING SCHEMA

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Target Instrument:** `XAUTUSD`  
**Mode:** STRICT READ-ONLY SHADOW / OFFLINE PLANNING ONLY.

---

## 1. Reporting Structure Specification

Upon completion or termination of a 60-minute public market-data observation dry run, [`ShadowDryRunRunner.generate_readiness_report()`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/dry_run_runner.py) exports a structured JSON report to `data/shadow_observer/dry_run_report_{timestamp}.json`.

**ZERO FABRICATED METRICS POLICY:** All fields are derived from runtime telemetry counters, system memory profilers, and the transport supervisor. If an event did not occur, its counter is strictly `0`.

---

## 2. Complete JSON Schema & Field Definitions

```json
{
  "session_metadata": {
    "instrument": "XAUTUSD",
    "exchange": "Delta Exchange India",
    "execution_mode": "STRICT READ-ONLY SHADOW",
    "start_timestamp": "2026-11-11T20:00:00.000Z",
    "stop_timestamp": "2026-11-11T21:00:00.000Z",
    "total_runtime_seconds": 3600.0,
    "shutdown_reason": "MAX_DURATION_REACHED: Observation completed 60 minutes.",
    "final_transport_state": "STOPPED"
  },
  "connectivity_metrics": {
    "connection_attempts": 1,
    "successful_connections": 1,
    "disconnects_and_failures": 0,
    "consecutive_failures": 0,
    "max_reconnect_attempts_budget": 5
  },
  "market_data_metrics": {
    "received_ticks": 4520,
    "received_completed_candles": 12,
    "rejected_candles": 0,
    "duplicate_events": 0,
    "out_of_order_events": 0,
    "stale_feed_incidents": 0,
    "queue_overflow_count": 0,
    "current_queue_depth": 0,
    "has_data_loss": false,
    "is_feed_healthy": true
  },
  "resource_utilization": {
    "peak_memory_mb": 54.2,
    "max_memory_budget_mb": 150.0,
    "peak_disk_mb": 2.4,
    "max_disk_budget_mb": 25.0
  },
  "safety_confirmations": {
    "orders_submitted": 0,
    "broker_methods_invoked": 0,
    "private_api_calls_attempted": 0,
    "trading_state_mutations": 0,
    "live_trading_enabled": false,
    "trading_mode": "PAPER",
    "restart_prevented": true
  }
}
```

---

## 3. Field Explanations & Auditable Truth

### 3.1 Session Metadata
- `total_runtime_seconds`: Calculated via `stop_epoch - start_epoch`.
- `final_transport_state`: Confirms the socket lifecycle reached terminal state `STOPPED`.

### 3.2 Connectivity Metrics
- Tracks every network reconnect attempt, success, and failure via `ReconnectController`.

### 3.3 Market Data Integrity
- `received_completed_candles`: Only 5-minute candles validated through `CandleValidator`.
- `duplicate_events`: Count of rejected duplicate timestamps.
- `out_of_order_events`: Count of timestamp regressions.
- `has_data_loss`: `True` if any queue overflow drops occurred.

### 3.4 Safety Confirmations
- Confirms zero orders were submitted, zero broker methods were called, and zero private endpoints were accessed.

---

## 4. Test Verification Summary

Verified via [`test_10_readiness_reporting_schema_completeness`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_phase3_41_readiness_plan.py):
- Validated all required fields are present and conform to type contracts.
- Confirmed zero orders and broker isolation flags are properly asserted.

---

## 5. Audit Verdict

**PASS**. The monitoring and reporting schema is complete, auditable, strictly non-fabricating, and verified by offline tests.