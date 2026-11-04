# Forward Test Day 12 Daily Report (2026-11-01)

- **Coverage**: `2026-11-01 22:40:00+05:30` to `2026-11-02 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0277`
- **End Equity**: `$10,000.0153`
- **Day Net P&L**: `$-0.0124`
- **Trades Closed Today**: `1` (0W / 1L)
- **Trades Opened Today**: `1`
- **Candidates Evaluated**: `5` | **Accepted**: `1`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `UNCERTAIN` | 102 | 35.4% |
| `RANGING` | 44 | 15.3% |
| `VOLATILE_BREAKOUT` | 16 | 5.6% |
| `TRENDING_BEARISH` | 10 | 3.5% |
| `TRENDING_BULLISH` | 116 | 40.3% |

### Top Rejection Reasons

- **(612x)** `REGIME_UNCERTAIN`
- **(232x)** `DISALLOWED_IN_TRENDING_BULLISH`
- **(186x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(186x)** `OUTSIDE_SESSION_HOURS`
- **(176x)** `DISALLOWED_IN_RANGING`

### Trades Closed Today

| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171919_LONG` | `VWAP_EMA` | LONG | $3909.13 | $3897.59 | $-0.0100 | $0.0008 | $0.0016 | **$-0.0124** | `-1.15R` | `SL` |

### Trades Opened Today

| Trade ID | Strategy | Side | Entry Time | Entry Fill | Lots | Stop Loss | Take Profit | Signal Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171919_LONG` | `VWAP_EMA` | LONG | `2026-11-02 10:40:00+05:30` | $3909.13 | 1 | $3898.37 | $3935.35 | Confirmed LONG VWAP + EMA confluence with pullback and continuation rejection. |
