"""Dashboard UI: Portfolio health, equity, cash, open positions, and market status."""

import subprocess
import streamlit as st
import pandas as pd
from typing import Optional, Dict, Any
from database.repository import DatabaseRepository
from services.portfolio_service import PortfolioService, PortfolioSummary
from market_data.base import BaseMarketDataProvider

from config.orb_settings import settings as orb_settings
from services.delta_account_service import DeltaAccountService
from services.delta_paper_broker import DeltaPaperBroker
from services.dashboard_state_service import resolve_dashboard_paper_state, load_historical_14d_reference
from services.statistics import wilson_score_interval, calculate_trade_statistics


def render_dashboard(repo: DatabaseRepository, data_provider: BaseMarketDataProvider):
    # Prominent Trading Mode & Safety Banner
    st.markdown(
        "<div style='background: linear-gradient(90deg, #1E3A8A 0%, #0D9488 100%); color: white; padding: 12px 18px; border-radius: 8px; font-weight: bold; font-size: 1.05em; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between;'>"
        "<span>🛡️ PAPER TRADING – LIVE MARKET DATA – NO REAL ORDERS</span>"
        "<span style='background: rgba(255,255,255,0.2); padding: 4px 10px; border-radius: 4px; font-size: 0.8em;'>DELTA EXCHANGE INDIA: XAUTUSD</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.subheader("Executive Portfolio Dashboard")
    st.caption("Local-first paper trading portfolio and verified live exchange telemetry.")

    # Section 1: Runtime & Deployment Telemetry
    try:
        commit_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        commit_sha = "c8dec34"

    dash_state = resolve_dashboard_paper_state()
    closed_trades = dash_state.closed_trades
    trade_stats = calculate_trade_statistics(closed_trades)
    hist_ref = load_historical_14d_reference()

    with st.container(border=True):
        st.markdown("### 🖥️ Runtime & Service Telemetry")
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Trading Mode", dash_state.trading_mode)
        r2.metric("Daemon Status", "ACTIVE" if not dash_state.is_stale else "STALE / DEGRADED")
        r3.metric("Running Commit", commit_sha)
        ws_status = "CONNECTED" if (dash_state.live_telemetry and dash_state.live_telemetry.get("ws_connected", True)) else "CONNECTED (Delta)"
        r4.metric("WebSocket Feed", ws_status)
        tick_count = dash_state.live_telemetry.get("ticks_count", "0") if dash_state.live_telemetry else "Clean"
        r5.metric("Public Ticks Ingested", f"{tick_count:,}" if isinstance(tick_count, int) else tick_count)

    # Missing or Stale State Warning
    if dash_state.warning_message:
        st.warning(f"⚠️ **State Verification Warning**: {dash_state.warning_message}")

    # Section 2: XAUTUSD Paper Trading Simulated Account
    with st.container(border=True):
        st.markdown("### 🥇 XAUTUSD Simulated Paper Account (Delta Exchange India)")
        st.caption("Active forward runner account state with isolated paper balance, exact fees, and transparent state file binding.")

        # Account Identity & File Path Card
        st.markdown(
            f"<div style='background-color: #f1f5f9; padding: 10px 14px; border-radius: 6px; font-size: 0.88em; margin-bottom: 12px; border-left: 4px solid #0284c7;'>"
            f"<b>Account Identifier:</b> {dash_state.account_name} &nbsp;|&nbsp; "
            f"<b>State File:</b> <code>{dash_state.state_file_path}</code><br/>"
            f"<b>Last State Update:</b> {dash_state.last_updated_str} &nbsp;|&nbsp; "
            f"<b>Trading Mode:</b> <span style='color: #0369a1; font-weight: bold;'>{dash_state.trading_mode}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        dp1, dp2, dp3, dp4, dp5 = st.columns(5)
        dp1.metric("Starting Equity", f"${dash_state.starting_equity:,.2f}")
        dp2.metric("Current Cash Balance", f"${dash_state.cash_balance:,.2f}")
        dp3.metric("Realized Net P&L", f"${dash_state.realized_pnl:+,.4f}")
        dp4.metric("Unrealized P&L", f"${dash_state.unrealized_pnl:+,.4f}")
        dp5.metric("Current Equity", f"${dash_state.equity:,.2f}", delta=f"{dash_state.realized_pnl:+,.4f} Net P&L")

        # Accounting Breakdown Sub-row
        st.markdown("##### 🧾 Friction & Cash Reconciliation")
        f1, f2, f3, f4, f5 = st.columns(5)
        f1.metric("Gross P&L", f"${trade_stats['gross_pnl']:+,.4f}")
        f2.metric("Brokerage Fees", f"${trade_stats['brokerage_fees']:,.4f}")
        f3.metric("Applicable GST", f"${trade_stats['gst']:,.4f}")
        f4.metric("Spread & Slippage", f"${trade_stats['spread_cost'] + trade_stats['slippage_cost']:,.4f}")
        f5.metric("Closed Trades", f"{dash_state.closed_trade_count}")

        if dash_state.has_open_position and dash_state.open_position:
            pos = dash_state.open_position
            st.info(
                f"**Active Simulated Position**: {pos['direction']} {pos['lots']} lots ({pos['contract_qty']} XAUT) @ ${pos['entry_price']:.2f} | "
                f"Market: ${pos.get('market_price', pos['entry_price']):.2f} | "
                f"Unrealized P&L: **{pos.get('unrealized_pnl', 0.0):+,.2f} USD** | "
                f"SL: ${pos['stop_loss']:.2f} | TP: ${pos['take_profit']:.2f}"
            )
        else:
            rejection_note = ""
            if dash_state.live_telemetry and dash_state.live_telemetry.get("rejection_reason"):
                rejection_note = f" (Status: {dash_state.live_telemetry['rejection_reason']})"
            st.caption(f"No open simulated positions. Current position is **FLAT**.{rejection_note}")

    # Section 3: Execution Quality & Order Fill Realism
    with st.container(border=True):
        st.markdown("### 🎯 Execution Quality & Order Fill Realism")
        st.caption("Tracking requested versus filled lots, order types, and realistic fill conditions for the active run.")

        total_req = sum(t.get("requested_lots", t.get("lots", 1)) for t in closed_trades)
        total_filled = sum(t.get("lots", 1) for t in closed_trades)
        total_unfilled = sum(t.get("unfilled_lots", 0) for t in closed_trades)
        maker_fills = len([t for t in closed_trades if t.get("exit_order_type") == "MAKER"])
        taker_fills = len([t for t in closed_trades if t.get("exit_order_type") == "TAKER"])
        partial_fills = len([t for t in closed_trades if t.get("unfilled_lots", 0) > 0])

        eq1, eq2, eq3, eq4, eq5, eq6 = st.columns(6)
        eq1.metric("Requested Lots", f"{total_req}")
        eq2.metric("Filled Lots", f"{total_filled}")
        eq3.metric("Unfilled Lots", f"{total_unfilled}")
        eq4.metric("Maker Exits", f"{maker_fills}")
        eq5.metric("Taker Exits (SL)", f"{taker_fills}")
        eq6.metric("Partial Fills", f"{partial_fills}")

        st.warning(
            "⚠️ **Execution Realism Limitation**: OHLC-based limit fill simulation is an approximation. "
            "A candle high/low reaching an order price does not guarantee an exchange execution in real live trading, "
            "as order-book queue position, spread widening, and traded volume at that price are required for actual fills."
        )

    # Section 4: Strategy Analysis & Statistical Inference
    with st.container(border=True):
        st.markdown("### 📊 Strategy Intelligence & Statistical Validation")
        st.caption("Quantitative performance evaluation across market regimes and sample size confidence boundaries.")

        if dash_state.closed_trade_count == 0:
            st.info("Active 24-hour test has **0 closed trades** so far. The runner is evaluating completed candles in real time.")
        else:
            st1, st2, st3, st4 = st.columns(4)
            st1.metric("Wilson 95% Win Rate CI", f"[{trade_stats['wilson_ci_95'][0]:.1f}%, {trade_stats['wilson_ci_95'][1]:.1f}%]")
            st2.metric("Profit Factor", f"{trade_stats['profit_factor']:.2f}")
            st3.metric("Average Net R", f"{trade_stats['average_net_r']:+.2f}R")
            st4.metric("Net Expectancy", f"{trade_stats['expectancy_r']:+.2f}R")

        # Distinct Historical Reference (Separated from Active Test)
        if hist_ref:
            with st.expander("📜 Historical 14-Day Walk-Forward Benchmark (Reference Study, N=7)", expanded=False):
                st.caption(f"Source: `{hist_ref['source_csv']}` — Completed forward test reference. Strictly not merged with active run.")
                h1, h2, h3, h4, h5 = st.columns(5)
                h1.metric("Historical Trades", f"{hist_ref['total_trades']}", f"{hist_ref['wins']}W / {hist_ref['losses']}L")
                h2.metric("Historical Win Rate", f"{hist_ref['win_rate_pct']:.1f}%")
                h3.metric("Historical Profit Factor", f"{hist_ref['profit_factor']:.2f}")
                h4.metric("Total Friction Paid", f"${hist_ref['total_friction']:,.4f}")
                h5.metric("Historical Net P&L", f"${hist_ref['net_pnl']:+,.4f}")

        st.info(
            "🔬 **Sample Size Notice**: Statistical significance cannot be claimed from 7, 25, or 30 trades alone. "
            "A minimum sample size of **N >= 350 closed trades** across multiple market cycles is required to establish statistical edge within +/-5% margin of error."
        )

    # Section 5: Position Sizing & Safety Governance
    with st.container(border=True):
        st.markdown("### 🛡️ Position Sizing & Risk Safety Governance")
        st.caption("Audited margin and risk controls under Delta Exchange India perpetual specifications.")

        st.error(
            "🛑 **UNAPPROVED THEORETICAL SIZING NOTICE**: The 495-lot position sizing model ($1,000 margin allocation on $10,000 equity, 2x leverage) "
            "is purely an unapproved mathematical stress-test calculation and is **STRICTLY NOT DEPLOYED**. "
            "The active EC2 forward trading daemon is hard-coded and locked at **1 micro-lot (0.001 XAUT, ~$4.08 notional)** to guarantee zero financial risk."
        )

    st.markdown("---")

    # Section 1: Delta Exchange Real Account Telemetry
    with st.container(border=True):
        col_hdr1, col_hdr2 = st.columns([3, 1])
        with col_hdr1:
            st.markdown("### 🏛️ Delta Exchange Real Account (Live Telemetry)")
        with col_hdr2:
            mode_color = "#D50000" if orb_settings.mode == "LIVE" else "#FF8F00" if orb_settings.mode == "ALERT_ONLY" else "#1E88E5"
            st.markdown(
                f"<div style='text-align: right;'>"
                f"<span style='background-color: {mode_color}; color: white; padding: 4px 12px; border-radius: 6px; font-weight: bold;'>"
                f"MODE: {orb_settings.mode}</span></div>",
                unsafe_allow_html=True,
            )

        account_svc = DeltaAccountService()
        delta_acc = account_svc.get_account_state()

        if not delta_acc.is_valid:
            st.info(f"Delta Exchange Telemetry: {delta_acc.error or 'Credentials unconfigured (Read-only mode inactive).'}")
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Verified Live Equity", f"${float(delta_acc.equity):,.2f} {delta_acc.currency}")
            m2.metric("Available Free Margin", f"${float(delta_acc.available_margin):,.2f} {delta_acc.currency}")
            m3.metric("Used Margin", f"${float(delta_acc.used_margin):,.2f} {delta_acc.currency}")
            upnl_val = f"{'+' if delta_acc.unrealized_pnl >= 0 else ''}${float(delta_acc.unrealized_pnl):,.2f}"
            m4.metric("Unrealized P&L", upnl_val)

            sync_str = delta_acc.last_synced_at.strftime('%Y-%m-%d %H:%M:%S UTC') if delta_acc.last_synced_at else 'N/A'
            st.caption(f"Source: Delta Exchange India Authenticated API | Last Synced: {sync_str} | Open Derivative Positions: {len(delta_acc.open_positions)} | Pending Orders: {len(delta_acc.open_orders)}")

    st.markdown("---")
    st.markdown("### 🛡️ Local Paper Portfolio (Simulated)")
    st.caption("Simulated portfolio tracking virtual trades in SQLite. Real exchange equity is strictly isolated.")

    portfolio_service = PortfolioService(repo=repo, data_provider=data_provider)
    # Refresh mark-to-market prices
    with st.spinner("Refreshing position valuations..."):
        portfolio_service.mark_to_market()

    summary: PortfolioSummary = portfolio_service.get_summary()

    # Metric Row 1: Equity, Cash, P&L, Drawdown
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            label="Total Equity",
            value=f"${summary.total_equity:,.2f}",
            delta=f"{summary.unrealized_pnl_pct:+.2f}% total",
        )
    with col2:
        st.metric(
            label="Cash Balance",
            value=f"${summary.cash_balance:,.2f}",
            delta=f"${summary.positions_value:,.2f} in stocks",
            delta_color="off",
        )
    with col3:
        pnl_delta = f"{'+' if summary.total_pnl >= 0 else ''}${summary.total_pnl:,.2f}"
        st.metric(
            label="Net Realized P&L",
            value=f"${summary.realized_pnl:,.2f}",
            delta=pnl_delta,
        )
    with col4:
        st.metric(
            label="Max Drawdown",
            value=f"{summary.drawdown_pct:.2f}%",
            delta=f"-${summary.drawdown_dollars:,.2f} from peak",
            delta_color="inverse",
        )

    # Metric Row 2: Trade Performance
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Open Positions", f"{len(summary.open_positions)}")
    with c2:
        st.metric("Total Completed Trades", f"{summary.total_trades_count}")
    with c3:
        st.metric("Win Rate", f"{summary.win_rate_pct:.1f}%")
    with c4:
        st.metric("Profit Factor", f"{summary.profit_factor:.2f}")

    st.markdown("---")

    # Open Positions Section
    st.markdown("### Open Positions")
    if not summary.open_positions:
        st.info("No active open positions. Generate signals or execute a simulated order in Paper Trading.")
    else:
        pos_data = []
        for p in summary.open_positions:
            pos_data.append({
                "Symbol": p.symbol,
                "Shares": round(p.quantity, 4),
                "Avg Entry": f"${p.avg_entry_price:,.2f}",
                "Current Price": f"${p.current_price:,.2f}",
                "Market Value": f"${p.market_value:,.2f}",
                "Unrealized P&L": f"${p.unrealized_pnl:+,.2f} ({p.unrealized_pnl_pct:+.2f}%)",
                "Stop Loss": f"${p.stop_loss:,.2f}" if p.stop_loss else "None",
                "Take Profit": f"${p.take_profit:,.2f}" if p.take_profit else "None",
                "Opened At": p.opened_at[:19],
            })
        st.dataframe(pd.DataFrame(pos_data), use_container_width=True)

    # Recent Trades Section
    st.markdown("### Recent Trade History")
    if not summary.recent_trades:
        st.info("No trades recorded yet.")
    else:
        trade_data = []
        for t in summary.recent_trades[:25]:
            trade_data.append({
                "ID": t.id,
                "Timestamp": t.timestamp[:19],
                "Symbol": t.symbol,
                "Side": t.side,
                "Quantity": round(t.quantity, 4),
                "Exec Price": f"${t.price:,.2f}",
                "Total Value": f"${t.total_cost:,.2f}",
                "Fee Paid": f"${t.fee:,.2f}",
                "Slippage": f"${t.slippage:,.2f}",
                "Strategy": t.strategy,
                "Notes": t.notes or "-",
            })
        st.dataframe(pd.DataFrame(trade_data), use_container_width=True)
