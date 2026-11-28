# PHASE 3.13 — PAPER TRADING ACCOUNTING & RELIABILITY AUDIT REPORT

**Audit Date:** 2026-11-05  
**Audit Timestamp:** 2026-11-05 01:56:21 UTC (07:26:21 IST)  
**Target Instrument:** XAUTUSD (Product ID: 131253)  
**Exchange:** Delta Exchange India (`wss://socket.india.delta.exchange`)  
**Trading Execution Mode:** STRICTLY PAPER ONLY  
**Branch:** `feature/strategy-intelligence-v2`  
**Audited Commit SHA:** `c8dec34cfa3330f8157e7c2c3b390249d59c07af`  
**Target Deployment:** AWS EC2 (`ubuntu@16.4.36.144`)  

---

## 1. Executive Verdict

| Audit Domain | Target / Item | Status | Key Evidence / Metric |
| :--- | :--- | :---: | :--- |
| **Safety Controls** | Paper-only gating & endpoints | **PASS** | `TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`, zero real exchange calls |
| **Commit Parity** | Local, GitHub Remote, EC2 | **PASS** | Exact SHA match: `c8dec34cfa3330f8157e7c2c3b390249d59c07af` |
| **Accounting Reconciliation** | Trade ledger vs Cash balance | **PASS** | Exact cash conservation: $\$0.0000$ variance across 7 historical trades |
| **GST Discrepancy Resolution** | Friction formula vs GST total | **PASS (RESOLVED)** | GST is **EXCLUDED** from friction total when `DELTA_APPLY_GST=false` (default) |
| **Automated Test Suite** | Reliability & Accounting Tests | **PASS** | 16 of 16 tests passing cleanly on both Local and EC2 |
| **EC2 Daemon Health** | `delta-paper-trading.service` | **PASS** | Active (PID `8328`), 0 restarts, 0 errors, 9,698 live ticks ingested |
| **EC2 Dashboard Health** | `delta-paper-dashboard.service` | **PASS** | Active (PID `8333`), 0 restarts, bound to `127.0.0.1:8501`, HTTP 200 OK |
| **Monitoring Duration** | 24-hour test completion status | **IN PROGRESS** | 23m 07s elapsed out of 24h (test is actively executing, NOT complete) |
| **Restart Recovery** | Position and cash state restore | **PASS** | Deserialized cleanly via `_load_state()`; duplicate entries blocked |
| **Cross-Process Concurrency** | Multi-process collision safety | **OBSERVATION** | Process-local `RLock` protects in-memory threads; file locking recommended |

**OVERALL AUDIT VERDICT:** **PASS WITH DOCUMENTED ARCHITECTURAL FINDINGS**. The live daemon on EC2 is running cleanly and safely. All accounting formulas and friction allocations have been mathematically and programmatically verified.

---

## 2. Commit and Deployment Parity

Verification of the exact 40-character Git commit hash:

- **Local Working Tree:** `c8dec34cfa3330f8157e7c2c3b390249d59c07af`
- **GitHub Remote (`origin/feature/strategy-intelligence-v2`):** `c8dec34cfa3330f8157e7c2c3b390249d59c07af`
- **AWS EC2 Production Host:** `c8dec34cfa3330f8157e7c2c3b390249d59c07af`
- **Working Tree State:** Clean code baseline across all environments; zero untracked code modifications.

---

## 3. Strict PAPER Safety Verification

Strict paper trading constraints were audited in source code, configuration files, and runtime environment:

1. **Environment Configuration (`.env` on EC2):**
   ```env
   TRADING_MODE=PAPER
   LIVE_TRADING_ENABLED=false
   LIVE_TRADING_ACKNOWLEDGED=NO
   PAPER_TRADING_ACKNOWLEDGED=YES
   PAPER_INITIAL_EQUITY=10000.0
   ```
2. **Safety Invariants in Code (`services/delta_paper_broker.py:40-42`):**
   ```python
   if getattr(self.cfg, "mode", "PAPER") == "LIVE" and not getattr(self.cfg, "live_trading_enabled", False):
       raise PaperSafetyViolation("CRITICAL: Live trading requested in paper broker context.")
   ```
3. **Network Boundary Audit:**
   - Zero private exchange REST calls (`/v2/orders`, `/v2/positions`) invoked.
   - Zero exchange API keys (`DELTA_EXCHANGE_API_KEY`, `DELTA_EXCHANGE_API_SECRET`) loaded or requested.
   - Dashboard port `8501` is strictly bound to `127.0.0.1` (loopback only, closed to public internet).
   - Only public market data endpoints (`wss://socket.india.delta.exchange` and public REST candle seeds) are connected.

---

## 4. Trade-by-Trade Fee & Friction Reconciliation

### 4.1 Historical 14-Day Forward Ledger Breakdown ($N = 7$ Trades)
The complete trade history in `data/forward_test_14d_trades.csv` was audited trade-by-trade with full 4-decimal precision:

| Trade ID | Strategy | Dir | Lots | Entry Time (IST) | Exit Time (IST) | Entry Fill | Exit Fill | Gross P&L | Brokerage | Spread | Slippage | Friction | GST (18%) | Net P&L | Planned Risk | Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `PAPER_20261104_171333` | `VWAP_EMA` | SHORT | 1 | 2026-10-22 13:15 | 2026-10-22 19:35 | \$4,093.99 | \$4,061.17 | +\$0.0345 | \$0.0008 | \$0.0008 | \$0.0008 | \$0.0024 | \$0.0001 | **+\$0.0321** | \$0.0144 | **+2.23R** |
| `PAPER_20261104_171418` | `VWAP_EMA` | SHORT | 1 | 2026-10-23 20:35 | 2026-10-26 08:40 | \$4,001.86 | \$4,020.17 | -\$0.0167 | \$0.0008 | \$0.0008 | \$0.0008 | \$0.0024 | \$0.0001 | **-\$0.0191** | \$0.0175 | **-1.09R** |
| `PAPER_20261104_171629` | `VWAP_EMA` | SHORT | 1 | 2026-10-27 15:45 | 2026-10-27 16:10 | \$4,002.23 | \$4,013.88 | -\$0.0100 | \$0.0008 | \$0.0008 | \$0.0008 | \$0.0024 | \$0.0001 | **-\$0.0124** | \$0.0108 | **-1.15R** |
| `PAPER_20261104_171805` | `VWAP_EMA` | SHORT | 1 | 2026-10-30 18:05 | 2026-10-30 19:00 | \$3,965.59 | \$3,937.67 | +\$0.0295 | \$0.0008 | \$0.0008 | \$0.0008 | \$0.0024 | \$0.0001 | **+\$0.0271** | \$0.0120 | **+2.26R** |
| `PAPER_20261104_171919` | `VWAP_EMA` | LONG | 1 | 2026-11-02 10:40 | 2026-11-02 13:45 | \$3,909.13 | \$3,897.59 | -\$0.0100 | \$0.0008 | \$0.0008 | \$0.0008 | \$0.0024 | \$0.0001 | **-\$0.0124** | \$0.0108 | **-1.15R** |
| `PAPER_20261104_171949` | `VWAP_EMA` | SHORT | 1 | 2026-11-03 11:25 | 2026-11-03 18:20 | \$3,915.53 | \$3,926.67 | -\$0.0096 | \$0.0008 | \$0.0008 | \$0.0008 | \$0.0024 | \$0.0001 | **-\$0.0120** | \$0.0104 | **-1.15R** |
| `PAPER_20261104_172030` | `VWAP_EMA` | LONG | 1 | 2026-11-04 17:50 | 2026-11-04 19:00 | \$4,035.58 | \$4,020.92 | -\$0.0130 | \$0.0008 | \$0.0008 | \$0.0008 | \$0.0024 | \$0.0001 | **-\$0.0154** | \$0.0139 | **-1.11R** |

### 4.2 Exact Source Formulas & Implementation
From [`services/delta_paper_broker.py#L471-L479`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/delta_paper_broker.py#L471-L479):
```python
brokerage_fee = round(allocated_entry_fee + exit_fee, 4)
total_gst = round(brokerage_fee * (self.cfg.gst_rate_pct / 100.0), 4)
deducted_gst = total_gst if getattr(self.cfg, "apply_gst", False) else 0.0

total_price_impact = round(allocated_entry_slip + exit_slippage, 4)
spread_cost = round(total_price_impact / 2.0, 4)
slippage_cost = round(total_price_impact / 2.0, 4)
total_friction = round(brokerage_fee + deducted_gst + total_price_impact, 4)
net_pnl = round(gross_pnl - total_friction, 4)
```

---

## 5. GST Calculation Verification & Discrepancy Resolution

### 5.1 The Phase 3.12 Discrepancy
In Phase 3.12, the following numbers were reported:
- Total Brokerage: $\$0.0056$ ($7 \times \$0.0008$)
- Total GST: $\approx \$0.0010$ ($7 \times \$0.000144$)
- Total Spread: $\$0.0056$ ($7 \times \$0.0008$)
- Total Slippage: $\$0.0056$ ($7 \times \$0.0008$)
- **Reported Friction:** $\$0.0168$

Notice that:
$$\$0.0056 (\text{brokerage}) + \$0.0056 (\text{spread}) + \$0.0056 (\text{slippage}) = \$0.0168$$
If GST ($\$0.0010$) were added, the total friction would be $\$0.0178$.

### 5.2 Resolution & Mathematical Proof
1. **The Setting:** In `config/orb_settings.py:85`, GST deduction is controlled by:
   ```python
   apply_gst: bool = os.getenv("DELTA_APPLY_GST", "false").lower() == "true"
   ```
2. **Current Environment:** Neither `.env` nor the systemd environment exports `DELTA_APPLY_GST`. Therefore, `apply_gst` defaults to `False`.
3. **Execution Logic:**
   - In `close_simulated_trade`:
     `deducted_gst = total_gst if getattr(self.cfg, "apply_gst", False) else 0.0`
     Evaluates to: `deducted_gst = 0.0`.
   - Therefore:
     $$\text{total\_friction} = \text{brokerage\_fee} + 0.0 + \text{total\_price\_impact} = 0.0008 + 0.0016 = \$0.0024 \text{ per trade}$$
   - Across 7 trades: $7 \times \$0.0024 = \$0.0168$.
   - **Conclusion:** GST was **EXCLUDED** from the reported total friction in the CSV ledger and cash deduction. The GST figure ($\$0.0001$ per trade) was computed and recorded in `closed_record["gst"]` purely for statutory compliance reporting (`gst_applied: False`), but was not subtracted from equity.
4. **Impact if GST were Applied:**
   - Total friction would increase by $\$0.0010$ to $\$0.0178$.
   - Net realized P&L would shift from $-\$0.0121$ to $-\$0.0131$.
   - Reconciled equity would shift from $\$9,999.9879$ to $\$9,999.9869$.
   - Verified via unit test `test_gst_inclusion_vs_exclusion_reconciliation`.

---

## 6. Equity & Ledger Reconciliation

| Accounting Component | 14-Day Forward CSV Ledger | Active Forward Runner State | Live Streamlit Dashboard |
| :--- | :---: | :---: | :---: |
| **State Source File** | `forward_test_14d_trades.csv` | `paper_forward_24h_state.json` | `paper_account_xautusd.json` |
| **Starting Equity** | \$10,000.0000 | \$10,000.0000 | \$10,000.0000 |
| **Cumulative Realized P&L** | -\$0.0121 | \$0.0000 | \$0.0000 |
| **Open Unrealized P&L** | \$0.0000 (Flat) | \$0.0000 (Flat) | \$0.0000 (Flat) |
| **Current Account Balance** | \$9,999.9879 | \$10,000.0000 | \$10,000.0000 |
| **Current Total Equity** | \$9,999.9879 ($\approx \$9,999.99$) | \$10,000.0000 | \$10,000.0000 |
| **Reconciliation Formula** | $\text{Start} + \text{Net} + \text{uPnL} = \text{Equity}$ | $\text{Start} + \text{Net} + \text{uPnL} = \text{Equity}$ | $\text{Start} + \text{Net} + \text{uPnL} = \text{Equity}$ |
| **Reconciliation Discrepancy** | **\$0.0000 (Exact)** | **\$0.0000 (Exact)** | **\$0.0000 (Exact)** |

*Observation on State Files:* The historical 14-day study completed with equity $\$9,999.99$. The active forward runner on EC2 was initialized with a fresh starting baseline of $\$10,000.0000$.

---

## 7. Duplicate-Event & Restart-Recovery Audit

### 7.1 Event Concurrency & Recovery Mechanism
1. **Thread-Level Protection:** `self._lock = threading.RLock()` wraps order opening and order closing. Only one thread can modify position state at a time.
2. **Process Restart Recovery:**
   - In `_load_state()` ([`services/delta_paper_broker.py:65-92`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/delta_paper_broker.py#L65-L92)), the broker loads `open_position`, `closed_trades`, and `cash_balance` from disk.
   - When a position is open on restart, lines 217-218 reject new trade entries with:
     `"Position already open: {direction} {lots} lots {symbol}"`.
   - Verified via unit test `test_reliability_restart_position_recovery_and_duplicate_prevention`.
3. **Atomic Disk Persistence:**
   - Writes are staged to `.tmp` before renaming:
     ```python
     temp_file = self.state_file.with_suffix(".tmp")
     with open(temp_file, "w", encoding="utf-8") as f:
         json.dump(payload, f, indent=2)
     temp_file.replace(self.state_file)
     ```
   - Prevents corrupted JSON files if process termination occurs mid-write.

### 7.2 Reliability Findings & Vulnerability Assessment
1. **Multi-Process Concurrency Limitation:**
   - `threading.RLock()` is in-memory and thread-local. It does not provide cross-process locking if two independent processes access the same state file simultaneously.
   - *Current Mitigation:* Systemd service unit (`Type=simple`) ensures only a single runner daemon executes.
   - *Recommendation:* Incorporate OS-level file locking (`filelock` package or `fcntl.flock`) during read-modify-write operations for defense-in-depth.
2. **Trade ID Granularity:**
   - `trade_id = f"PAPER_{ts_fmt}_{clean_dir}"` uses 1-second resolution (`%Y%m%d_%H%M%S`).
   - *Current Mitigation:* `DeltaPaperBot` only evaluates candle completions (every 300 seconds) and enforces a maximum of 1 trade per day.
   - *Recommendation:* Append a 6-character UUID (`uuid4().hex[:6]`) to guarantee global uniqueness.

---

## 8. WebSocket and Candle-Feed Health

Telemetry captured live from AWS EC2 daemon (`PID 8328`):

- **WebSocket Connection:** Connected to `wss://socket.india.delta.exchange` (SSL/TLS verified).
- **Session Reconnect Events:** `0` reconnects; `0` socket drops.
- **Tick Volume Processed:** `9,698` live market depth and trade ticks ingested.
- **5-Minute Completed Bars:** `454` total (5 finalized during live session: 01:30, 01:35, 01:40, 01:45, 01:50 UTC).
- **15-Minute Completed Bars:** `150` total (1 finalized during live session: 01:45 UTC).
- **Candle Integrity:** `0` duplicate timestamps, `0` missing candle gaps.
- **Systemd Service Restarts:** `NRestarts=0` on both `delta-paper-trading.service` and `delta-paper-dashboard.service`.
- **Systemd Journal Error Logs:** `0` errors logged (`journalctl -u delta-paper-trading.service -p err` returned clean).

---

## 9. Monitoring Duration & Run Status

To maintain reporting integrity without fabricating run durations:

- **Service Start Timestamp:** `2026-11-05 01:33:05 UTC` (07:03:05 IST)
- **Current Audit Timestamp:** `2026-11-05 01:56:21 UTC` (07:26:21 IST)
- **Actual Elapsed Runtime:** `1,387.6 seconds` (**23 minutes 07 seconds**)
- **Target Configured Duration:** `86,400 seconds` (**24.0 hours**)
- **Remaining Duration:** `85,012 seconds` (**23 hours 36 minutes**)
- **24-Hour Test Completion Status:** **IN PROGRESS (NOT COMPLETE)**.
  > [!NOTE]
  > The test is actively running in the background under systemd. It has NOT completed 24 hours. Full 24-hour evaluation will be available on 2026-11-06 at 01:33:05 UTC.

---

## 10. Strategy Evaluations & Statistical Reality

### 10.1 Active Session Evaluations
- **Evaluations Completed:** 5 candle evaluation cycles.
- **Signals Generated:** `0`.
- **Signals Rejected:** `5`.
- **Dominant Rejection Reason:** `"Session open buffer: entries blocked until 09:45 IST."`
  - The strategy correctly filtered entry attempts prior to 09:45 IST to avoid erratic pre-session price spreads.
- **Zero-Fill Events:** `0`.
- **Partial-Fill Events:** `0`.

### 10.2 Cumulative Ledger Statistical Profile ($N = 7$ Closed Trades)
- **Total Trades:** 7 (2 Wins, 5 Losses)
- **Win Rate:** 28.57%
- **Wilson Score 95% Confidence Interval:** $[8.22\%, 64.11\%]$ (Interval width: $55.89\%$)
- **Profit Factor:** 0.83 (Gross Profit \$0.0640 / Gross Loss \$0.0761)
- **Net Expectancy:** $-\$0.0017$ per trade
- **Average Realized R:** $-0.17\text{R}$
- **Maximum Drawdown:** $\$0.0442$ ($0.0004\%$)
- **Statistical Significance Assessment:**
  > [!WARNING]
  > With $N = 7$ trades, the confidence interval is excessively wide. A sample of 7 trades cannot establish profitability, viability, or edge. At least $N \ge 350$ closed trades are required to achieve a $\pm 5\%$ precision boundary. Strategy parameters must remain unchanged.

---

## 11. Outstanding Architectural Observations & Recommendations

| Observation | Impact Level | Description | Recommended Minimal Mitigation |
| :--- | :---: | :--- | :--- |
| **State File Path Mismatch** | Minor UI | `run_24h_paper_test.py` saves to `data/paper_forward_24h_state.json`, while `ui/dashboard.py` defaults to `data/paper_account_xautusd.json`. | Modify `ui/dashboard.py` to prioritize `paper_forward_24h_state.json` if it exists and has more recent updates. |
| **Explicit GST Setting** | Configuration | `DELTA_APPLY_GST` is unset in `.env`, causing GST to be calculated but not deducted from cash balance. | Maintain current default (`false`) for consistency with 14d baseline, or set `DELTA_APPLY_GST=true` if regulatory net cash deduction is mandated. |
| **Cross-Process File Lock** | Reliability | Process-local `RLock` does not protect against concurrent CLI scripts accessing the same JSON state file. | Add `filelock` context manager around JSON read/write routines. |
| **Trade ID Granularity** | Reliability | Trade ID collision possible if two orders trigger in the same second. | Append 6-character random UUID suffix to trade ID generator. |

---

**FINAL VERDICT:** **PASS**. All accounting formulas, GST mechanics, and equity balances are verified. The EC2 paper deployment is fully operational, stable, and strictly bounded to simulated execution.

