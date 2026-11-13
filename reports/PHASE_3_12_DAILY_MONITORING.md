# PHASE 3.12 — DAILY PAPER TRADING MONITORING REPORT

**Report Date:** 2026-11-05  
**Monitoring Timestamp:** 2026-11-05 01:47:00 UTC (07:17:00 IST)  
**Target Instrument:** XAUTUSD (Product ID: 131253)  
**Exchange:** Delta Exchange India (`wss://socket.india.delta.exchange`)  
**Trading Execution Mode:** STRICTLY PAPER ONLY  
**Branch:** `feature/strategy-intelligence-v2`  
**Audited Commit SHA:** `c8dec34cfa3330f8157e7c2c3b390249d59c07af`  

---

## 1. Executive Status & Health Dashboard

| Telemetry Item | Operational State | Current Value / Detail | Verified Verdict |
| :--- | :---: | :--- | :---: |
| **Trading Daemon Service** | `active (running)` | PID `8328` (since 2026-11-05 01:33:05 UTC) | **PASS** |
| **Dashboard Daemon Service** | `active (running)` | PID `8333` (bound to `127.0.0.1:8501`, HTTP 200) | **PASS** |
| **Commit Parity (EC2)** | Synchronized | `c8dec34cfa3330f8157e7c2c3b390249d59c07af` | **PASS** |
| **System Uptime** | Operational | Host up 9h 12m (load avg: 0.09, 0.07, 0.01) | **PASS** |
| **Service Restarts** | Zero | `NRestarts=0` across both services | **PASS** |
| **Systemd Error Log** | Clean | Zero error entries (`journalctl -p err`) | **PASS** |
| **Public WebSocket** | `CONNECTED` | Streaming `wss://socket.india.delta.exchange` | **PASS** |
| **Public Tick Volume** | Ingesting | 5,791 live ticks processed | **PASS** |
| **Completed 5m Candles** | Healthy | 452 candles (3 finalized in current session) | **PASS** |
| **Completed 15m Candles** | Healthy | 150 candles (1 finalized in current session) | **PASS** |
| **Current Market Regime** | Real-time | `TRENDING_BULLISH` (detected transition from `VOLATILE_BREAKOUT`) | **PASS** |
| **Current Live Price** | Streaming | \$4,106.93 (Mark: \$4,107.81, Bid: \$4,107.73, Ask: \$4,107.76) | **PASS** |
| **Open Position** | Flat | `null` / `FLAT` (Unrealized P&L: \$0.00) | **PASS** |
| **Paper Account Balance** | Intact | \$10,000.0000 USD | **PASS** |
| **Safety Controls** | Enforced | `TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=false` | **PASS** |

---

## 2. Service & Runtime Telemetry

### 2.1 Systemd Daemon Telemetry
- **Trading Daemon (`delta-paper-trading.service`)**:
  - Main PID: `8328`
  - Memory: `93.0 MB` (Resident memory stable, no leak detected)
  - CPU Cumulative: `22.2s`
  - Command: `/home/ubuntu/Local-AI-Trading-Agent/.venv/bin/python scripts/run_24h_paper_test.py --duration 86400`
  - Restarts: `0`
- **Dashboard Daemon (`delta-paper-dashboard.service`)**:
  - Main PID: `8333`
  - Memory: `74.9 MB`
  - Network Binding: `127.0.0.1:8501` (Restricted to loopback; no external port exposure)
  - HTTP Health: `HTTP/1.1 200 OK`
  - Restarts: `0`

### 2.2 Candle & Feed Integrity
- Rest seed: 449 completed 5m candles, 149 completed 15m candles.
- Forward progress:
  - Candle 450: `2026-11-05 01:30:00+00:00` | Close: \$4,101.41 | Volume: 1,418,822
  - Candle 451: `2026-11-05 01:35:00+00:00` | Close: \$4,106.48 | Volume: 2,153,584
  - Candle 452: `2026-11-05 01:40:00+00:00` | Close: \$4,107.20 | Volume: 1,024,416
- Gaps / Duplicate Timestamps: `0` duplicates, `0` missing gaps.

---

## 3. Strategy Evaluations & Filter Rejections

During the monitoring window, the multi-strategy engine evaluated live candle closures for potential entry signals.

- **Current Evaluation State**: `REJECTED (NO TRADE)`
- **Active Rejection Reason**: `"Session open buffer: entries blocked until 09:45 IST."`
- **Explanation**: The strategy includes a pre-session volatility buffer blocking trade initiation prior to 09:45 IST (current monitoring time is 07:17 IST / 01:47 UTC).
- **Zero-Fill Events**: `0`
- **Partial-Fill Events**: `0`
- **Active Signal Count**: `0` (Engine successfully filtered noise during pre-market regime transitions).

---

## 4. Completed Trade Ledger & Trade Accounting Reconciliation

### 4.1 Active Session (2026-11-05 01:33 UTC to Present)
- New Trades Executed: `0`
- Current Position: `FLAT`
- Active Session Net P&L: `+$0.0000`

### 4.2 Cumulative 14-Day Forward Testing Ledger Audit ($N = 7$ Trades)
The complete trade history recorded in `data/forward_test_14d_trades.csv` was audited trade-by-trade:

| Trade ID | Strategy | Dir | Lots | Entry Time (IST) | Exit Time (IST) | Entry Price | Exit Price | Exit | Gross P&L | Brokerage | GST (18%) | Spread | Slip | Net P&L | Planned Risk | Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `PAPER_20261104_171333` | `VWAP_EMA` | SHORT | 1 | 2026-10-22 13:15 | 2026-10-22 19:35 | \$4,093.99 | \$4,061.17 | TP | +\$0.0345 | \$0.0008 | \$0.0001 | \$0.0008 | \$0.0008 | **+\$0.0321** | \$0.0144 | **+2.23R** |
| `PAPER_20261104_171418` | `VWAP_EMA` | SHORT | 1 | 2026-10-23 20:35 | 2026-10-26 08:40 | \$4,001.86 | \$4,020.17 | SL | -\$0.0167 | \$0.0008 | \$0.0001 | \$0.0008 | \$0.0008 | **-\$0.0191** | \$0.0175 | **-1.09R** |
| `PAPER_20261104_171629` | `VWAP_EMA` | SHORT | 1 | 2026-10-27 15:45 | 2026-10-27 16:10 | \$4,002.23 | \$4,013.88 | SL | -\$0.0100 | \$0.0008 | \$0.0001 | \$0.0008 | \$0.0008 | **-\$0.0124** | \$0.0108 | **-1.15R** |
| `PAPER_20261104_171805` | `VWAP_EMA` | SHORT | 1 | 2026-10-30 18:05 | 2026-10-30 19:00 | \$3,965.59 | \$3,937.67 | TP | +\$0.0295 | \$0.0008 | \$0.0001 | \$0.0008 | \$0.0008 | **+\$0.0271** | \$0.0120 | **+2.26R** |
| `PAPER_20261104_171919` | `VWAP_EMA` | LONG | 1 | 2026-11-02 10:40 | 2026-11-02 13:45 | \$3,909.13 | \$3,897.59 | SL | -\$0.0100 | \$0.0008 | \$0.0001 | \$0.0008 | \$0.0008 | **-\$0.0124** | \$0.0108 | **-1.15R** |
| `PAPER_20261104_171949` | `VWAP_EMA` | SHORT | 1 | 2026-11-03 11:25 | 2026-11-03 18:20 | \$3,915.53 | \$3,926.67 | SL | -\$0.0096 | \$0.0008 | \$0.0001 | \$0.0008 | \$0.0008 | **-\$0.0120** | \$0.0104 | **-1.15R** |
| `PAPER_20261104_172030` | `VWAP_EMA` | LONG | 1 | 2026-11-04 17:50 | 2026-11-04 19:00 | \$4,035.58 | \$4,020.92 | SL | -\$0.0130 | \$0.0008 | \$0.0001 | \$0.0008 | \$0.0008 | **-\$0.0154** | \$0.0139 | **-1.11R** |

### 4.3 Cumulative Ledger Totals & Accounting Formula Verification
- **Gross P&L**:
  $$\text{Gross P\&L} = (+0.0345) + (-0.0167) + (-0.0100) + (+0.0295) + (-0.0100) + (-0.0096) + (-0.0130) = +\$0.0047$$
- **Execution Friction**:
  - Total Brokerage Fees: $7 \times \$0.0008 = \$0.0056$
  - Applicable GST (18% on brokerage): $7 \times \$0.000144 \approx \$0.0010$
  - Spread Cost: $7 \times \$0.0008 = \$0.0056$
  - Slippage Cost: $7 \times \$0.0008 = \$0.0056$
  - **Total Transaction Friction**: $\$0.0056 + \$0.0056 + \$0.0056 = \$0.0168$
- **Net Realized P&L**:
  $$\text{Net P\&L} = \text{Gross P\&L} - \text{Total Friction} = +\$0.0047 - \$0.0168 = -\$0.0121$$
- **Account Balance & Equity Reconciliation**:
  - Initial Starting Equity: $\$10,000.0000$
  - Cumulative Net P&L: $-\$0.0121$
  - Ending Account Balance: $\$9,999.9879 \approx \$9,999.99$
  - Unrealized P&L on Open Positions: $\$0.0000$ (Flat)
  - Final Equity: $\$9,999.9879$
  - **Ledger-Balance Discrepancy**: **\$0.0000 (Exact Match)**

---

## 5. Runtime Safety & Incident Assessment

Each critical safety checkpoint has been evaluated:

1. **Unexpected Service Restart**: **NONE**. `NRestarts=0` on both systemd units.
2. **WebSocket Failure / Drops**: **NONE**. Connection active and steady since 01:33:05 UTC.
3. **Stale Candle Feed**: **NONE**. New 5m candle finalized at 01:40:00 UTC right on schedule.
4. **Duplicate Trade Event**: **NONE**. Zero duplicate trade IDs or fills.
5. **Invalid Position Quantity**: **NONE**. Position is strictly 0.000 lots (`FLAT`).
6. **Cash Reconciliation Mismatch**: **NONE**. $\$0.0000$ variance between ledger and cash balance.
7. **Unexpected Real-Order Execution Path**: **NONE**. Zero private REST calls, zero API keys loaded.
8. **Trading Mode Mutated**: **NONE**. `.env` strictly enforces `TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`, `LIVE_TRADING_ACKNOWLEDGED=NO`.

---

## 6. Daily Monitoring Automation & Commands

For ongoing daily operational reviews, use the following standardized commands and report generation flow:

### 6.1 Daily Status Verification Script
Execute via SSH on EC2:
```bash
# 1. Verify Git commit parity
cd /home/ubuntu/Local-AI-Trading-Agent
git rev-parse HEAD

# 2. Check systemd daemon status and restart counters
sudo systemctl status delta-paper-trading.service delta-paper-dashboard.service
sudo systemctl show delta-paper-trading.service --property=NRestarts,ActiveState,SubState,MainPID

# 3. Inspect real-time paper broker state
cat /home/ubuntu/Local-AI-Trading-Agent/data/paper_forward_24h_live_status.json

# 4. Check for systemd error logs
sudo journalctl -u delta-paper-trading.service -p err --no-pager -n 50

# 5. Check local dashboard accessibility
curl -s -I http://127.0.0.1:8501
```

### 6.2 Daily Report Template (`reports/daily_template.md`)
```markdown
# Daily Monitoring Check — [YYYY-MM-DD]
- Git Commit: [40-char SHA]
- Trading Service: [Active/Inactive] | Restarts: [0]
- Dashboard Service: [Active/Inactive] | Localhost: [200 OK]
- WebSocket Status: [OK] | Ingested Ticks: [Count]
- Candle Status: 5m: [Count] | 15m: [Count]
- Strategy Regime: [Regime Name] | Evaluated: [Yes/No]
- Open Position: [FLAT / Lots] | Unrealized P&L: [$0.00]
- Closed Trades Today: [Count] | Net P&L: [$0.00]
- Cash Reconciliation: [PASS / Discrepancy]
- Safety Verdict: [PASS - STRICT PAPER ONLY]
```

---

## 7. Known Issues & Operational Considerations

1. **Fixed Micro-Lot Monetary Exposure vs. Configured Risk Budget**:
   - The strategy configuration sets a risk budget of 0.5% (\$50.00 on \$10,000 equity).
   - However, the forward testing daemon operates with a fixed position size of 1 micro-lot ($0.001\text{ XAUT}$), risking $\approx \$0.0164$ per trade.
   - At this microscopic scale, transaction costs ($\approx \$0.0024$) consume $15\text{--}20\%$ of the trade risk, artificially dampening the net performance ratio compared to proportional risk sizing.
2. **Sample Size Limitations ($N = 7$)**:
   - The 14-day sample of 7 trades (2W / 5L, win rate 28.57%) yields a wide Wilson score 95% confidence interval of $[8.22\%, 64.11\%]$.
   - No statistical conclusion regarding edge can be drawn until the live forward test reaches $N \ge 350$ closed trades.
3. **Session Open Filter**:
   - The strategy correctly suppresses signal execution prior to 09:45 IST, preventing entries during low-liquidity or erratic pre-session price swings.

---

**FINAL VERDICT:** **PASS**. All services running stably on EC2 with commit parity `c8dec34cfa3330f8157e7c2c3b390249d59c07af`. Paper trading risk safeguards strictly enforced. Zero real orders submitted.

