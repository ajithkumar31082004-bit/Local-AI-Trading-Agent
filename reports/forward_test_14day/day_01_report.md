# Forward Test Day 01 Daily Report (2026-10-21)

- **Coverage**: `2026-10-21 22:40:00+05:30` to `2026-10-22 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0000`
- **End Equity**: `$10,000.0321`
- **Day Net P&L**: `$+0.0321`
- **Trades Closed Today**: `1` (1W / 0L)
- **Trades Opened Today**: `1`
- **Candidates Evaluated**: `11` | **Accepted**: `1`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `UNCERTAIN` | 90 | 31.2% |
| `RANGING` | 12 | 4.2% |
| `VOLATILE_BREAKOUT` | 16 | 5.6% |
| `TRENDING_BEARISH` | 170 | 59.0% |

### Top Rejection Reasons

- **(540x)** `REGIME_UNCERTAIN`
- **(340x)** `DISALLOWED_IN_TRENDING_BEARISH`
- **(198x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(147x)** `REJECTION_NOT_CONFIRMED`
- **(114x)** `NO_VOLATILITY_COMPRESSION`

### Trades Closed Today

| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171333_SHORT` | `VWAP_EMA` | SHORT | $4093.99 | $4061.17 | $+0.0345 | $0.0008 | $0.0016 | **$+0.0321** | `+2.23R` | `TP` |

### Trades Opened Today

| Trade ID | Strategy | Side | Entry Time | Entry Fill | Lots | Stop Loss | Take Profit | Signal Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171333_SHORT` | `VWAP_EMA` | SHORT | `2026-10-22 13:15:00+05:30` | $4093.99 | 1 | $4108.35 | $4060.36 | Confirmed SHORT VWAP + EMA confluence with pullback and continuation rejection. |
