"""Perform statistical validation and confidence interval analysis on XAUTUSD forward trades."""

import math
import pandas as pd
import numpy as np

trades_df = pd.read_csv("data/forward_test_14d_trades.csv")

def wilson_score_interval(wins: int, total: int, confidence: float = 0.95):
    """Calculate Wilson score confidence interval for binomial proportion."""
    if total == 0:
        return 0.0, 0.0, 0.0
    z = 1.95996  # 95% confidence z-score
    p_hat = wins / total
    denominator = 1.0 + (z**2 / total)
    centre = (p_hat + (z**2 / (2.0 * total))) / denominator
    margin = (z * math.sqrt((p_hat * (1.0 - p_hat) / total) + (z**2 / (4.0 * (total**2))))) / denominator
    lower = max(0.0, centre - margin)
    upper = min(1.0, centre + margin)
    return p_hat, lower, upper

def required_sample_size(p: float = 0.35, margin_of_error: float = 0.05, confidence: float = 0.95):
    """Calculate sample size required to estimate win rate within margin of error."""
    z = 1.95996
    return math.ceil((z**2 * p * (1.0 - p)) / (margin_of_error**2))

total_trades = len(trades_df)
wins = trades_df[trades_df["net_pnl"] > 0]
losses = trades_df[trades_df["net_pnl"] <= 0]
n_wins = len(wins)
n_losses = len(losses)

p_hat, ci_lower, ci_upper = wilson_score_interval(n_wins, total_trades)

# Regime breakdown
trades_df["market_regime"] = trades_df["signal_reason"].apply(
    lambda x: "TRENDING_BEARISH" if "SHORT" in str(x) else "TRENDING_BULLISH"
)

# Regime metrics
regime_stats = {}
for reg, grp in trades_df.groupby("market_regime"):
    reg_wins = grp[grp["net_pnl"] > 0]
    reg_losses = grp[grp["net_pnl"] <= 0]
    reg_wr = (len(reg_wins) / len(grp)) * 100.0 if len(grp) > 0 else 0.0
    reg_gross = grp["gross_pnl"].sum()
    reg_net = grp["net_pnl"].sum()
    reg_pf = (reg_wins["net_pnl"].sum() / abs(reg_losses["net_pnl"].sum())) if len(reg_losses) > 0 else 0.0
    regime_stats[reg] = {
        "trades": len(grp),
        "wins": len(reg_wins),
        "losses": len(reg_losses),
        "win_rate": reg_wr,
        "gross_pnl": reg_gross,
        "net_pnl": reg_net,
        "profit_factor": reg_pf,
    }

print("=== STATISTICAL VALIDATION SUMMARY ===")
print(f"Total Forward Trades: {total_trades} (Span: 2026-10-21 to 2026-11-04)")
print(f"Observed Win Rate: {p_hat*100:.2f}% ({n_wins}W / {n_losses}L)")
print(f"95% Wilson Score Confidence Interval: [{ci_lower*100:.1f}%, {ci_upper*100:.1f}%]")
print(f"Interval Width: {(ci_upper - ci_lower)*100:.1f}% (Excessively wide due to N=7)")
print(f"Sample Size for +/-5% error at 95% CI (p=0.35): {required_sample_size(0.35, 0.05)} trades")
print(f"Sample Size for +/-10% error at 95% CI (p=0.35): {required_sample_size(0.35, 0.10)} trades")
print(f"Sample Size for +/-15% error at 95% CI (p=0.35): {required_sample_size(0.35, 0.15)} trades")
print("\n=== REGIME ATTRIBUTION ===")
for reg, stats in regime_stats.items():
    print(f"Regime: {reg} | Trades: {stats['trades']} | WR: {stats['win_rate']:.1f}% | Gross: ${stats['gross_pnl']:+.4f} | Net: ${stats['net_pnl']:+.4f} | PF: {stats['profit_factor']:.2f}")

