"""Dashboard UI: Portfolio health, equity, cash, open positions, and market status."""

import streamlit as st
import pandas as pd
from typing import Optional
from database.repository import DatabaseRepository
from services.portfolio_service import PortfolioService, PortfolioSummary
from market_data.base import BaseMarketDataProvider


def render_dashboard(repo: DatabaseRepository, data_provider: BaseMarketDataProvider):
    st.subheader("Executive Portfolio Dashboard")
    st.caption("Local-first paper trading portfolio and live market data telemetry.")

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
