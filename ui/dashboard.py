"""Dashboard UI: Portfolio health, equity, cash, open positions, and market status."""

import streamlit as st
import pandas as pd
from typing import Optional
from database.repository import DatabaseRepository
from services.portfolio_service import PortfolioService, PortfolioSummary
from market_data.base import BaseMarketDataProvider


from config.orb_settings import settings as orb_settings
from services.delta_account_service import DeltaAccountService
from services.delta_paper_broker import DeltaPaperBroker


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

    # Section 1: XAUTUSD Paper Trading Simulated Account
    with st.container(border=True):
        st.markdown("### 🥇 XAUTUSD Simulated Paper Account (Delta Exchange India)")
        st.caption("All orders and fills are simulated locally with exact spread (0.02%), slippage (0.02%), and fees (0.01%). Real exchange balance is isolated.")

        delta_paper = DeltaPaperBroker()
        d_summary = delta_paper.get_account_summary()

        dp1, dp2, dp3, dp4, dp5 = st.columns(5)
        dp1.metric("Simulated Equity", f"${d_summary['equity']:,.2f}", delta=f"{d_summary['realized_pnl']:+,.2f} P&L")
        dp2.metric("Available Cash", f"${d_summary['cash_balance']:,.2f}")
        dp3.metric("Win Rate", f"{d_summary['win_rate_pct']:.1f}%", f"{d_summary['winning_trades']}W / {d_summary['losing_trades']}L")
        dp4.metric("Profit Factor", f"{d_summary['profit_factor']:.2f}")
        dp5.metric("Max Drawdown", f"{d_summary['max_drawdown_pct']:.2f}%", delta=f"-${d_summary['max_drawdown_cash']:,.2f}", delta_color="inverse")

        if d_summary["has_open_position"] and d_summary["open_position"]:
            pos = d_summary["open_position"]
            st.info(
                f"**Active Simulated Position**: {pos['direction']} {pos['lots']} lots ({pos['contract_qty']} XAUT) @ ${pos['entry_price']:.2f} | "
                f"Market: ${pos.get('market_price', pos['entry_price']):.2f} | "
                f"Unrealized P&L: **{pos.get('unrealized_pnl', 0.0):+,.2f} USD** | "
                f"SL: ${pos['stop_loss']:.2f} | TP: ${pos['take_profit']:.2f}"
            )
        else:
            st.caption("No open simulated positions. Awaiting Strategy Intelligence V2 signal confirmation.")

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
