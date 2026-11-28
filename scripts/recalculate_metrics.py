"""Independently recalculate all strategy performance metrics directly from raw trade CSV."""

import pandas as pd
import json

df = pd.read_csv("data/forward_test_14d_trades.csv")

print(f"Total Trades: {len(df)}")
for idx, r in df.iterrows():
    gross_r = r["gross_pnl"] / r["trade_risk_cash"]
    net_r = r["net_pnl"] / r["trade_risk_cash"]
    print(f"Trade {idx+1}: {r['trade_id']} | {r['direction']} | Fill: {r['entry_price']} -> {r['exit_price']} | "
          f"Gross P&L: ${r['gross_pnl']:+.4f} | Friction: ${r['total_friction']:.4f} (Fees: ${r['fees']:.4f}, Spread: ${r['spread_cost']:.4f}, Slip: ${r['slippage_cost']:.4f}) | "
          f"Net P&L: ${r['net_pnl']:+.4f} | Planned Risk: ${r['trade_risk_cash']:.4f} | "
          f"Gross R: {gross_r:+.2f}R | Net Trade R: {r['trade_r_multiple']:+.2f}R | Port R: {r['portfolio_r_multiple']:+.4f}R | Exit: {r['exit_reason']}")

total_trades = len(df)
winning_trades = df[df["net_pnl"] > 0]
losing_trades = df[df["net_pnl"] <= 0]

n_wins = len(winning_trades)
n_losses = len(losing_trades)
win_rate = (n_wins / total_trades) * 100.0

gross_profit = winning_trades["gross_pnl"].sum()
gross_loss = abs(losing_trades["gross_pnl"].sum())
gross_pnl = df["gross_pnl"].sum()

net_profit = winning_trades["net_pnl"].sum()
net_loss = abs(losing_trades["net_pnl"].sum())
net_pnl = df["net_pnl"].sum()

profit_factor_gross = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
profit_factor_net = (net_profit / net_loss) if net_loss > 0 else 0.0

total_fees = df["fees"].sum()
total_spread = df["spread_cost"].sum()
total_slip = df["slippage_cost"].sum()
total_friction = df["total_friction"].sum()

friction_pct_gross_profit = (total_friction / gross_profit) * 100.0 if gross_profit > 0 else 0.0

# Gross R-multiples
df["gross_trade_r"] = df["gross_pnl"] / df["trade_risk_cash"]
avg_winning_gross_r = df[df["gross_pnl"] > 0]["gross_trade_r"].mean()
avg_losing_gross_r = df[df["gross_pnl"] <= 0]["gross_trade_r"].mean()
avg_gross_r = df["gross_trade_r"].mean()
gross_expectancy_r = ((win_rate / 100.0) * avg_winning_gross_r) + (((100.0 - win_rate) / 100.0) * avg_losing_gross_r)

# Net R-multiples
avg_winning_net_r = winning_trades["trade_r_multiple"].mean()
avg_losing_net_r = losing_trades["trade_r_multiple"].mean()
avg_realized_net_r = df["trade_r_multiple"].mean()
net_expectancy_r = ((win_rate / 100.0) * avg_winning_net_r) + (((100.0 - win_rate) / 100.0) * avg_losing_net_r)

# Drawdown calculation
initial_equity = 10000.0000
running_equity = initial_equity
equity_curve = [initial_equity]
peak_equity = initial_equity
max_dd_cash = 0.0
max_dd_pct = 0.0

for pnl in df["net_pnl"]:
    running_equity = round(running_equity + pnl, 4)
    equity_curve.append(running_equity)
    if running_equity > peak_equity:
        peak_equity = running_equity
    dd_cash = round(peak_equity - running_equity, 4)
    dd_pct = (dd_cash / peak_equity) * 100.0
    if dd_cash > max_dd_cash:
        max_dd_cash = dd_cash
    if dd_pct > max_dd_pct:
        max_dd_pct = dd_pct

print("\n--- SUMMARY METRICS ---")
print(f"Win Rate: {win_rate:.2f}% ({n_wins}W / {n_losses}L)")
print(f"Gross Profit: ${gross_profit:+.4f}")
print(f"Gross Loss:   ${gross_loss:+.4f}")
print(f"Gross Realized P&L: ${gross_pnl:+.4f}")
print(f"Net Profit:   ${net_profit:+.4f}")
print(f"Net Loss:     ${net_loss:+.4f}")
print(f"Net Realized P&L:   ${net_pnl:+.4f}")
print(f"Profit Factor (Gross): {profit_factor_gross:.2f}")
print(f"Profit Factor (Net):   {profit_factor_net:.2f}")
print(f"Total Fees:     ${total_fees:.4f}")
print(f"Total Spread:   ${total_spread:.4f}")
print(f"Total Slippage: ${total_slip:.4f}")
print(f"Total Friction: ${total_friction:.4f}")
print(f"Friction % of Gross Profit: {friction_pct_gross_profit:.2f}%")
print(f"Average Winning R (Gross): {avg_winning_gross_r:+.2f}R")
print(f"Average Losing R (Gross):  {avg_losing_gross_r:+.2f}R")
print(f"Average Realized R (Gross): {avg_gross_r:+.2f}R")
print(f"Gross Expectancy: {gross_expectancy_r:+.4f}R")
print(f"Average Winning R (Net): {avg_winning_net_r:+.2f}R")
print(f"Average Losing R (Net):  {avg_losing_net_r:+.2f}R")
print(f"Average Realized R (Net): {avg_realized_net_r:+.2f}R")
print(f"Net Expectancy: {net_expectancy_r:+.4f}R")
print(f"Peak Equity: ${peak_equity:.4f}")
print(f"Final Equity: ${running_equity:.4f}")
print(f"Max Drawdown ($): ${max_dd_cash:.4f}")
print(f"Max Drawdown (%): {max_dd_pct:.6f}%")

