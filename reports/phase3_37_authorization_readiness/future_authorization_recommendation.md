# Phase 3.37 — Recommendations for Future Public-Feed Authorization

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `13e8680`  
**Audit Branch:** `feature/phase3-37-authorization-readiness`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Actionable roadmap and staging protocol for safely conducting a limited, separately authorized public-feed observation stage.

---

## 1. Executive Summary

Based on the forensic evidence gathered during Phase 3.36 and Phase 3.37, the shadow observer's core validation and strategy evaluation engine is completely isolated, safe, and ready for public data ingestion.

However, to guarantee zero risk to production infrastructure, live trading daemons, or financial accounts, a **phased, staged roadmap** is strongly recommended. Live observation should begin as a **short, controlled local run** before any continuous cloud deployment is considered.

---

## 2. Recommended Future Staging Roadmap

### Phase 3.38: Authoring the Transport Runner & Offline Socket Verification
- **Objective:** Construct a dedicated, standalone public market data runner (e.g. [`scripts/run_shadow_observer.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/scripts/)) that wraps `PublicMarketDataReader` with robust network transport mechanisms:
  1. Unauthenticated WebSocket connection to `wss://socket.india.delta.exchange`.
  2. Strict connect timeout (10s) and message read timeout (30s).
  3. Exponential backoff reconnection loop (2s, 4s, 8s, up to 60s max, 5 retries max).
  4. Pre-flight host disk space validation ($\ge 1.0\text{ GB}$).
  5. Clean `SIGINT` / `SIGTERM` shutdown signal handler.
- **Validation:** Test the runner against synthetic local WebSocket server fixtures to verify reconnection and timeout behavior completely offline.

### Phase 3.39: Operator Sign-Off & Limited 60-Minute Local Public-Feed Dry Run
- **Objective:** Execute an initial, tightly time-boxed public observation run on the **local workstation**:
  - **Environment:** Local machine (isolated from EC2 trading services).
  - **Duration:** Exactly 60 minutes (12 completed 5m candles).
  - **Prerequisite:** Human operator signs off on the authorization checklist.
  - **Verification:** Monitor live feed ingestion, verify zero order placement, verify `shadow_signals.jsonl` streaming, and verify zero alterations to broker state files.

### Phase 3.40: Post-Observation Performance Attribution & Report
- **Objective:** Run [`ShadowReportGenerator`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/shadow_report.py) to compile signal distribution, strategy performance, market regime distribution, and rejection metrics from the 60-minute run into a formal audit report.

---

## 3. Mandatory Safety Restrictions for Future Phases

Under all circumstances, the following safeguards must remain immutable across all future phases:
1. **Zero Order Execution:** No PAPER or LIVE orders may be submitted to Delta Exchange India or `DeltaPaperBroker`.
2. **One Micro-Lot Invariant:** `APPROVED_MAX_PAPER_LOTS == 1` must remain unchanged in [`risk/position_size_guard.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/risk/position_size_guard.py).
3. **Environment Invariants:** `TRADING_MODE=PAPER` and `LIVE_TRADING_ENABLED=false` must be strictly enforced.
4. **Historical Record Preservation:** Historical incident `PAPER_20261105_101000_LONG` (484 lots, net -$7.9472) must remain 100% intact on disk.
5. **No EC2 Trading Daemon Interference:** The observer must never interact with `trading_daemon.service`.
