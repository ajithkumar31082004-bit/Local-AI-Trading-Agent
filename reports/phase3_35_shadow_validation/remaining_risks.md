# Phase 3.35 — Remaining Risks & Operational Considerations

This document details residual operational risks, environmental constraints, and risk-mitigation measures for any future authorized shadow observation run.

---

## 1. Public Market Data Constraints

| Risk Factor | Operational Impact | Mitigation Implemented |
| :--- | :--- | :--- |
| **Public REST Rate Limits** | Excessive polling on `api.india.delta.exchange` could result in HTTP 429 Too Many Requests. | Primary data ingestion relies on public WebSocket streaming (`wss://socket.india.delta.exchange`). REST is queried only on initial startup or gap recovery. |
| **WebSocket Feed Interruption** | Network disconnections could cause missing 5m candles. | `CandleValidator` detects timestamp gaps ($>300\text{s}$) and marks the feed as unfinalized until gapless history is restored. |
| **Stale Feed Stagnation** | Exchange freeze or socket hang where no new ticks arrive. | `PublicMarketDataReader` implements a 60-second staleness threshold (`is_feed_stale`), blocking strategy evaluation until fresh data arrives. |

---

## 2. Observer Execution Safety

| Potential Hazard | Severity | Isolation Enforcement |
| :--- | :--- | :--- |
| **Accidental Order Placement** | Critical | **Completely Eliminated**. The shadow observer module does not import `DeltaPaperBroker`, does not load exchange API keys, and has zero execution methods. |
| **Broker State Contamination** | High | **Protected**. `validate_shadow_write_path` denies writes to `paper_account_xautusd.json`, `paper_forward_24h_state.json`, and `.idempotency.db`. |
| **Storage Consumption** | Low | At 5-minute intervals, the observer generates 288 records per day (~144 KB/day). Negligible footprint on disk. |

---

## 3. Mandatory Authorization Gate

No live observation run is permitted without explicit human instruction. All preparation steps completed in Phase 3.35 were strictly offline using synthetic fixtures and local files.
