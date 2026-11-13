# Forward Test Day 02 Daily Report (2026-10-22)

- **Coverage**: `2026-10-22 22:40:00+05:30` to `2026-10-23 22:35:00+05:30` (288 completed 5m bars)
- **Start Equity**: `$10,000.0321`
- **End Equity**: `$10,000.0321`
- **Day Net P&L**: `$+0.0000`
- **Trades Closed Today**: `0` (0W / 0L)
- **Trades Opened Today**: `1`
- **Candidates Evaluated**: `1` | **Accepted**: `1`

### Market Regime Distribution

| Market Regime | Bar Count | Percentage |
| :--- | :---: | :---: |
| `TRENDING_BEARISH` | 140 | 48.6% |
| `UNCERTAIN` | 93 | 32.3% |
| `VOLATILE_BREAKOUT` | 15 | 5.2% |
| `RANGING` | 40 | 13.9% |

### Top Rejection Reasons

- **(558x)** `REGIME_UNCERTAIN`
- **(280x)** `DISALLOWED_IN_TRENDING_BEARISH`
- **(219x)** `OUTSIDE_SESSION_HOURS`
- **(195x)** `STRATEGY_DISABLED_EXPERIMENTAL`
- **(160x)** `DISALLOWED_IN_RANGING`

### Trades Closed Today

*Zero closed trades today. Capital preserved.*

### Trades Opened Today

| Trade ID | Strategy | Side | Entry Time | Entry Fill | Lots | Stop Loss | Take Profit | Signal Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `PAPER_20261104_171418_SHORT` | `VWAP_EMA` | SHORT | `2026-10-23 20:35:00+05:30` | $4001.86 | 1 | $4019.36 | $3962.05 | Confirmed SHORT VWAP + EMA confluence with pullback and continuation rejection. |
