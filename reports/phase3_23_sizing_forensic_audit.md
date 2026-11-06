# Phase 3.23 — Forensic Audit: Position-Sizing Architecture & Vulnerability Analysis

**Date:** 2026-11-06  
**Auditor:** Senior Financial Risk & Algorithmic Trading Systems Engineer  
**Branch:** `feature/phase3-23-position-size-guard`  
**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Scope:** Full codebase search and forensic audit of all position-sizing, quantity calculation, and order submission paths.

---

## 1. Executive Summary & Incident Forensic Breakdown

### 1.1 The Critical Incident (`PAPER_20261105_101000_LONG`)
During the 24-hour forward paper trading test on AWS EC2, the trading engine generated and executed an order vastly exceeding the authorized forward-test specification of **1 micro-lot (0.001 XAUT)**:

| Field | Historical Record Value | Specification Limit | Violation Magnitude |
| :--- | :--- | :--- | :--- |
| **Trade ID** | `PAPER_20261105_101000_LONG` | - | - |
| **Execution Quantity** | **484 lots** | **1 lot** | **484x Oversized** |
| **Physical Contract Qty** | **0.484 XAUT** | **0.001 XAUT** | **484x Oversized** |
| **Entry Price** | \$4,127.90 | - | - |
| **Exit Price (Stop Loss)** | \$4,113.95 | - | - |
| **Planned Dollar Risk** | \$6.3551 | ≤ \$50.00 (0.5%) | Met budget, but blew lot ceiling |
| **Gross Loss (Fill-to-Fill)** | -\$6.7518 | - | - |
| **Brokerage Fees (Maker+Taker)**| -\$1.1954 | - | 484x fee inflation |
| **Net Realized P&L** | **-\$7.9472 (-1.25R)** | - | - |

### 1.2 Mathematical & Architectural Root Cause
1. **Dynamic Risk Budget Sizing Formula:**
   In [risk/position_sizing.py](file:///C:/Users/ajith/Downloads/Local_AI_Agent/risk/position_sizing.py#L74-L97), `size_position()` implements a classical percent-risk formula:
   $$\text{risk\_budget} = \text{equity} \times \frac{\text{risk\_pct}}{100} = \$10,000 \times 0.005 = \$50.00$$
   $$\text{stop\_distance} = |4127.90 - 4113.95| = \$13.95$$
   $$\text{per\_lot\_risk} \approx (13.95 \times 0.001) + \text{friction} \approx \$0.02102$$
   $$\text{raw\_lots} = \lfloor \$50.00 / \$0.02102 \rfloor = 2,378 \text{ lots}$$
   After margin clamping at leverage 2.0x / max margin allocation 10%:
   $$\text{margin\_per\_lot} = \frac{4127.90 \times 0.001}{2.0} = \$2.06395$$
   $$\text{max\_margin\_lots} = \lfloor \$1000 / \$2.06395 \rfloor = 484 \text{ lots}$$
   `size_position()` approved and returned **484 lots**.

2. **The Missing Boundary Check in Paper Broker:**
   In [services/delta_paper_broker.py](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/delta_paper_broker.py#L204-L214):
   ```python
   # Line 204: Only checks lower bound!
   if lots < 1:
       return False, f"Requested lots must be >= 1 ...", None
   ```
   There was **zero upper bound check** on `lots`. The broker did not enforce `lots <= 1` or check an approved maximum. It accepted 484 lots, verified that available paper cash covered \$40 margin, and executed the trade.

---

## 2. Exhaustive Codebase Sizing Inventory

A repository-wide grep for `size_position`, `quantity`, `lots`, `risk_budget`, `contracts`, `execute_order`, and `open_simulated_trade` identified all sizing functions and order generation paths:

### 2.1 Sizing & Risk Evaluation Functions

| # | File Path | Function Name | Input Parameters | Sizing Method / Output |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `risk/position_sizing.py:45` | `size_position()` | `entry, stop, equity, cfg` | Dynamic: `min(risk_budget, margin_cap)`. Returns `RiskDecision(lots=int)`. |
| 2 | `risk/position_sizing.py:31` | `estimate_lot_risk()` | `entry, stop, cfg` | Computes dollar risk for exactly 1 lot (`gross + fees + slip`). |
| 3 | `services/delta_risk.py:161` | `evaluate_delta_trade()` | `entry, sl, equity_inr, ...` | INR tiny-account dynamic sizing: `min(max_risk_lots, max_margin_lots)`. |
| 4 | `services/delta_risk.py:128` | `compute_lot_risk_and_costs()`| `entry, sl, lots, ...` | Computes USD & INR friction and margin for arbitrary `lots`. |
| 5 | `services/risk_manager.py:72` | `RiskManager.evaluate_order()`| `symbol, side, qty, price, ...`| Classical equity sizing: `min(risk_qty, alloc_qty, cash_qty)`. Returns `RiskCheckResult`. |
| 6 | `agents/risk_agent.py:15` | `RiskAgent.audit_trade()` | `symbol, side, qty, price, ...`| LLM wrapper around `RiskManager.evaluate_order()`. |

### 2.2 Strategy Signal Generators (Inspect: All 10+ Strategies)

All registered strategies across both `strategy/` (V2 multi-strategy intelligence engine) and `strategies/` (classical agent engine) were audited:

| Strategy Name | Source File | Signal Function | Dictates Quantity? | Sizing Mechanism |
| :--- | :--- | :--- | :---: | :--- |
| **ORB (EMA + Volume)** | `strategy/orb_ema_volume.py` | `evaluate_orb()` | **NO** | Returns `Signal(entry, sl, tp)`. Quantity is omitted. |
| **VWAP + EMA** | `strategy/vwap_ema.py` | `evaluate_vwap_ema()` | **NO** | Returns `Signal(entry, sl, tp)`. Quantity is omitted. |
| **Trend Following (V2)** | `strategy/trend_following.py` | `evaluate_trend_following()`| **NO** | Returns `Signal(entry, sl, tp)`. Quantity is omitted. |
| **Bollinger Bands + RSI** | `strategy/bb_rsi_mean_reversion.py`| `evaluate_bb_rsi()` | **NO** | Returns `Signal(entry, sl, tp)`. Quantity is omitted. |
| **ATR Compression Breakout**| `strategy/atr_compression_breakout.py`| `evaluate_atr_compression_breakout()`| **NO** | Returns `Signal(entry, sl, tp)`. Quantity is omitted. |
| **Liquidity Sweep** | `strategy/liquidity_sweep.py` | `evaluate_liquidity_sweep()`| **NO** | Returns `Signal(entry, sl, tp)`. Quantity is omitted. |
| **Strategy Arbitrator** | `strategy/arbitrator.py` | `arbitrate_signals()` | **NO** | Selects winning `Signal`. Quantity is omitted. |
| **Moving Average Cross** | `strategies/moving_average.py` | `generate_signal()` | **NO** | Returns `StrategySignal(BUY/SELL)`. Quantity omitted. |
| **RSI Confirmation** | `strategies/rsi_strategy.py` | `generate_signal()` | **NO** | Returns `StrategySignal(BUY/SELL)`. Quantity omitted. |
| **Trend Following (V1)** | `strategies/trend_following.py` | `generate_signal()` | **NO** | Returns `StrategySignal(BUY/SELL)`. Quantity omitted. |
| **Custom Strategy** | `strategies/custom_strategy.py` | `generate_signal()` | **NO** | Returns `StrategySignal(BUY/SELL)`. Quantity omitted. |

> **Audit Finding:** Strategies themselves **do not dictate order quantities**. They exclusively generate market direction, entry price, stop-loss, and take-profit targets. The quantity is determined downstream in execution harnesses.

---

## 3. Order-Generation Paths & Execution Boundaries

We identified **three distinct order-execution pathways** in the repository:

```
[PATHWAY 1: Delta 24h Paper Trading Bot (EC2 Forward Test)]
Strategy Signal (V2)
       ↓
DeltaPaperBot._evaluate_strategy()
       ↓
Calls risk/position_sizing.py: size_position() ---> (BUG: Sized 484 lots!)
       ↓
DeltaPaperBroker.open_simulated_trade(lots=484) ---> (BUG: Missing upper bound check!)
       ↓
Simulated Position Opened with 484 Lots (CRITICAL VULNERABILITY)

-------------------------------------------------------------------------

[PATHWAY 2: Agent Supervisor / Streamlit Paper Trading]
Streamlit UI (ui/paper_trading.py) OR Supervisor (trading_supervisor.py)
       ↓
Supplies arbitrary 'quantity: float' (default 10.0 in UI)
       ↓
Calls services/paper_broker.py: execute_order()
       ↓
Calls services/risk_manager.py: RiskManager.evaluate_order()
       ↓ (VULNERABILITY: 'bypass_risk=True' parameter exists in paper_broker.py!)
Simulated Trade Recorded in SQLite Database

-------------------------------------------------------------------------

[PATHWAY 3: Live Trading Adapter (Safety Locked)]
Strategy Signal
       ↓
services/delta_live_executor.py: execute_candle_signal()
       ↓
Gate 1: Hard assertion 'LIVE_TRADING_ENABLED=False' raises Exception.
Gate 2: Real order submission is permanently disabled.
```

---

## 4. Quantity Conversion & Bypass Vulnerabilities

| Vulnerability ID | Location | Description | Severity |
| :--- | :--- | :--- | :---: |
| **VULN-01** | `services/delta_paper_broker.py:204` | **Missing Upper Lot Bound:** Only enforces `lots >= 1`. Accepts any positive integer (e.g., 484, 10,000). | **CRITICAL** |
| **VULN-02** | `services/delta_paper_bot.py:249` | **Unconstrained Dynamic Sizing Call:** Directly assigns `lots = sizing.lots` from `size_position()` without checking mode or 1-lot cap. | **CRITICAL** |
| **VULN-03** | `services/paper_broker.py:36` | **Risk Bypass Parameter:** `execute_order(..., bypass_risk=False)` allows caller to bypass `RiskManager`. | **HIGH** |
| **VULN-04** | `services/paper_broker.py:30` | **Float / Non-Integer Quantities:** Accepts `quantity: float` (e.g. 10.5 shares or contracts) rather than discrete lots. | **MEDIUM** |
| **VULN-05** | `services/delta_risk.py:258` | **Unclamped INR Dynamic Sizing:** `evaluate_delta_trade()` calculates `lots = min(max_risk_lots, max_margin_lots)` without 1-lot clamp. | **HIGH** |
| **VULN-06** | `risk/position_sizing.py:90` | **Absence of Sizing Mode Switch:** `size_position()` lacks an explicit fixed-lot policy argument for forward testing. | **HIGH** |

---

## 5. Architectural Remediation Plan (Phases 2 – 4)

To eliminate all bypasses deterministically, the remediation must implement **Defense in Depth**:

1. **Central Hard Guard Module (`risk/position_size_guard.py`):**
   - Define immutable constant: `APPROVED_MAX_PAPER_LOTS = 1`.
   - Provide strict validation function `validate_paper_order_lots(lots: Any) -> int`.
   - Strictly reject:
     - Non-integer types (`float`, `str`, `list`, `dict`).
     - Booleans masquerading as integers (`isinstance(True, int)` is True in Python!).
     - Missing or `None` values.
     - `NaN`, `+Infinity`, `-Infinity`.
     - Non-positive values ($\le 0$).
     - Values $> 1$ (e.g. 2, 10, 100, 484, 495).
   - **Zero silent clamping:** Oversized orders must raise `PositionSizeViolation` with an auditable error, NEVER silently clamped to 1.

2. **Final Order Acceptance Boundary Enforcement:**
   - In [services/delta_paper_broker.py](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/delta_paper_broker.py): Immediately inside `open_simulated_trade()`, before any margin, fee, or state calculations, pass `lots` through `validate_paper_order_lots()`. If validation fails, immediately reject order and log security audit event.
   - In [services/paper_broker.py](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/paper_broker.py): Enforce `validate_paper_order_lots()` and remove the ability of `bypass_risk` to override the lot guard.

3. **Bot-Level Forward Test Restriction:**
   - In [services/delta_paper_bot.py](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/delta_paper_bot.py): Hard-clamp forward-test orders to 1 lot, and verify via the central guard before calling broker.

4. **Risk Budget Subordination:**
   - Enforce that 1 lot is a **ceiling**, not permission to exceed the 0.5% risk budget. If 1 lot risk exceeds \$50, the trade must still be rejected.

---

## 6. Audit Verdict

- **Phase 1 Forensic Audit Status:** ✅ **COMPLETE**
- **Vulnerabilities Identified:** 6 distinct failure/bypass points mapped.
- **Ready for Implementation:** YES. Proceed to Phase 2 (Central Hard Position-Size Guard).
