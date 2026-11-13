# Forward Test Day 05 Daily Report (2026-10-25)

- **Coverage**: `2026-10-25 22:40:00+05:30` to `2026-10-26 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0321`
- **End Equity**: `$10,000.0130`
- **Day Net P&L**: `$-0.0191`
- **Trades Closed Today**: `1` (0W / 1L)
- **Trades Opened Today**: `0`
- **Candidates Evaluated**: `0` | **Accepted**: `0`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `TRENDING_BULLISH` | 167 | 58.0% |
| `UNCERTAIN` | 97 | 33.7% |
| `VOLATILE_BREAKOUT` | 16 | 5.6% |
| `TRENDING_BEARISH` | 8 | 2.8% |

### Top Rejection Reasons

- **(582x)** `REGIME_UNCERTAIN`
- **(334x)** `DISALLOWED_IN_TRENDING_BULLISH`
- **(191x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(187x)** `OUTSIDE_SESSION_HOURS`
- **(92x)** `REJECTION_NOT_CONFIRMED`

### Trades Closed Today

| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171418_SHORT` | `VWAP_EMA` | SHORT | $4001.86 | $4020.17 | $-0.0167 | $0.0008 | $0.0016 | **$-0.0191** | `-1.09R` | `SL` |
