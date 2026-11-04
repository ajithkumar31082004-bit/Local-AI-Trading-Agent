# Forward Test Day 13 Daily Report (2026-11-02)

- **Coverage**: `2026-11-02 22:40:00+05:30` to `2026-11-03 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0153`
- **End Equity**: `$10,000.0033`
- **Day Net P&L**: `$-0.0120`
- **Trades Closed Today**: `1` (0W / 1L)
- **Trades Opened Today**: `1`
- **Candidates Evaluated**: `5` | **Accepted**: `1`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `TRENDING_BULLISH` | 46 | 16.0% |
| `UNCERTAIN` | 87 | 30.2% |
| `RANGING` | 57 | 19.8% |
| `VOLATILE_BREAKOUT` | 8 | 2.8% |
| `TRENDING_BEARISH` | 90 | 31.2% |

### Top Rejection Reasons

- **(522x)** `REGIME_UNCERTAIN`
- **(228x)** `DISALLOWED_IN_RANGING`
- **(201x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(180x)** `DISALLOWED_IN_TRENDING_BEARISH`
- **(131x)** `REJECTION_NOT_CONFIRMED`

### Trades Closed Today

| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171949_SHORT` | `VWAP_EMA` | SHORT | $3915.53 | $3926.67 | $-0.0096 | $0.0008 | $0.0016 | **$-0.0120** | `-1.15R` | `SL` |

### Trades Opened Today

| Trade ID | Strategy | Side | Entry Time | Entry Fill | Lots | Stop Loss | Take Profit | Signal Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171949_SHORT` | `VWAP_EMA` | SHORT | `2026-11-03 11:25:00+05:30` | $3915.53 | 1 | $3925.88 | $3890.12 | Confirmed SHORT VWAP + EMA confluence with pullback and continuation rejection. |
