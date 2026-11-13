# Forward Test Day 09 Daily Report (2026-10-29)

- **Coverage**: `2026-10-29 22:40:00+05:30` to `2026-10-30 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0006`
- **End Equity**: `$10,000.0277`
- **Day Net P&L**: `$+0.0271`
- **Trades Closed Today**: `1` (1W / 0L)
- **Trades Opened Today**: `1`
- **Candidates Evaluated**: `3` | **Accepted**: `1`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `UNCERTAIN` | 166 | 57.6% |
| `RANGING` | 54 | 18.8% |
| `VOLATILE_BREAKOUT` | 14 | 4.9% |
| `TRENDING_BEARISH` | 54 | 18.8% |

### Top Rejection Reasons

- **(996x)** `REGIME_UNCERTAIN`
- **(216x)** `DISALLOWED_IN_RANGING`
- **(122x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(108x)** `DISALLOWED_IN_TRENDING_BEARISH`
- **(50x)** `NO_VOLATILITY_COMPRESSION`

### Trades Closed Today

| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171805_SHORT` | `VWAP_EMA` | SHORT | $3965.59 | $3937.67 | $+0.0295 | $0.0008 | $0.0016 | **$+0.0271** | `+2.26R` | `TP` |

### Trades Opened Today

| Trade ID | Strategy | Side | Entry Time | Entry Fill | Lots | Stop Loss | Take Profit | Signal Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171805_SHORT` | `VWAP_EMA` | SHORT | `2026-10-30 18:05:00+05:30` | $3965.59 | 1 | $3977.56 | $3936.89 | Confirmed SHORT VWAP + EMA confluence with pullback and continuation rejection. |
