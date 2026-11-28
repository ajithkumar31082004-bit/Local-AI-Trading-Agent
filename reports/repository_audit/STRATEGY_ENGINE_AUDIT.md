# Strategy Engine Audit Report — XAUTUSD Derivatives Portfolio

**Document Identifier:** `REP-AUD-002-STRAT`  
**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Audit Date:** 2026-11-11  
**Audit Scope:** Quantitative Strategy Implementations, Technical Indicators, Multi-Strategy Arbitrator, Regime Classifier, Signal Arbitration  
**Lead Auditor:** Senior Quantitative Trading Systems Architect & Algorithmic Trading Engineer  

---

## 1. Executive Summary & Strategy Inventory

The quantitative trading system targets the **XAUTUSD** perpetual contract (Product ID: `131253`) on Delta Exchange India using 5-minute bar intervals with resampled 15-minute timeframe context. The strategy architecture is strictly deterministic; no Large Language Models (LLMs) or generative AI agents participate in the generation, filtering, or sizing of quantitative trading signals.

### Strategy Family Status Overview

| Strategy Family | Module Path | Implemented | Registered in Arbitrator | Completed Bars Only | Dynamic SL/TP | Min R-Multiple | Offline Tests Passing | Active Status |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **1. Opening Range Breakout (ORB)** | `strategy/orb_ema_volume.py` | YES | YES | YES | YES | $\ge 2.0R$ | 8 passed | **ACTIVE** |
| **2. EMA20/EMA50 Trend Following** | `strategy/trend_following.py` | YES | YES | YES | YES | $\ge 2.0R$ | 14 passed | **ACTIVE** |
| **3. VWAP + EMA Confirmation** | `strategy/vwap_ema.py` | YES | YES | YES | YES | $\ge 2.0R$ | 3 passed | **ACTIVE** |
| **4. Bollinger Bands + RSI Reversion** | `strategy/bb_rsi_mean_reversion.py` | YES | YES | YES | YES | $\ge 2.0R$ | 2 passed | **DISABLED** (Negative Expectancy) |
| **5. ATR Compression Breakout** | `strategy/atr_compression_breakout.py` | YES | YES | YES | YES | $\ge 2.0R$ | 2 passed | **ACTIVE** |
| **6. Liquidity Sweep Reversal** | `strategy/liquidity_sweep.py` | YES | YES | YES | YES | $\ge 2.0R$ | 2 passed | **ACTIVE** |

---

## 2. Strategy Family Detailed Analysis

### 2.1 Family 1: Opening Range Breakout (ORB + EMA + Volume)
- **Implementation File:** [`strategy/orb_ema_volume.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/orb_ema_volume.py)
- **Class Name:** `ORBStrategy`
- **Registration:** Registered in [`strategy/arbitrator.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/arbitrator.py#L31) under key `"ORB_EMA_VOLUME"`.
- **Core Logic:**
  - Establishes a 15-minute opening range high and low from completed 5m bars during session open.
  - Requires breakout above range high for BUY or below range low for SELL.
  - Requires trend alignment with EMA20 and volume surge exceeding $1.5\times$ 20-period volume moving average.
- **Indicators:**
  - Exponential Moving Average (EMA 20) calculated via standard exponential weighting.
  - Simple Moving Average of Volume (SMA 20).
  - Average True Range (ATR 14) for volatility-scaled buffering.
- **Candle Integrity:** Strictly operates on closed, completed bars (`df.iloc[:-1]` if live bar present, or indexed by timestamp cutoff).
- **Risk Parameters:**
  - Entry: Immediate next bar open or breakout limit.
  - Stop-Loss: Placed beyond the opposite boundary of the 15m range or $1.5 \times ATR$.
  - Take-Profit: Calculated to achieve a minimum $2.0R$ to $2.5R$ reward-to-risk ratio.
- **Test Evidence:** 8 passing tests in [`tests/test_orb_strategy.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_orb_strategy.py).
- **Known Limitations:** Vulnerable to false breakouts during low-liquidity holiday sessions; relies on clear session opening definition (IST 09:15 / UTC 03:45 or London/NY open).

### 2.2 Family 2: EMA20 / EMA50 Trend Following
- **Implementation File:** [`strategy/trend_following.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/trend_following.py)
- **Class Name:** `TrendFollowingStrategy`
- **Registration:** Registered in `StrategyArbitrator` under key `"TREND_FOLLOWING"`.
- **Core Logic:**
  - Evaluates dual timeframe hierarchy: 15-minute primary trend and 5-minute entry trigger.
  - 15-minute trend filter: EMA20 must be cleanly separated above EMA50 for bullish regime, or below for bearish regime.
  - 5-minute trigger: Pullback towards EMA20/EMA50 ribbon with bullish/bearish engulfing or rejection candle confirmation.
- **Indicators:**
  - EMA 20 and EMA 50 on 15m and 5m bars.
  - Average Directional Index (ADX 14) with minimum threshold of 25 to avoid choppy consolidation.
  - ATR 14 for stop distance.
- **Candle Integrity:** Only completed bars are processed.
- **Risk Parameters:**
  - Entry: Market order upon bar close confirmation.
  - Stop-Loss: Placed below the recent swing low (BUY) or above recent swing high (SELL) buffered by $0.5 \times ATR$.
  - Take-Profit: Sized dynamically to satisfy $\ge 2.0R$.
- **Test Evidence:** 14 passing tests in [`tests/test_trend_following.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_trend_following.py).
- **Known Limitations:** In extended trends, entries late in the trend have elevated risk of deep pullbacks before TP is reached.

### 2.3 Family 3: VWAP + EMA Confirmation
- **Implementation File:** [`strategy/vwap_ema.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/vwap_ema.py)
- **Class Name:** `VWAPEMAStrategy`
- **Registration:** Registered in `StrategyArbitrator` under key `"VWAP_EMA"`.
- **Core Logic:**
  - Session-anchored Volume-Weighted Average Price (VWAP) combined with EMA20.
  - BUY signal when price pulls back to test VWAP from above while EMA20 $> \text{VWAP}$ and price forms a bullish rejection candle.
  - SELL signal when price tests VWAP from below while EMA20 $< \text{VWAP}$ and price forms a bearish rejection candle.
- **Indicators:**
  - Intraday cumulative VWAP: $\sum(TypicalPrice \times Volume) / \sum Volume$.
  - EMA 20.
  - ATR 14.
- **Candle Integrity:** Enforced completed bar evaluations.
- **Risk Parameters:**
  - Stop-Loss: Beyond VWAP with ATR buffer ($0.3 \times ATR$).
  - Take-Profit: Target $\ge 2.0R$.
- **Test Evidence:** 3 passing tests in [`tests/test_vwap_ema.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_vwap_ema.py).
- **Known Limitations:** Requires accurate volume data. In crypto derivatives, delta exchange tick volume discrepancies can distort intraday VWAP if not synchronized to exact session resets.

### 2.4 Family 4: Bollinger Bands + RSI Mean Reversion
- **Implementation File:** [`strategy/bb_rsi_mean_reversion.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/bb_rsi_mean_reversion.py)
- **Class Name:** `BBRSIMeanReversionStrategy`
- **Registration:** Registered in arbitrator constructor, BUT explicitly placed in the **disabled portfolio set**:
  ```python
  self.disabled_in_portfolio = {"BB_RSI_MEAN_REVERSION"}
  ```
  in [`strategy/arbitrator.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/arbitrator.py#L38).
- **Core Logic:**
  - 20-period Bollinger Bands ($2\sigma$) and 14-period Relative Strength Index (RSI).
  - Generates BUY when price touches or pierces lower band with $RSI < 30$, followed by inside bar close.
  - Generates SELL when price touches or pierces upper band with $RSI > 70$.
- **Reason for Disabling:** During historical backtesting and forward simulation on XAUTUSD, mean-reversion strategies produced persistent drawdown during strong gold macro trend breakouts. In accordance with quantitative governance, it was quarantined until positive mathematical expectancy can be proven.
- **Test Evidence:** 2 passing tests in [`tests/test_bb_rsi_mean_reversion.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_bb_rsi_mean_reversion.py).
- **Known Limitations:** Highly dangerous during strong trending regimes; frequently stopped out during breakout expansion phases.

### 2.5 Family 5: ATR Volatility Compression Breakout
- **Implementation File:** [`strategy/atr_compression_breakout.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/atr_compression_breakout.py)
- **Class Name:** `ATRCompressionBreakoutStrategy`
- **Registration:** Registered in `StrategyArbitrator` under key `"ATR_COMPRESSION_BREAKOUT"`.
- **Core Logic:**
  - Measures 15-bar volatility compression where $ATR(14) \le 0.85 \times \text{SMA}(ATR, 30)$.
  - Squeeze identification followed by an expansion candle breaking out of the 15-bar high/low range with volume $> 1.25 \times \text{Volume SMA}$.
- **Indicators:**
  - ATR 14, 30-period rolling average of ATR.
  - Donchian-style 15-period channel.
- **Candle Integrity:** Enforced completed bars.
- **Risk Parameters:**
  - Stop-Loss: Placed at the midpoint of the compression channel.
  - Take-Profit: Sized dynamically to achieve $2.0R$ to $3.0R$.
- **Test Evidence:** 2 passing tests in [`tests/test_atr_compression_breakout.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_atr_compression_breakout.py).
- **Known Limitations:** In tight choppy markets, false expansions can trigger stop-outs prior to genuine directional expansion.

### 2.6 Family 6: Liquidity Sweep Reversal
- **Implementation File:** [`strategy/liquidity_sweep.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/liquidity_sweep.py)
- **Class Name:** `LiquiditySweepStrategy`
- **Registration:** Registered in `StrategyArbitrator` under key `"LIQUIDITY_SWEEP"`.
- **Core Logic:**
  - Detects institutional stop-runs and liquidity sweeps around major 20-bar swing highs and swing lows.
  - Identifies price wick piercing the swing level by $> 0.1 \times ATR$ but closing back inside the range, indicating aggressive absorption.
  - Bullish sweep (sweep below swing low + hammer or inside close) $\rightarrow$ BUY.
  - Bearish sweep (sweep above swing high + shooting star or inside close) $\rightarrow$ SELL.
- **Indicators:**
  - 20-period fractal swing highs and lows.
  - ATR 14.
- **Candle Integrity:** Strictly evaluated after bar close.
- **Risk Parameters:**
  - Stop-Loss: Beyond the extreme wick point of the sweep $+ 0.2 \times ATR$.
  - Take-Profit: Target opposite liquidity pool or minimum $2.0R$.
- **Test Evidence:** 2 passing tests in [`tests/test_liquidity_sweep.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_liquidity_sweep.py).
- **Known Limitations:** Requires sufficient volatility to trigger sweeps; during micro-range drift, false wicks can occur.

---

## 3. Market Regime Engine & AI Arbitration

### 3.1 Regime Detection Framework
- **Implementation:** [`regime/market_regime.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/regime/market_regime.py) & [`regime/readiness.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/regime/readiness.py)
- **Classified States:**
  1. `TRENDING_BULLISH` (EMA20 $> $ EMA50, ADX $> 25$, positive slope)
  2. `TRENDING_BEARISH` (EMA20 $< $ EMA50, ADX $> 25$, negative slope)
  3. `RANGING` (ADX $< 20$, price oscillates within Bollinger Bands)
  4. `VOLATILE_BREAKOUT` (ATR expansion $> 1.5\times$ mean, high volume)
  5. `UNCERTAIN` (Conflicting signals, low confidence $< 60\%$, or insufficient warm-up bars)
- **Warm-Up Requirement:** Hard requirement for minimum 450 bars (50 15m bars, 150 5m bars). If insufficient, `RegimeReadiness` emits `UNCERTAIN` and forbids trade entry.

### 3.2 Strategy Arbitrator & Signal Conflict Resolution
- **Implementation:** [`strategy/arbitrator.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/strategy/arbitrator.py)
- **Arbitration Pipeline:**
  1. **Filtering:** Filters out disabled strategies (`self.disabled_in_portfolio`).
  2. **Regime Compatibility:** Compares each candidate signal's strategy against the current detected regime (e.g., trend-following strategies rejected in `RANGING` regime).
  3. **Conflict Detection:** If active strategies simultaneously produce a `BUY` and a `SELL`, the arbitrator rejects both with rejection code `CONFLICTING_SIGNALS`.
  4. **Ranking & Selection:** Eligible signals are scored by composite quality:
     $$\text{Score} = (\text{Confidence} \times 0.4) + (\text{RegimeFit} \times 0.3) + (\text{R-Multiple} \times 0.3)$$
     Minimum quality cutoff: $\ge 0.65$. The highest-scoring signal is selected.
  5. **Deduplication & Cooldown:** Reject signals if an active position exists or if the minimum cooldown duration (15 minutes) has not elapsed since the last trade.

### 3.3 Complete Disconnection from Generative AI / LLMs
- **Audit Verification:** The quantitative trading pipeline (`strategy/`, `regime/`, `risk/`, `services/`) contains **zero dependencies** on `crewai`, `openai`, `ollama`, or generative LLMs.
- All decisions, indicators, filters, and risk checks are 100% deterministic, testable, and mathematically reproducible.
- Legacy LLM code in `agents/` is completely separated from the quantitative execution path.

---

## 4. Test Verification Summary

The complete offline strategy test suite was executed:
```powershell
.venv\Scripts\python.exe -m pytest tests/test_orb_strategy.py tests/test_trend_following.py tests/test_vwap_ema.py tests/test_bb_rsi_mean_reversion.py tests/test_atr_compression_breakout.py tests/test_liquidity_sweep.py tests/test_strategy_arbitrator.py tests/test_market_regime.py tests/test_regime_readiness.py
```
- **Results:** **53 passed** in 15.68 seconds.
- **Failures:** 0.
- **Errors:** 0.

---

## 5. Architectural Findings & Recommendations

1. **Demonstrate Expectancy for Bollinger Bands Strategy (P3):** Keep `BB_RSI_MEAN_REVERSION` disabled until a rigorous walk-forward backtest demonstrates positive Sharpe and expectancy on XAUTUSD.
2. **Standardize Session Open Definition (P2):** ORB requires an explicit, multi-timezone session open configuration for 24/7 crypto gold trading (e.g. aligning with London 08:00 UTC and NY 13:30 UTC liquidity influx).
3. **VWAP Reset Alignment (P2):** Implement a explicit UTC midnight session reset for VWAP to guarantee consistency across continuous EC2 operations.

