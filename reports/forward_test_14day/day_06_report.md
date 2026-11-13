# Forward Test Day 06 Daily Report (2026-10-26)

- **Coverage**: `2026-10-26 22:40:00+05:30` to `2026-10-27 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0130`
- **End Equity**: `$10,000.0006`
- **Day Net P&L**: `$-0.0124`
- **Trades Closed Today**: `1` (0W / 1L)
- **Trades Opened Today**: `1`
- **Candidates Evaluated**: `4` | **Accepted**: `1`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `UNCERTAIN` | 117 | 40.6% |
| `RANGING` | 87 | 30.2% |
| `VOLATILE_BREAKOUT` | 11 | 3.8% |
| `TRENDING_BEARISH` | 24 | 8.3% |
| `TRENDING_BULLISH` | 49 | 17.0% |

### Top Rejection Reasons

- **(702x)** `REGIME_UNCERTAIN`
- **(348x)** `DISALLOWED_IN_RANGING`
- **(171x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(98x)** `DISALLOWED_IN_TRENDING_BULLISH`
- **(70x)** `OUTSIDE_SESSION_HOURS`

### Trades Closed Today

| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171629_SHORT` | `VWAP_EMA` | SHORT | $4002.23 | $4013.88 | $-0.0100 | $0.0008 | $0.0016 | **$-0.0124** | `-1.15R` | `SL` |

### Trades Opened Today

| Trade ID | Strategy | Side | Entry Time | Entry Fill | Lots | Stop Loss | Take Profit | Signal Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171629_SHORT` | `VWAP_EMA` | SHORT | `2026-10-27 15:45:00+05:30` | $4002.23 | 1 | $4013.07 | $3975.74 | Confirmed SHORT VWAP + EMA confluence with pullback and continuation rejection. |
