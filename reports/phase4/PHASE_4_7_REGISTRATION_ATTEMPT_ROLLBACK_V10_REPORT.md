# Phase 4.7 — V10 Registration-Attempt Tracking & Atomic Rollback Remediation Report

**Date:** 2026-11-17  
**Audit Target:** Phase 4.7 Forward Validation — Candidate V10  
**Governing Posture:** LOCKED ON HOLD | Acceptance STRICTLY WITHHELD  
**Operational Boundaries:** `TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=False`, Live Orders = 0, EC2/SSH = 0, PR #3 = OPEN / UNMERGED (`main` pinned at `2f348e4`).

---

## 1. Executive Summary

This report delivers the comprehensive technical remediation of the partial callback registration edge case identified in the **Phase 4.7 — V9 Independent Re-Audit**:
1. **Pre-Invocation Registration Attempt Tracking:** Replaced post-return success flags with pre-invocation attempt tracking (`candle_registration_attempted = True`, `tick_registration_attempted = True`). If an injected feed's registration method attaches the callback to its internal state and subsequently raises an exception before returning, the rollback sequence unconditionally executes the corresponding unregister operation.
2. **Safe Idempotent Teardown:** The unregistration handlers safely remove the callback if present, or act as a harmless no-op if the callback was never attached prior to raising.
3. **Adversarial Verification:** Added 2 new focused adversarial unit tests (Tests 32 & 33) specifically covering registration methods that append handlers internally and then raise exceptions, confirming zero leaked callbacks post-rollback.
4. **Complete Regression Suite:** Full 7-suite regression battery expanded to **150 tests**, achieving 100% pass rate locally and in an isolated sandbox.

---

## 2. Remediation Matrix

| Finding ID | Audit Finding | V9 Defect Description | V10 Remediation Implementation | Verification Test |
|---|---|---|---|---|
| **F6.1** | **Candle Registration Attach & Raise** | `candle_registered = True` occurred after `register_candle_callback()`. If registration attached the callback and then raised, the callback was leaked. | Set `candle_registration_attempted = True` before invoking `self.feed.register_candle_callback()`. Rollback unconditionally unregisters if attempt was made. | `test_32_partial_candle_registration_attaches_and_raises_rollback` |
| **F6.2** | **Tick Registration Attach & Raise** | `tick_registered = True` occurred after `register_tick_callback()`. If registration attached the callback and then raised, the tick callback was leaked. | Set `tick_registration_attempted = True` before invoking `self.feed.register_tick_callback()`. Rollback unconditionally unregisters if attempt was made. | `test_33_partial_tick_registration_attaches_and_raises_rollback` |

---

## 3. Detailed Source Implementation (`services/delta_paper_bot.py`)

```python
    def start(self, bootstrap_lookback: Optional[int] = None) -> bool:
        """Start live market data feed and register event callbacks with atomic rollback."""
        target_lookback = bootstrap_lookback or getattr(self.cfg, "bootstrap_lookback_candles", 450)
        with self._lock:
            if self._is_running:
                logger.info("DeltaPaperBot is already running.")
                return True

            # Fail-closed startup governance safety verification
            if self.gov_supervisor is None:
                raise PaperSafetyViolation("FATAL: DeltaPaperBot requires active GovernanceSupervisor to start.")
            enforce_fn = getattr(self.gov_supervisor, "enforce_startup_governance_safety", None)
            if not callable(enforce_fn):
                raise PaperSafetyViolation(
                    "FATAL: Injected gov_supervisor lacks mandatory 'enforce_startup_governance_safety' method or method is not callable."
                )
            self.gov_supervisor.enforce_startup_governance_safety()

            logger.info("Starting DeltaPaperBot for %s [Mode: %s, Strategy: %s, Bootstrap: %d bars]...", self.cfg.symbol, self.cfg.mode, self._strategy_type, target_lookback)
            self._log_event("SYSTEM", f"Starting DeltaPaperBot for {self.cfg.symbol} in PAPER mode.")

            candle_registration_attempted = False
            tick_registration_attempted = False
            start_attempted = False
            try:
                # Register callbacks with feed independently (attempts tracked prior to invocation)
                if self.feed is not None:
                    if hasattr(self.feed, "register_candle_callback"):
                        candle_registration_attempted = True
                        self.feed.register_candle_callback(self._on_completed_candle)
                    if hasattr(self.feed, "register_tick_callback"):
                        tick_registration_attempted = True
                        self.feed.register_tick_callback(self._on_tick)

                    # Start real-time feed (attempt tracked prior to invocation)
                    if hasattr(self.feed, "start"):
                        start_attempted = True
                        self.feed.start(bootstrap_lookback=target_lookback)

                # Evaluate initial historical batch to initialize signal state
                if self.feed is not None and hasattr(self.feed, "get_completed_5m_candles"):
                    initial_5m = self.feed.get_completed_5m_candles()
                    if hasattr(initial_5m, "empty") and not initial_5m.empty:
                        logger.info("Evaluating initial completed candle batch (%d bars)...", len(initial_5m))
                        self._evaluate_strategy(initial_5m)

                self._is_running = True
                return True

            except Exception as exc:
                logger.error("FATAL: DeltaPaperBot startup failed. Executing atomic rollback: %s", exc)
                self._is_running = False
                cleanup_errors: List[str] = []

                # Rollback step 1: Stop feed whenever startup was attempted (even partially)
                if start_attempted and self.feed is not None and hasattr(self.feed, "stop"):
                    try:
                        self.feed.stop()
                    except Exception as stop_exc:
                        cleanup_errors.append(f"Feed stop failed: {stop_exc}")
                        logger.warning("Error during feed stop in rollback: %s", stop_exc)

                # Rollback step 2: Unregister tick callback whenever registration was attempted
                if tick_registration_attempted and self.feed is not None:
                    if hasattr(self.feed, "unregister_tick_callback"):
                        try:
                            self.feed.unregister_tick_callback(self._on_tick)
                        except Exception as cb_exc:
                            cleanup_errors.append(f"Tick callback unregister failed: {cb_exc}")
                    elif hasattr(self.feed, "_tick_callbacks") and isinstance(self.feed._tick_callbacks, list):
                        try:
                            if self._on_tick in self.feed._tick_callbacks:
                                self.feed._tick_callbacks.remove(self._on_tick)
                        except Exception as cb_exc:
                            cleanup_errors.append(f"Tick callback removal failed: {cb_exc}")

                # Rollback step 3: Unregister candle callback whenever registration was attempted
                if candle_registration_attempted and self.feed is not None:
                    if hasattr(self.feed, "unregister_candle_callback"):
                        try:
                            self.feed.unregister_candle_callback(self._on_completed_candle)
                        except Exception as cb_exc:
                            cleanup_errors.append(f"Candle callback unregister failed: {cb_exc}")
                    elif hasattr(self.feed, "_candle_callbacks") and isinstance(self.feed._candle_callbacks, list):
                        try:
                            if self._on_completed_candle in self.feed._candle_callbacks:
                                self.feed._candle_callbacks.remove(self._on_completed_candle)
                        except Exception as cb_exc:
                            cleanup_errors.append(f"Candle callback removal failed: {cb_exc}")

                if cleanup_errors:
                    self._log_event(
                        "SYSTEM",
                        f"Startup failed with exception: {exc}. Rollback encountered secondary cleanup errors: {'; '.join(cleanup_errors)}"
                    )
                else:
                    self._log_event("SYSTEM", f"Startup failed with exception: {exc}. Atomic rollback completed cleanly.")

                raise
```

---

## 4. Adversarial Regression Suite Expansion (33 Tests)

The Tier 0 Circuit Breaker suite (`tests/test_tier0_governance_circuit_breaker.py`) expanded from 31 to **33 tests**:
- **Test 32 (`test_32_partial_candle_registration_attaches_and_raises_rollback`):** When `register_candle_callback` attaches the handler internally and raises an exception before returning, verifies that rollback unregisters the handler, subsequent lifecycle steps are not executed, `_is_running is False`, and the original exception propagates.
- **Test 33 (`test_33_partial_tick_registration_attaches_and_raises_rollback`):** When `register_tick_callback` attaches the handler internally and raises an exception before returning, verifies that rollback unregisters both the candle and tick handlers, feed start is not attempted, `_is_running is False`, and the original exception propagates.

---

## 5. Full Regression Suite Results (150 / 150 Passed)

| Test Suite File | Test Count | Result | Execution Time |
|---|---|---|---|
| `tests/test_dashboard_state_consistency.py` | 6 | **PASS** | 0.81s |
| `tests/test_paper_trade_accounting.py` | 16 | **PASS** | 1.02s |
| `tests/test_deployment_tracker_regression.py` | 5 | **PASS** | 0.43s |
| `tests/test_ci_security_scanner_regression.py` | 5 | **PASS** | 0.40s |
| `tests/test_monitoring_api.py` | 65 | **PASS** | 7.55s |
| `tests/test_phase4_7_forward_analytics.py` | 20 | **PASS** | 2.05s |
| `tests/test_tier0_governance_circuit_breaker.py` | 33 | **PASS** | 4.72s |
| **Total Full Battery** | **150** | **100% PASS** | **22.98s** |

### Isolated Sandbox Verification
- **Sandbox Directory:** `C:\Users\ajith\AppData\Local\Temp\v10_independent_sandbox`
- **Result:** **150 / 150 passed in 18.46s** (Exit Code: 0).

---

## 6. Candidate V10 Archive Manifest & Integrity

| Check | Specification / Value |
|---|---|
| **Archive File** | `review_files_v10_phase4_7.zip` |
| **File Size** | `5,707,997` bytes |
| **SHA-256 Checksum** | `e4e97890202e113394a5d018be1fafb40f2a9b92b8cf3c47027e110aeaa68c2e` |
| **Total Archive Entries** | `219` |
| **Added Files (vs V9)** | `0` |
| **Removed Files (vs V9)** | `0` |
| **Modified Files (vs V9)** | `3` (`services/delta_paper_bot.py`, `test_tier0_governance_circuit_breaker.py`, `tests/test_tier0_governance_circuit_breaker.py`) |
| **Common Path Identity** | `215` entries byte-identical to benchmark archives |
| **Historical Benchmark State** | `data/paper_forward_24h_state.json` (SHA-256: `DAD10844B0E77ED19DB058EEC1E541AA858EB8E2D3EA1ADA66D83A69390CED25`) |

---

## 7. Standing Disposition

| Control Parameter | Status |
|---|---|
| **Phase 4.7 Forward Validation** | **LOCKED ON HOLD** |
| **Acceptance** | **WITHHELD** |
| **Gate 1 — Independent Verification** | **PENDING V10 INDEPENDENT AUDIT** |
| **Gates 2–5** | **CLOSED** |
| **Trading Mode** | **PAPER ONLY** (Max 0.001 XAUTUSD, 1 order/session) |
| **Live Exchange Execution** | **PROHIBITED** |
| **Pull Request #3** | **OPEN / UNMERGED** |
| **Mainline Branch (`origin/main`)** | **PINNED at `2f348e4` (FROZEN)** |
