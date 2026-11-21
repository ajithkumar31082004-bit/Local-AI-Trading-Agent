# Phase 3.36 — Strategy Source Code Verification & Inventory Audit

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `f3432c8`  
**Audit Branch:** `feature/phase3-36-independent-shadow-audit`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Offline source-code verification of all 6 trading strategies and the arbitration engine against `reports/phase3_35_shadow_validation/strategy_registry_inventory.json`.

---

## 1. Executive Summary

An independent line-by-line inspection was conducted across the source code of all 6 trading strategies in `strategy/` and the master arbitration engine. The audit confirmed that:
1. Every strategy implementation is a **pure mathematical function** operating on `pd.DataFrame` and `ORBSettings`.
2. None of the strategies instantiate, reference, or import `DeltaPaperBroker`, `services.broker`, or any execution endpoint.
3. Every strategy produces a deterministic `Signal` dataclass containing direction, entry, stop loss, take profit, r-multiple, quality score, and diagnostic reasons.
4. No strategy writes to disk, database, network, or process environment.
5. All 6 strategies accurately match the inventory documented in Phase 3.35, with important domain nuances clarified below.

---

## 2. Detailed Strategy-by-Strategy Verification

### 2.1 Strategy 1: Opening Range Breakout (ORB)
- **Module:** [`strategy/orb_ema_volume.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/orb_ema_volume.py)
- **Entry Function:** `evaluate_orb(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal`
- **Timeframe:** 5m OHLCV execution.
- **Inventory Comparison:**
  - *Inventory Listing:* Opening range High/Low (09:00-09:45 IST), EMA20, ATR(14), Volume SMA(20), EMA slope.
  - *Source Reality & Nuance:* The opening range itself is defined by the first 15 minutes of trading (09:00–09:15 IST, three 5m candles). However, `cfg.no_entry_minutes_after_open` is configured to 45 minutes, meaning breakout signals are intentionally suppressed during the initial 45 minutes (09:00–09:45 IST) to avoid opening whipsaws. The active signal window is 09:45–14:30 IST.
  - *Calculation Purity:* Pure Pandas calculations utilizing vector indicators from `indicators/orb_indicators.py`.
  - *Broker Coupling:* **ZERO**. No broker import, no order submission, no state mutation.
  - *Verdict:* **VERIFIED PURE & ACCURATE**.

### 2.2 Strategy 2: Multi-Timeframe Trend Following Pullback
- **Module:** [`strategy/trend_following.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/trend_following.py)
- **Entry Function:** `evaluate_trend_following(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal`
- **Timeframe:** 15m higher-timeframe trend / 5m execution pullback.
- **Inventory Comparison:**
  - *Inventory Listing:* 15m EMA20, 15m EMA50, 15m ADX(14), 5m EMA20, 5m ATR(14), Volume SMA(20). Warmup: 150 candles.
  - *Source Reality:* Uses `resample_to_15m` to generate higher-timeframe trend context. Evaluates 15m trend filter (`EMA20 > EMA50` and `ADX > 20`). On 5m timeframe, looks for pullback touch of EMA20 with rejection wick and volume confirmation.
  - *Risk Integration:* Uses `estimate_lot_risk` from `risk/position_sizing.py`, which is purely mathematical (calculates gross risk, fees, and spread for 1 micro-lot = 0.001 XAU/USD).
  - *Broker Coupling:* **ZERO**.
  - *Verdict:* **VERIFIED PURE & ACCURATE**.

### 2.3 Strategy 3: Intraday VWAP + EMA Confirmation
- **Module:** [`strategy/vwap_ema.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/vwap_ema.py)
- **Entry Function:** `evaluate_vwap_ema(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal`
- **Timeframe:** 5m OHLCV.
- **Inventory Comparison:**
  - *Inventory Listing:* Intraday VWAP, 5m EMA20, 5m ATR(14), ADX(14), Volume SMA(20), EMA slope. Warmup: 20 candles.
  - *Source Reality:* Calculates daily cumulative VWAP from session open (09:00 IST). Validates price alignment relative to VWAP and EMA20. Rejects when price is overextended (>2.0 ATR from EMA20) or when EMA slope is flat.
  - *Broker Coupling:* **ZERO**.
  - *Verdict:* **VERIFIED PURE & ACCURATE**.

### 2.4 Strategy 4: Bollinger Bands + RSI Mean Reversion
- **Module:** [`strategy/bb_rsi_mean_reversion.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/bb_rsi_mean_reversion.py)
- **Entry Function:** `evaluate_bb_rsi(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal`
- **Timeframe:** 5m OHLCV.
- **Inventory Comparison:**
  - *Inventory Listing:* Bollinger Bands (20, 2.0), RSI(14), ATR(14). Warmup: 30 candles.
  - *Source Reality:* Detects price piercing outer Bollinger Bands with RSI extreme (<30 for LONG, >70 for SHORT), followed by a reversal candle closing back inside the band. Prohibited in trending regimes.
  - *Broker Coupling:* **ZERO**.
  - *Verdict:* **VERIFIED PURE & ACCURATE**.

### 2.5 Strategy 5: ATR Volatility Compression Breakout
- **Module:** [`strategy/atr_compression_breakout.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/atr_compression_breakout.py)
- **Entry Function:** `evaluate_atr_compression_breakout(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal`
- **Timeframe:** 5m OHLCV.
- **Inventory Comparison:**
  - *Inventory Listing:* ATR(14), 20-period ATR Moving Average, Bollinger Bandwidth, 5m EMA20, Volume SMA(20). Warmup: 30 candles.
  - *Source Reality:* Checks for consolidation squeeze (current ATR < 0.7 * 20-period ATR MA). Generates breakout signal when candle body closes outside consolidation boundaries with volume expansion > Volume SMA.
  - *Broker Coupling:* **ZERO**.
  - *Verdict:* **VERIFIED PURE & ACCURATE**.

### 2.6 Strategy 6: Liquidity Sweep Reversal
- **Module:** [`strategy/liquidity_sweep.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/liquidity_sweep.py)
- **Entry Function:** `evaluate_liquidity_sweep(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal`
- **Timeframe:** 5m OHLCV.
- **Inventory Comparison:**
  - *Inventory Listing:* Rolling Swing Highs/Lows (20-period), 5m ATR(14), 5m EMA20, Volume Spike (>1.5 * SMA). Warmup: 30 candles.
  - *Source Reality:* Identifies failed breakouts beyond swing points (wicks extending beyond swing high/low while close remains inside) accompanied by volume spikes.
  - *Broker Coupling:* **ZERO**.
  - *Verdict:* **VERIFIED PURE & ACCURATE**.

---

## 3. Master Arbitration Engine Verification

- **Module:** [`strategy/arbitrator.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/arbitrator.py)
- **Entry Function:** `arbitrate_strategies(frame: pd.DataFrame, cfg: ORBSettings = settings) -> ArbitrationResult`
- **Core Logic:**
  1. Calls `detect_market_regime(frame, cfg)` to evaluate `MarketRegime` (BULL_TREND, BEAR_TREND, RANGING, VOLATILE, UNCERTAIN).
  2. Evaluates all 6 strategies on the provided DataFrame.
  3. Filters signals against the prevailing regime (e.g. disables mean reversion during strong trends; disables trend following during ranging/choppy regimes).
  4. If multiple strategies fire valid signals, arbitrates based on signal quality score, risk-reward ratio, and strategy hierarchy.
  5. Returns `ArbitrationResult(winning_signal=..., all_signals=..., regime_result=...)`.
- **Readiness Integration:** If system is warming up (<150 bars) or regime is UNCERTAIN, arbitrator safely defaults to `NO TRADE` with detailed diagnostic code.
- **Broker Coupling:** **ZERO**.

---

## 4. Verification Matrix

| Strategy Name | Module Path | Function Signature | Warmup (Bars) | Broker Free? | State Free? |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **ORB** | `strategy.orb_ema_volume` | `evaluate_orb(frame, cfg)` | 20 | YES | YES |
| **TREND_FOLLOWING** | `strategy.trend_following` | `evaluate_trend_following(frame, cfg)` | 150 | YES | YES |
| **VWAP_EMA** | `strategy.vwap_ema` | `evaluate_vwap_ema(frame, cfg)` | 20 | YES | YES |
| **BB_RSI** | `strategy.bb_rsi_mean_reversion` | `evaluate_bb_rsi(frame, cfg)` | 30 | YES | YES |
| **ATR_COMPRESSION** | `strategy.atr_compression_breakout` | `evaluate_atr_compression_breakout(frame, cfg)` | 30 | YES | YES |
| **LIQUIDITY_SWEEP** | `strategy.liquidity_sweep` | `evaluate_liquidity_sweep(frame, cfg)` | 30 | YES | YES |
| **ARBITRATOR** | `strategy.arbitrator` | `arbitrate_strategies(frame, cfg)` | 150 | YES | YES |

---

## 5. Audit Conclusion

All 6 strategies and the arbitrator are completely uncoupled from broker execution, filesystem persistence, and network APIs. They are safe to be driven by `ReadOnlyStrategyEvaluator` on completed market candles.
