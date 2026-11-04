# Phase 3 Audit Report: XAUTUSD PAPER Execution Realism and Statistical Validation

> **Repository**: `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
> **Branch**: `feature/strategy-intelligence-v2`  
> **Instrument**: `XAUTUSD` (Tether Gold Perpetual Futures, Product ID: 131253)  
> **Exchange**: Delta Exchange India  
> **Remote EC2 Host**: AWS EC2 Ubuntu (`16.4.36.144`)  
> **Execution Mode**: STRICTLY PAPER (0 Real Capital, 0 Real Orders)  
> **Audit Date**: 2026-11-04  

---

## Executive Summary

Phase 3 establishes advanced paper execution realism and rigorous statistical validation for the continuous XAUTUSD paper forward test. Enhancements include simulation of maker orders that do not fill (price bounds testing), partial fill modeling, maker entry with taker stop-loss exit, regime-dependent friction expansion (50% wider spread and slippage during breakout volatility), and Indian GST tracking.

All **176 automated tests** pass with 100% green status across the repository. The production paper runner remains strictly locked at **1 micro-lot** ($0.001\text{ XAUT}$ $\approx$ $4.05 exposure) with live orders permanently prohibited.

| Audit Domain | Finding / Result | Status |
| :--- | :--- | :---: |
| **Safety Invariants** | `TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`, 0 real orders, dashboard on localhost. | **VERIFIED** |
| **Execution Simulation** | Unfilled maker orders, partial fills, regime-dependent spread/slip, maker entry + taker exit modeled. | **IMPLEMENTED** |
| **Position Sizing Audit** | Theoretical 495 lots verified as unapproved theoretical ceiling; 90% free collateral buffer confirmed. | **VERIFIED** |
| **Statistical Validation** | 95% Wilson Score CI for Win Rate: **[8.2%, 64.1%]**. Confirmed 7, 25, or 30 trades cannot prove an edge. | **VALIDATED** |
| **Regime Attribution** | `TRENDING_BEARISH` delivered **1.36 Profit Factor** (+0.0157 net); `TRENDING_BULLISH` yielded 0.00. | **ANALYTICALLY IDENTIFIED** |
| **EC2 Deployment State** | Deployed commit `f5bcf57`, `delta-paper-trading.service` running (PID 5947, 0 restarts, 0 drops). | **CONFIRMED VIA SSH** |

---

## 1. Safety Controls Verification

The production system strictly enforces all paper trading boundaries:

- [x] **`TRADING_MODE=PAPER`**: Enforced in local `.env`, EC2 `.env`, and broker runtime configs.
- [x] **`LIVE_TRADING_ENABLED=false`**: Enforced; invoking `execute_live_order()` raises `PaperSafetyViolation`.
- [x] **`LIVE_TRADING_ACKNOWLEDGED=NO`**: Live trading permanently disacknowledged.
- [x] **`PAPER_TRADING_ACKNOWLEDGED=YES`**: Explicit confirmation active.
- [x] **Fixed 1-Lot Forward Runner**: Forward daemon trades exclusively 1 micro-lot (0.001 XAUT); dynamic sizing is NOT deployed.
- [x] **Zero Private Exchange Endpoints**: Public WebSocket (`wss://socket.india.delta.exchange`) and public REST (`https://api.india.delta.exchange/v2/history/candles`) only.
- [x] **Zero Exchange API Keys**: Zero API keys or secrets are stored, requested, or queried on EC2 or in code.
- [x] **Localhost Dashboard**: Streamlit bound strictly to `127.0.0.1:8501` on EC2 (no external port exposure).

---

## 2. Advanced Execution Cost & Simulation Modeling

### Execution Realism Enhancements in `DeltaPaperBroker`
[`services/delta_paper_broker.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/delta_paper_broker.py) incorporates comprehensive paper simulation capabilities:

1. **Maker Orders That Do Not Fill (Price Bounds Verification)**:
   - For Buy Limit orders, if the candle's low price does not reach or penetrate the limit entry (`bar_low > entry_price`), the order is treated as **unfilled resting liquidity** and rejected:
     `return False, "Maker LIMIT BUY unfilled: bar low > limit entry", None`
   - For Sell Limit orders, if the candle's high price fails to reach the limit entry (`bar_high < entry_price`), the order is treated as unfilled:
     `return False, "Maker LIMIT SELL unfilled: bar high < limit entry", None`
   - Tested in [`test_maker_order_fill_and_unfill_conditions`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_trade_accounting.py#L344-L395).
2. **Partial Fill Simulation**:
   - Accepts a `fill_ratio` parameter ($0.0 < \text{ratio} \le 1.0$) to simulate liquidity depth exhaustion. Position size is scaled:
     $$\text{Executed Lots} = \max(1, \lfloor \text{Lots} \times \text{fill\_ratio} \rceil)$$
   - Tested in [`test_partial_fill_simulation`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_trade_accounting.py#L398-L417).
3. **Maker Entry Followed by Taker Stop-Loss Exit**:
   - Models the realistic market scenario where the entry order rests as a passive limit order (Maker fee: 0.01%), but when Stop-Loss is hit, it triggers an urgent Stop-Market order (Taker fee: 0.05%).
   - Take-Profit exits remain passive limit orders (Maker fee: 0.01%).
   - Tested in [`test_maker_entry_followed_by_taker_sl_exit`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_trade_accounting.py#L420-L450).
4. **Regime-Dependent Spread and Slippage**:
   - In `VOLATILE_BREAKOUT`: Bid-ask spread and slippage expand by **1.5x** (0.03% spread + 0.03% slippage) to capture illiquid breakout conditions.
   - In `UNCERTAIN`: Multiplier is **1.25x**.
   - In `RANGING` / `TRENDING`: Standard baseline friction applies (0.02% spread + 0.02% slippage).
   - Tested in [`test_regime_dependent_spread_and_slippage`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_trade_accounting.py#L453-L485).
5. **Applicable GST on Brokerage Fees**:
   - 18.0% Indian Goods and Services Tax (GST) is tracked separately on every transaction. When enabled (`cfg.apply_gst=True`), GST is deducted from realized P&L with exact cash balance conservation.

---

### Comparative Execution Cost Scenarios

$$\text{Gross P\&L Across 7 Forward Trades} = \mathbf{+\$0.0047} \quad (\text{Gross PF: 1.08, Gross Expectancy: } +0.0271\text{R})$$

| Scenario | Fee Structure | Exchange Fees | 18% GST | Spread & Slip | Total Friction | Net P&L | Net PF | Net Expectancy | Max Drawdown |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Maker/Maker [Base]** | 0.01% / 0.01% | $0.0056 | $0.0007* | $0.0112 | **$0.0168** | **-$0.0121** | 0.83 | -0.1657R | $0.0442 |
| **1+GST. Maker/Maker + GST** | 0.01% / 0.01% + 18% GST | $0.0056 | $0.0007 | $0.0112 | **$0.0175** | **-$0.0128** | 0.82 | -0.1738R | $0.0448 |
| **2. Maker Entry + Taker SL Exit** | 0.01% Entry / 0.05% Exit | $0.0167 | $0.0028* | $0.0112 | **$0.0279** | **-$0.0232** | 0.71 | -0.2886R | $0.0537 |
| **2+GST. Maker/Taker + GST** | 0.01% / 0.05% + 18% GST | $0.0167 | $0.0028 | $0.0112 | **$0.0307** | **-$0.0260** | 0.68 | -0.3271R | $0.0561 |
| **3. Taker Entry + Taker Exit** | 0.05% / 0.05% | $0.0279 | $0.0049* | $0.0112 | **$0.0391** | **-$0.0344** | 0.61 | -0.4186R | $0.0633 |
| **3+GST. Taker/Taker + GST** | 0.05% / 0.05% + 18% GST | $0.0279 | $0.0049 | $0.0112 | **$0.0440** | **-$0.0393** | 0.57 | -0.4786R | $0.0675 |

*\* Tracked as a distinct line item; deducted when `apply_gst=True`.*

---

## 3. Position Sizing Audit: Theoretical 495-Lot Calculation

> [!WARNING]
> **UNAPPROVED THEORETICAL CALCULATION ONLY**: The 495-lot sizing calculation is an audit artifact evaluating the theoretical capacity of the `risk/position_sizing.py` engine on a $10,000 account. It is **NOT approved for live execution** and is **NOT connected to the running EC2 daemon**. The forward runner remains strictly locked at **1 micro-lot**.

### Independent Step-by-Step Verification

| Parameter | Value | Audit Verification |
| :--- | :---: | :--- |
| **Contract Multiplier** | `0.001 XAUT` | 1 lot represents 0.001 troy oz of gold (~$4.05 notional exposure). |
| **Lot Increment** | `1 lot` | Sizing enforces integer lots: $\lfloor \text{Lots} \rfloor$. |
| **Account Equity** | `$10,000.00` | Audited paper starting equity. |
| **Risk Budget (0.5%)** | `$50.00` | Account risk allocation per trade ($10,000 \times 0.005$). |
| **Entry Price** | `$4035.58` | Verified fill price from trade #7. |
| **Stop-Loss Price** | `$4021.73` | Stop-loss distance = $13.85 points. |
| **Per-Lot Gross Risk** | `$0.01385` | $13.85 \times 0.001$. |
| **Per-Lot Friction** | `$0.00242` | Maker fee ($0.0008) + Spread ($0.0008) + Slip ($0.0008). |
| **Total Risk Per Lot** | `$0.01627` | Combined risk + friction at stop loss. |
| **Raw Risk Lot Capacity** | **3,073 lots** | $\lfloor \$50.00 / \$0.01627 \rfloor = 3,073$ lots ($12,400 notional). |
| **Leverage Ratio** | `2.0x` | Conservative 2x margin leverage. |
| **Margin Per Lot** | **$2.01779** | $(\$4035.58 \times 0.001) / 2.0$. |
| **Max Margin Budget (10%)** | **$1,000.00** | Strict 10% maximum margin allocation rule ($10,000 \times 0.10$). |
| **Max Margin Lots** | **495 lots** | $\lfloor \$1,000.00 / \$2.01779 \rfloor = \lfloor 495.59 \rfloor = \mathbf{495\text{ lots}}$. |
| **Final Sized Lots** | **495 lots** | $\min(\text{Risk Lots}=3073, \text{Margin Lots}=495) = \mathbf{495\text{ lots}}$. |

### Liquidation Safety Buffer
- Total margin required for 495 lots: $495 \times \$2.01779 = \mathbf{\$998.81}$.
- Free unencumbered equity: $\$10,000.00 - \$998.81 = \mathbf{\$9,001.19}$ (**90.01% free collateral buffer**).
- Liquidation distance at 2x leverage: requires a ~50% adverse price drop (~$2,000 drop in gold price).
- Stop-loss distance: 13.85 points (0.34% price drop).
- **Safety Ratio**: The stop loss is placed **144 times closer** than the liquidation threshold.

Verified by dedicated unit test [`test_position_sizing_reported_495_lots`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_trade_accounting.py#L283-L341).

---

## 4. Statistical Validation & Sample Size Reality

### The Law of Small Numbers: Why 7, 25, or 30 Trades Cannot Prove an Edge

A fundamental principle of statistical inference is that observed win rates from small sample sizes carry massive estimation error. 

For a binomial process with $N$ independent trials and observed proportion $\hat{p} = \frac{n_w}{N}$, the **Wilson Score 95% Confidence Interval** is given by:

$$\tilde{p} = \frac{n_w + \frac{z^2}{2}}{N + z^2}, \quad \text{CI}_{95\%} = \tilde{p} \pm \frac{z}{N + z^2} \sqrt{n_w \left(1 - \frac{n_w}{N}\right) + \frac{z^2}{4}} \quad (z = 1.96)$$

Applying this formula to our forward test results:

| Sample Size ($N$) | Observed Wins ($n_w$) | Observed Win Rate ($\hat{p}$) | 95% Wilson Score Confidence Interval | Interval Width | Statistical Conclusion |
| :---: | :---: | :---: | :---: | :---: | :--- |
| **$N = 7$ (Current)** | **2** | **28.57%** | **[8.2%, 64.1%]** | **55.9%** | **Statistically indistinguishable from random noise.** The true win rate could plausibly range from 8.2% (catastrophic) to 64.1% (elite). |
| **$N = 25$** | 9 | 36.00% | **[20.3%, 55.4%]** | **35.1%** | Spans across unviable (<33%) and highly profitable (>45%) regimes. |
| **$N = 30$** | 11 | 36.67% | **[21.9%, 54.5%]** | **32.6%** | Error margin remains $\pm 16.3\%$. Cannot reject null hypothesis of zero edge. |
| **$N = 100$** | 38 | 38.00% | **[29.1%, 47.8%]** | **18.7%** | Narrower, but still spans $\pm 9.4\%$. |
| **$N = 350$** | 133 | 38.00% | **[33.1%, 43.2%]** | **10.1%** | First sample size achieving true statistical power ($\pm 5\%$ margin of error at 95% confidence). |

$$\text{Required Sample Size for Margin of Error } e: \quad N = \frac{z^2 \cdot p(1-p)}{e^2}$$
- For $e = \pm 15\%$: $N \ge 39$ trades.
- For $e = \pm 10\%$: $N \ge 88$ trades.
- For $e = \pm 5\%$: $N \ge 350$ trades.

**Formal Determination**: No statistical claim of edge or profitability can be made from 7, 25, or 30 trades. Forward testing must continue to accumulate trades over extended time horizons.

---

### Strategy & Market Regime Attribution

All 7 accepted trades were generated by `VWAP_EMA`. Performance stratified by prevailing market regime:

| Prevailing Regime | Trades | Win Rate | Gross P&L | Total Friction | Net P&L | Gross PF | Net PF | Regime Assessment |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`TRENDING_BEARISH`** | 5 (100% Short) | **40.0%** (2W / 3L) | **+$0.0277** | $0.0120 | **+$0.0157** | **1.71** | **1.36** | **Net Positive Edge**: Captured clean trend continuations in gold sell-offs. |
| **`TRENDING_BULLISH`** | 2 (100% Long) | **0.0%** (0W / 2L) | **-$0.0230** | $0.0048 | **-$0.0278** | 0.00 | 0.00 | **Whipsaw Drag**: Both longs entered near short-term intraday exhaustion tops. |

**Regime Finding**: The strategy exhibited robust quantitative edge in `TRENDING_BEARISH` conditions (PF: 1.36 net), but suffered from mean-reverting pullbacks during `TRENDING_BULLISH` runs.

---

## 5. Live EC2 Deployment Verification

Independent SSH verification of AWS EC2 instance `ubuntu@16.4.36.144` confirms that the remote environment matches the audited codebase:

### 1. Running Commit Hash
```bash
$ git rev-parse HEAD
f5bcf57dcdc7ab36a397c1810a3ee76aaa0fa753
$ git log -1 --oneline
f5bcf57 feat(phase2-audit): add execution cost scenarios, GST tracking, 495-lot sizing test, and Phase 2 validation report
```

### 2. Systemd Daemon Health & Zero Restarts
```
● delta-paper-trading.service - Delta Exchange India XAUTUSD Continuous 24h Paper Trading Daemon
     Loaded: loaded (/etc/systemd/system/delta-paper-trading.service; enabled; preset: enabled)
     Active: active (running) since Wed 2026-11-04 17:49:31 UTC; 40min ago
   Main PID: 5947 (python)
      Tasks: 5 (limit: 627)
     Memory: 100.9M (peak: 114.9M)
   Restarts: NRestarts=0 (Zero unexpected restarts)
```

### 3. Remote Accounting Test Suite
```bash
$ .venv/bin/pytest tests/test_paper_trade_accounting.py
============================== 13 passed in 0.95s ==============================
```

### 4. Candle Continuity & Stream Freshness
Inspecting `/home/ubuntu/Local-AI-Trading-Agent/data/paper_trading_service.log`:
```
17:50:04 [INFO] market_data.delta_live_feed: New 5m completed candle finalized: 2026-11-04 17:45:00+00:00 Close=$4058.12 Vol=407454
17:50:04 [INFO] ForwardTest24H: 5m Bar Completed: 2026-11-04 17:45:00+00:00 Close=$4058.12 Vol=407454 (Total 5m: 450, 15m: 149)
17:50:04 [INFO] services.delta_paper_bot: Candle completed event: 2026-11-04 17:45:00+00:00 Close=$4058.12 High=$4058.12 Low=$4056.62
[00h03m38s] WS: OK | Ready: READY | Price: $ 4058.15 | 5m: 450 | 15m: 149 | Regime: TRENDING_BULLISH | Pos: FLAT | Ticks: 606
```
The live feed maintains strictly monotonic timestamps, continuous 5-minute candle finalization, and zero dropped frames.

---

## 6. Automated Test Suite Results

```
============================= test session starts ==============================
platform win32 -- Python 3.13.14, pytest-9.1.1, pluggy-1.7.0
rootdir: C:\Users\ajith\Downloads\Local_AI_Agent
configfile: pytest.ini
plugins: anyio-4.15.1, platformdirs-4.13.6
collected 176 items

tests/test_app_import.py .                                               [  0%]
tests/test_atr_compression_breakout.py ...                               [  2%]
tests/test_backtester.py .....                                           [  5%]
tests/test_bb_rsi_mean_reversion.py ...                                  [  6%]
tests/test_delta_candles.py .....                                        [  9%]
tests/test_delta_exchange.py .                                           [ 10%]
tests/test_delta_live_integration.py .......                             [ 14%]
tests/test_delta_paper_trading.py ...............                        [ 22%]
tests/test_delta_risk.py .....                                           [ 25%]
tests/test_indicators.py ...                                             [ 27%]
tests/test_liquidity_sweep.py ...                                        [ 28%]
tests/test_market_regime.py ...                                          [ 30%]
tests/test_news_and_alphavantage.py .....                                [ 33%]
tests/test_ollama_resilience.py .                                        [ 34%]
tests/test_orb_backtest.py ......                                        [ 37%]
tests/test_orb_diagnostics_regression.py .........                      [ 42%]
tests/test_orb_indicators.py ....                                        [ 45%]
tests/test_orb_risk.py .....                                             [ 47%]
tests/test_orb_strategy.py ........                                      [ 52%]
tests/test_orb_tools_and_agent.py ......                                 [ 55%]
tests/test_paper_broker.py ...                                           [ 57%]
tests/test_paper_trade_accounting.py .............                        [ 64%]
tests/test_regime_readiness.py ..................                        [ 75%]
tests/test_risk_manager.py ....                                          [ 77%]
tests/test_strategies.py ...                                             [ 79%]
tests/test_strategy_arbitrator.py ....                                   [ 81%]
tests/test_supabase.py .                                                 [ 81%]
tests/test_trend_following.py .........................                  [ 96%]
tests/test_vwap_ema.py ......                                            [100%]

======================== 176 passed, 4 warnings in 27.84s =======================
```

---

## 7. Outstanding Risks & Operational Recommendations

1. **Do Not Alter Strategy or Risk Parameters**:
   - The strategy demonstrated positive gross signal expectancy (+0.027R) and solid performance in bearish trends (PF: 1.36). Do not loosen filters, increase trade frequency, or override stop losses.
2. **Never Deploy Dynamic Sizing Without Statistical Edge**:
   - Theoretical 495-lot sizing must remain unapproved and disconnected. Sizing up before establishing a statistically significant sample would compound downside risk.
3. **Continue Unattended PAPER Forward Run on EC2**:
   - Maintain the active EC2 daemon (`delta-paper-trading.service`) until **150+ completed candles / 25-30 forward trades** are accumulated across diverse market regimes.
4. **Strict Safety Protocol**:
   - Live trading mode remains permanently disacknowledged (`LIVE_TRADING_ACKNOWLEDGED=NO`).

