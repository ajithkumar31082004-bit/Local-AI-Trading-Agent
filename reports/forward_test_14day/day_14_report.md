# Forward Test Day 14 Daily Report (2026-11-03)

- **Coverage**: `2026-11-03 22:40:00+05:30` to `2026-11-04 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0033`
- **End Equity**: `$9,999.9879`
- **Day Net P&L**: `$-0.0154`
- **Trades Closed Today**: `1` (0W / 1L)
- **Trades Opened Today**: `1`
- **Candidates Evaluated**: `5` | **Accepted**: `1`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `RANGING` | 63 | 21.9% |
| `UNCERTAIN` | 95 | 33.0% |
| `VOLATILE_BREAKOUT` | 12 | 4.2% |
| `TRENDING_BULLISH` | 118 | 41.0% |

### Top Rejection Reasons

- **(570x)** `REGIME_UNCERTAIN`
- **(252x)** `DISALLOWED_IN_RANGING`
- **(236x)** `DISALLOWED_IN_TRENDING_BULLISH`
- **(193x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(96x)** `REJECTION_NOT_CONFIRMED`

### Trades Closed Today

| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_172030_LONG` | `VWAP_EMA` | LONG | $4035.58 | $4020.92 | $-0.0130 | $0.0008 | $0.0016 | **$-0.0154** | `-1.11R` | `SL` |

### Trades Opened Today

| Trade ID | Strategy | Side | Entry Time | Entry Fill | Lots | Stop Loss | Take Profit | Signal Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_172030_LONG` | `VWAP_EMA` | LONG | `2026-11-04 17:50:00+05:30` | $4035.58 | 1 | $4021.73 | $4068.12 | Confirmed LONG VWAP + EMA confluence with pullback and continuation rejection. |
