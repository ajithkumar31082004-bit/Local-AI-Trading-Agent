# Phase 3.27 — Remaining Risks & Operational Boundaries

This document provides a rigorous, transparent assessment of operational boundaries, trade-offs, and residual risks following the Phase 3.27 safety remediations.

---

## 1. Residual Risk Assessment Matrix

| Risk Category | Residual Risk Level | Mitigation Implemented | Operational Boundary / Recommendation |
|---|:---:|---|---|
| **Cross-Process Concurrency** | **LOW** | `CrossProcessReentrantLock` (`portalocker.Lock`) on `.lock` file | Production deployment should run strictly as a single designated systemd unit (`delta-paper-trading.service`). |
| **Idempotency Key Cache Growth** | **LOW** | 1,000-entry pruning window | Prunes keys oldest-first; 1,000 keys covers weeks of signals at 1-lot maximum frequency. |
| **State Persistence Storage Failure** | **LOW** | Atomic temporary file replacement (`MoveFileExW` / `rename`) | If disk is 100% full, write fails safely; existing valid state is preserved. |
| **System Clock Skew (NTP)** | **VERY LOW** | UTC epoch comparisons using `datetime.now(timezone.utc).timestamp()` | Server system clock must be synchronized via `systemd-timesyncd` or `chrony`. |
| **Market Data Connection Drops** | **VERY LOW** | WebSocket supervision loop with exponential backoff and REST fallback | Tested and proven during 1-hour EC2 Stage 1 observation. |

---

## 2. Invariant Checklist

1. **Trading Mode**: STRICT PAPER ONLY (`TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`).
2. **Order Submission**: Strictly ZERO live or external exchange order submission.
3. **Position Size Cap**: Strictly 1 micro-lot maximum (`APPROVED_MAX_PAPER_LOTS = 1`, `CONTRACT_SIZE_XAUT = 0.001`). Central Hard Position Size Guard rejects any order > 1 lot.
4. **Historical Incident Integrity**: Trade `PAPER_20261105_101000_LONG` (484 lots, -$7.9472) preserved completely intact in `data/paper_forward_24h_state.json`.
5. **Cash / Equity Accounting**: Cash conservation equation $\Delta \text{Equity} \equiv \text{Net Realized P\&L}$ holds across all tests.
