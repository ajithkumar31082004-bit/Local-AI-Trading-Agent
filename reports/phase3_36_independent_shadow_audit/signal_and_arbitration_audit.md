# Phase 3.36 — Signal Recording & Arbitration Architecture Audit

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `f3432c8`  
**Audit Branch:** `feature/phase3-36-independent-shadow-audit`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Technical inspection of signal evaluation, arbitration orchestration, hypothetical-event schema validation, and persistence safety in `services/shadow_observer/strategy_evaluator.py` and `signal_recorder.py`.

---

## 1. Executive Summary

The purpose of the shadow observer is to evaluate live public market feeds and record hypothetical strategy signals without creating simulated orders, deducting fees, or modifying broker ledgers.

This audit verified that:
1. Every evaluation event is strictly labeled `"HYPOTHETICAL — NOT AN ORDER"` with `"is_order": false`.
2. No broker execution fields (`trade_id`, `fill_price`, `commission`, `cash_balance`, `equity`, `pnl`) exist in recorded events.
3. The arbitration engine properly coordinates all 6 strategies and conditions execution on regime classification.
4. Signal recording utilizes append-only JSONL streaming and atomic summary replacement.
5. Re-initialization of the recorder safely reloads disk state without data corruption.

---

## 2. Event Schema & Non-Order Purity

Every evaluation produces a `ShadowEvaluationEvent` defined in [`services/shadow_observer/strategy_evaluator.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/strategy_evaluator.py).

### 2.1 Schema Definition

```python
@dataclass
class ShadowEvaluationEvent:
    event_id: str
    timestamp_utc: str
    timestamp_ist: str
    instrument: str
    candle_timeframe: str
    market_regime: str
    regime_confidence: float
    is_warming_up: bool
    winning_strategy: str
    winning_signal: str  # 'BUY', 'SELL', 'NO_SIGNAL'
    entry_reference_price: Optional[float]
    hypothetical_sl: Optional[float]
    hypothetical_tp: Optional[float]
    risk_reward_ratio: float
    signal_quality_score: float
    rejection_reason: Optional[str]
    diagnostic_code: str
    sub_strategy_signals: Dict[str, Dict[str, Any]]
    label: str = "HYPOTHETICAL — NOT AN ORDER"
    is_order: bool = False
```

### 2.2 Absence of Execution Artifacts

The audit performed an automated invariant check (`test_adv_10_invariant_schema_purity`) confirming that the schema contains zero execution or accounting artifacts:
- No `trade_id`
- No `filled_price`
- No `commission`
- No `cash_balance`
- No `equity`
- No `net_pnl`

Before appending to disk, `ShadowSignalRecorder.record_event()` asserts:
```python
assert event_dict.get("label") == "HYPOTHETICAL — NOT AN ORDER", "Must be labeled hypothetical"
assert event_dict.get("is_order") is False, "Must not be flagged as an order"
```
Any violation causes an immediate runtime assertion error, failing closed.

---

## 3. Master Arbitration Integration

In [`services/shadow_observer/strategy_evaluator.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/strategy_evaluator.py), `ReadOnlyStrategyEvaluator.evaluate_arbitrated_shadow()` invokes `arbitrate_strategies(df, self.cfg)`.

### 3.1 Regime-Based Gating
- The arbitrator executes `detect_market_regime(df, cfg)` to classify the market into one of five states:
  1. `BULL_TREND`
  2. `BEAR_TREND`
  3. `RANGING`
  4. `VOLATILE`
  5. `UNCERTAIN`
- Gating rules:
  - In `RANGING` regimes: Trend-following signals are suppressed.
  - In `BULL_TREND` / `BEAR_TREND`: Mean-reversion signals are suppressed.
  - In `UNCERTAIN` or `WARMING_UP`: All signals are rejected with `NO_SIGNAL` and diagnostic codes.

### 3.2 Sub-Strategy Telemetry Capture
The event captures not only the winning arbitrated signal, but the complete state of all 6 individual strategies in `sub_strategy_signals`. This allows post-observation performance attribution across every individual strategy.

---

## 4. Append-Only Streaming & Atomic Persistence

In [`services/shadow_observer/signal_recorder.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/signal_recorder.py):
1. **JSONL Stream:** Each event is appended as a single JSON line to `shadow_signals.jsonl`.
2. **Summary File:** Cumulative metrics (`total_events_recorded`, `signals_by_type`, `signals_by_strategy`, `signals_by_regime`, `warmup_events`) are serialized to `shadow_signals_summary.tmp` and atomically replaced to `shadow_signals_summary.json`.
3. **Restart Resilience:** Tested via `test_9_recorder_restart_and_persistence`. When `ShadowSignalRecorder` is instantiated over an existing directory, `load_events()` and `load_summary()` seamlessly reconstruct the recorded state without overwriting or truncating prior history.

---

## 5. Audit Test Verification

| Test Name | Verification Focus | Result |
| :--- | :--- | :---: |
| `test_7_signal_generation_synthetic_data` | Synthetic candle processing across all 6 strategies | **PASSED** |
| `test_8_rejection_reason_recording` | Accurate capture of rejection reasons and warmup flags | **PASSED** |
| `test_9_recorder_restart_and_persistence` | Restart recovery and persistence | **PASSED** |
| `test_10_output_file_integrity_and_schema` | Verification of schema fields and non-order labels | **PASSED** |
| `test_adv_08_empty_and_warmup_handling` | Graceful `NO_SIGNAL` handling for empty/insufficient data | **PASSED** |
| `test_adv_10_invariant_schema_purity` | Absence of execution/broker fields | **PASSED** |
| `test_adv_11_multi_threaded_concurrency_stress` | Thread-safe concurrent event recording | **PASSED** |

---

## 6. Audit Verdict

**PASS**. The signal recording and arbitration architecture strictly enforces non-order semantics, preserves rich diagnostic telemetry, and maintains full crash-recovery integrity.
