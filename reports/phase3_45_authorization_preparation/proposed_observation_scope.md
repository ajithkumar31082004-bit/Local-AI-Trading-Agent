# Phase 3.45 — Proposed Observation Scope & Operational Boundaries

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Current Branch:** `feature/phase3-45-dual-human-authorization-prep`  
**Target Instrument:** `XAUTUSD` | **Exchange:** Delta Exchange India  
**Execution Mode:** STRICT READ-ONLY SHADOW  
**Document Status:** PROPOSED LIMITS FOR DUAL HUMAN REVIEW ONLY — EXECUTION STRICTLY PROHIBITED  

---

## 1. Executive Definition of Observation Scope

This document specifies the exact, non-negotiable operational boundaries proposed for a future, separately authorized 60-minute public market-data observation dry run.

These parameters have been modeled in code within [`DryRunConfig`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/dry_run_runner.py#L47-L87) and [`TransportConfig`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/transport/transport_config.py#L14-L45) and verified across 143 offline tests.

---

## 2. Core Boundary Specifications

| Boundary Dimension | Proposed Operational Setting | Code Enforcement Mechanism | Safety Rationale |
|---|---|---|---|
| **Target Instrument** | `XAUTUSD` ONLY | Enforced in `DryRunConfig.__post_init__` and `CandleValidator.validate_incoming_candle`. Any other symbol causes immediate frame drop or safety violation. | Prevents foreign instrument ingestion or cross-market data contamination. |
| **Exchange** | Delta Exchange India | Enforced in `TransportConfig.validate_config`. Only `*.india.delta.exchange` domains are permitted. | Confines transport strictly to authorized regional exchange infrastructure. |
| **Public REST Endpoint** | `https://api.india.delta.exchange/v2/tickers/XAUTUSD` | `PublicRestClient.fetch_ticker_snapshot`. Restricted to initial preflight check. | Snapshot price check only; zero account or trade routes accessible. |
| **Public WS Channels** | `v2/ticker` & `candlestick_5m` for `XAUTUSD` | Subscription payload in `PublicWebSocketClient.get_subscription_payload`. | Ingests real-time price updates and completed 5-minute candles only. |
| **Observation Duration** | Exactly $\le 60\text{ minutes}$ ($3,600.0\text{s}$) | Monotonic countdown timer in `ShadowDryRunRunner.evaluate_stop_conditions`. Trips `DURATION_EXPIRED` at $t \ge 3600\text{s}$. | Hard time-box prevents open-ended runtime, runaway daemon processes, or memory bloat. |
| **Process Memory Budget** | $\le 150.0\text{ MB}$ RSS | Tracked via `memory_check_fn` on every loop tick. Trips `MEMORY_BUDGET_EXCEEDED` if usage $>150.0\text{ MB}$. | Guarantees lightweight footprint; eliminates risk of host memory starvation. |
| **Disk & Log Budget** | $\le 25.0\text{ MB}$ cumulative storage | Directory quota check on `data/shadow_observer/`. Trips `DISK_BUDGET_EXCEEDED` if size $>25.0\text{ MB}$. | Protects workstation/EC2 disk from volume saturation or uncontrolled log dumping. |
| **In-Memory Event Queue** | $\le 1,000\text{ items}$ | Bounded ring buffer `collections.deque(maxlen=1000)`. Any drop trips `QUEUE_OVERFLOW` immediately. | Bounds worst-case queue RAM consumption to $\approx 240\text{ KB}$; prevents silent data loss. |
| **Reconnect Limit** | Max 5 consecutive attempts | `ReconnectController` with exponential backoff (2s, 4s, 8s, 16s, 32s capped at 60s). Trips `MAX_RECONNECTS_EXCEEDED` on 5th failure. | Prevents infinite reconnect loops or exchange rate-limit bans during network degradation. |
| **Authentication Keys** | ZERO (Unauthenticated) | Preflight Gate 1 fails startup if `DELTA_API_KEY` or `DELTA_API_SECRET` is set. Dynamic scanner halts on `api-key` in payload. | Absolute zero credential leakage risk. |
| **Account Endpoints** | STRICTLY FORBIDDEN | Banned keyword regex (`/v2/orders*`, `/v2/positions*`, `/v2/wallet*`, `/v2/fills*`) raises `ShadowObserverSafetyViolation`. | Total barrier against account query or mutation. |
| **Order Execution** | ZERO (Disabled) | Zero imports of broker classes. Hypothetical signals recorded to local JSON summary only. | Zero financial risk; cannot submit orders under any condition. |
| **Restart Policy** | STRICTLY `"never"` | `ShadowDryRunRunner.has_terminated` permanently blocks restart post-shutdown. | Eliminates automatic daemon flapping or unmonitored crash recovery. |

---

## 3. Explicit Operational Non-Authorization Notice

> [!IMPORTANT]
> **PROPOSAL SPECIFICATION ONLY:**  
> The limits defined in this document represent the formal proposal to be reviewed, verified, and signed off on the [Dual-Human Authorization Form](file:///C:/Users/ajith/Downloads/Local_AI_Agent/reports/phase3_45_authorization_preparation/dual_human_authorization_form.md).  
> 
> **Documenting these limits does NOT grant permission to execute the observation.** Execution requires completion of dual physical signatures, user authorization, and a separately initiated phase.
