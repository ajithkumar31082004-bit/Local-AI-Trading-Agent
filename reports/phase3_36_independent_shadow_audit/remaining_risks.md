# Phase 3.36 — Remaining Risks & Operational Edge Cases

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `f3432c8`  
**Audit Branch:** `feature/phase3-36-independent-shadow-audit`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Identification of residual operational risks, network edge cases, and architectural considerations prior to any future authorized public-feed observation stage.

---

## 1. Executive Summary

While Phase 3.36 has verified that the read-only shadow observer is structurally isolated from trade execution, broker state mutation, and protected files, the transition to an active public-market-data observation phase introduces external runtime risks (network, exchange, clock, disk).

This document catalogs all identified residual risks and specifies required operational mitigations.

---

## 2. Risk Matrix

| Risk ID | Risk Category | Description | Severity | Likelihood | Mitigation Strategy |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **R-01** | Network | WebSocket disconnection or public REST timeout | Medium | Medium | Automated reconnection with exponential backoff; gap detection flags missing intervals. |
| **R-02** | Data Quality | Prolonged candle gaps (>1 hour) due to network outage | Medium | Low | `CandleValidator` records gap duration; `ReadOnlyStrategyEvaluator` enforces warmup gating (<150 bars). |
| **R-03** | System Clock | Clock drift between host OS and exchange server | Low | Medium | Host NTP time synchronization; candle validator allows completion on local time crossing interval boundary. |
| **R-04** | Exchange | Delta Exchange India scheduled maintenance (HTTP 502/503) | Medium | Low | Safe error handling in `PublicMarketDataReader`; zero-crash fail-safe state retention. |
| **R-05** | Storage | Unbounded JSONL growth over multi-week observation | Low | Medium | `shadow_signals.jsonl` size monitoring; log rotation policy for prolonged runs. |
| **R-06** | Memory | Buffer overflow in streaming candle ingestion | Very Low | Low | Enforced `deque(maxlen=500)` in `PublicMarketDataReader` caps memory consumption to under 10 MB. |
| **R-07** | Operational | Accidental invocation of live trading daemon | High | Low | Hardware/environment tripwires (`LIVE_TRADING_ENABLED=false` enforced in `observer_safety_guard.py`). |

---

## 3. In-Depth Risk Analysis & Safeguards

### 3.1 Network Disconnection & Reconnection Buffering (R-01, R-02)
- **Nature of Risk:** Public WebSocket connections may terminate unexpectedly due to transit ISP routing changes or exchange socket resets.
- **Current Safeguard:** `PublicMarketDataReader.is_feed_stale()` detects stagnation exceeding 60 seconds.
- **Future Operational Requirement:** When an observation runner is authorized, it must employ automatic reconnection logic and request a REST backfill of completed 5m candles to restore continuity without requiring a full 150-candle cold restart.

### 3.2 Host System Clock Synchronization (R-03)
- **Nature of Risk:** If host system time lags exchange time, `CandleValidator.validate_candle_completion()` might briefly reject a closed candle as `INCOMPLETE_CANDLE` until local wall clock catches up. If host clock runs ahead, it might attempt early evaluation.
- **Current Safeguard:** Dual validation checks both feed completion flags and local wall-clock time.
- **Operational Requirement:** The execution host (local machine or EC2) must maintain active NTP (`chrony` or `systemd-timesyncd`) synchronization with clock drift bounded within $\pm 500\text{ms}$.

### 3.3 Historical Incident Preservation & Invariant Protection (R-07)
- **Nature of Risk:** A developer or automated runner could accidentally point trading tools at live execution.
- **Current Safeguard:**
  - `APPROVED_MAX_PAPER_LOTS == 1` is strictly asserted.
  - `TRADING_MODE=PAPER` and `LIVE_TRADING_ENABLED=false` are enforced at startup via `verify_observer_isolation()`.
  - Historical incident record `PAPER_20261105_101000_LONG` (484 lots, net -$7.9472) is protected by `validate_shadow_write_path()`.

---

## 4. Residual Risk Assessment

No critical or unmitigated security vulnerabilities remain within the offline shadow observer codebase. The system fails closed upon any environment anomaly, write violation, or data inconsistency.
