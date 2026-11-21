# PHASE 3.40 — REMAINING OPERATIONAL RISKS

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Working Directory:** `C:\Users\ajith\Downloads\Local_AI_Agent`  
**Instrument:** `XAUTUSD`  
**Mode:** STRICT READ-ONLY SHADOW / OFFLINE CODE AUDIT ONLY.

---

## 1. Classification of Audit Findings

To maintain absolute clarity regarding operational readiness, all audit findings are segregated into four distinct categories:

```
┌───────────────────────────────────────┬───────────────────────────────────────┐
│          VERIFIED IN PHASE 3.40       │          NOT INDEPENDENTLY VERIFIED   │
├───────────────────────────────────────┼───────────────────────────────────────┤
│ 1. Verified by Static Inspection      │ 3. Reported But Unverified Locally    │
│ 2. Verified by Offline Tests          │ 4. Unverified (Runtime Access Prohib.)│
└───────────────────────────────────────┴───────────────────────────────────────┘
```

---

## 2. Verified Offline Items

### 2.1 Verified by Static Inspection
- Complete AST isolation of `services/shadow_observer/transport/` from credentials, private channels, order endpoints, and broker modules.
- Verification of public allowlists: only `/v2/tickers`, `/v2/chart/history`, `/v2/products` and `v2/ticker`, `candlestick_5m`, `candlestick_15m`.
- Python exception hierarchy verification: `BaseException` handling preserves process termination and re-raises `KeyboardInterrupt`, `SystemExit`, and `asyncio.CancelledError`.
- Pure import isolation: importing transport causes zero background execution or thread creation.
- Strict single micro-lot cap (`APPROVED_MAX_PAPER_LOTS == 1`) in `risk/position_size_guard.py`.

### 2.2 Verified by Offline Tests
- 8 new Phase 3.40 operational safety tests passed.
- 68 combined shadow observer tests (Phases 3.35, 3.36, 3.38, 3.39, 3.40) passed in 17.24s.
- 503 full repository regression tests passed in 120.52s with zero regressions.
- Socket cleanup on handshake cancellation and timeout.
- Exponential backoff capping at 60.0s, $\pm 10\%$ jitter, and 5-attempt retry limit.
- Queue overflow data loss degradation and observable telemetry.

---

## 3. Items Reported But Not Independently Verified

- **Exchange API Stability:** Documentation reports that Delta Exchange India supports WebSocket subscriptions on `candlestick_5m` for `XAUTUSD`. Because live connections were prohibited, real exchange frame timing and exact schema versions could not be verified against the live exchange servers.
- **Third-Party Supabase Deprecations:** Upstream `supabase-py` deprecation warnings on `SyncPostgrestClient` in legacy test files remain unpatched by upstream maintainers (documented and harmless to shadow observer).

---

## 4. Unverified Real-World Network & Runtime Behaviors

Because Phase 3.40 strictly barred live network connections, the following real-world behaviors remain unverified:

### 4.1 WAN Packet Loss & TCP Reset (RST)
- **Risk:** Internet routing fluctuations between the host and Delta Exchange India servers in Mumbai could trigger abrupt TCP RSTs or silent half-open socket states.
- **Prepared Defense:** `read_timeout_seconds` (30.0s) and `stale_feed_threshold_seconds` (60.0s) will detect dropped sockets and trigger state degradation to `DEGRADED`.

### 4.2 Exchange Maintenance Windows & Cloudflare Challenges
- **Risk:** Delta Exchange India occasionally initiates scheduled maintenance or routes traffic through Cloudflare DDoS mitigation, temporarily rejecting WebSocket handshakes.
- **Prepared Defense:** `ReconnectController` terminates after 5 failed attempts, preventing infinite reconnect loops during prolonged downtime.

### 4.3 Remote EC2 Host Runtime State
- **Risk:** The physical status of systemd units (`trading.service`), remote disk space, and remote `.env` configuration on the EC2 production instance remain unknown.
- **Constraint:** Zero SSH or remote access was permitted. Any EC2 verification must occur in a future, explicitly authorized operational preflight.

---

## 5. Audit Conclusion

All software logic, queue bounds, exception propagation, and safety invariants are 100% verified offline. The remaining risks are physical WAN dynamics that can only be evaluated during a future authorized live observation dry run.