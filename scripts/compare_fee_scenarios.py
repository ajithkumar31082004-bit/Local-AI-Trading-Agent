"""Run comparative performance analysis across execution cost scenarios on raw 14-day trades."""

import pandas as pd
import numpy as np

trades_df = pd.read_csv("data/forward_test_14d_trades.csv")

scenarios = [
    {
        "name": "Scenario 1: Maker Entry + Maker Exit (0.01% / 0.01%) [Base]",
        "entry_fee_pct": 0.01,
        "exit_fee_pct": 0.01,
        "apply_gst": False,
    },
    {
        "name": "Scenario 1+GST: Maker/Maker + 18% GST (0.01% / 0.01% + GST)",
        "entry_fee_pct": 0.01,
        "exit_fee_pct": 0.01,
        "apply_gst": True,
    },
    {
        "name": "Scenario 2: Maker Entry + Taker Exit (0.01% / 0.05%)",
        "entry_fee_pct": 0.01,
        "exit_fee_pct": 0.05,
        "apply_gst": False,
    },
    {
        "name": "Scenario 2+GST: Maker/Taker + 18% GST (0.01% / 0.05% + GST)",
        "entry_fee_pct": 0.01,
        "exit_fee_pct": 0.05,
        "apply_gst": True,
    },
    {
        "name": "Scenario 3: Taker Entry + Taker Exit (0.05% / 0.05%) [Aggressive]",
        "entry_fee_pct": 0.05,
        "exit_fee_pct": 0.05,
        "apply_gst": False,
    },
    {
        "name": "Scenario 3+GST: Taker/Taker + 18% GST (0.05% / 0.05% + GST) [Max Friction]",
        "entry_fee_pct": 0.05,
        "exit_fee_pct": 0.05,
        "apply_gst": True,
    },
]

results = []

for sc in scenarios:
    name = sc["name"]
    entry_rate = sc["entry_fee_pct"] / 100.0
    exit_rate = sc["exit_fee_pct"] / 100.0
    apply_gst = sc["apply_gst"]

    scenario_trades = []
    running_eq = 10000.0000
    peak_eq = 10000.0000
    max_dd_cash = 0.0

    for _, r in trades_df.iterrows():
        contract_qty = r["contract_qty"]
        entry_price = r["entry_price"]
        exit_price = r["exit_price"]
        signal_entry = r.get("signal_entry_price", r["entry_price"])
        gross_pnl = r["gross_pnl"]
        spread_cost = r["spread_cost"]
        slippage_cost = r["slippage_cost"]
        price_impact = spread_cost + slippage_cost
        planned_risk = r["trade_risk_cash"]

        entry_notional = entry_price * contract_qty
        exit_notional = exit_price * contract_qty

        entry_fee = round(entry_notional * entry_rate, 4)
        exit_fee = round(exit_notional * exit_rate, 4)
        total_fees = round(entry_fee + exit_fee, 4)

        gst = round(total_fees * 0.18, 4)
        deducted_gst = gst if apply_gst else 0.0

        total_friction = round(total_fees + deducted_gst + price_impact, 4)
        net_pnl = round(gross_pnl - total_friction, 4)

        net_r = round(net_pnl / planned_risk, 2) if planned_risk > 0 else 0.0
        gross_r = round(gross_pnl / planned_risk, 2) if planned_risk > 0 else 0.0

        running_eq = round(running_eq + net_pnl, 4)
        if running_eq > peak_eq:
            peak_eq = running_eq
        dd = round(peak_eq - running_eq, 4)
        if dd > max_dd_cash:
            max_dd_cash = dd

        scenario_trades.append({
            "gross_pnl": gross_pnl,
            "fees": total_fees,
            "gst": gst,
            "deducted_gst": deducted_gst,
            "spread_cost": spread_cost,
            "slippage_cost": slippage_cost,
            "total_friction": total_friction,
            "net_pnl": net_pnl,
            "planned_risk": planned_risk,
            "gross_r": gross_r,
            "net_r": net_r,
        })

    sc_df = pd.DataFrame(scenario_trades)
    total_trades = len(sc_df)
    wins = sc_df[sc_df["net_pnl"] > 0]
    losses = sc_df[sc_df["net_pnl"] <= 0]
    win_rate = (len(wins) / total_trades) * 100.0

    gross_profit = sc_df[sc_df["gross_pnl"] > 0]["gross_pnl"].sum()
    gross_loss = abs(sc_df[sc_df["gross_pnl"] <= 0]["gross_pnl"].sum())
    gross_pf = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

    net_profit = wins["net_pnl"].sum()
    net_loss = abs(losses["net_pnl"].sum())
    net_pf = (net_profit / net_loss) if net_loss > 0 else 0.0

    avg_win_r = wins["net_r"].mean() if len(wins) > 0 else 0.0
    avg_loss_r = losses["net_r"].mean() if len(losses) > 0 else 0.0
    avg_realized_r = sc_df["net_r"].mean()
    net_expectancy = ((len(wins) / total_trades) * avg_win_r) + ((len(losses) / total_trades) * avg_loss_r)

    total_gross_pnl = sc_df["gross_pnl"].sum()
    total_fees_sum = sc_df["fees"].sum()
    total_gst_sum = sc_df["gst"].sum()
    total_deducted_gst = sc_df["deducted_gst"].sum()
    total_spread_sum = sc_df["spread_cost"].sum()
    total_slip_sum = sc_df["slippage_cost"].sum()
    total_friction_sum = sc_df["total_friction"].sum()
    total_net_pnl = sc_df["net_pnl"].sum()
    final_eq = running_eq
    max_dd_pct = (max_dd_cash / peak_eq) * 100.0

    results.append({
        "scenario": name,
        "entry_fee_pct": sc["entry_fee_pct"],
        "exit_fee_pct": sc["exit_fee_pct"],
        "apply_gst": apply_gst,
        "total_fees": round(total_fees_sum, 4),
        "total_gst": round(total_gst_sum, 4),
        "deducted_gst": round(total_deducted_gst, 4),
        "total_spread": round(total_spread_sum, 4),
        "total_slip": round(total_slip_sum, 4),
        "total_friction": round(total_friction_sum, 4),
        "gross_pnl": round(total_gross_pnl, 4),
        "net_pnl": round(total_net_pnl, 4),
        "final_equity": round(final_eq, 4),
        "profit_factor": round(net_pf, 2),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
        "avg_r": round(avg_realized_r, 2),
        "expectancy_r": round(net_expectancy, 4),
        "max_dd_cash": round(max_dd_cash, 4),
        "max_dd_pct": round(max_dd_pct, 6),
        "win_rate": round(win_rate, 2),
    })

print(f"{'Scenario':<45} | {'Fees':<7} | {'GST':<6} | {'Slip/Spr':<8} | {'Frict':<7} | {'Net P&L':<8} | {'PF':<5} | {'Avg R':<6} | {'Max DD ($)':<9}")
print("-" * 120)
for res in results:
    print(f"{res['scenario']:<45} | ${res['total_fees']:<6.4f} | ${res['total_gst']:<5.4f} | ${res['total_spread']+res['total_slip']:<7.4f} | ${res['total_friction']:<6.4f} | ${res['net_pnl']:<7.4f} | {res['profit_factor']:<5.2f} | {res['avg_r']:<+5.2f}R | ${res['max_dd_cash']:<8.4f}")
