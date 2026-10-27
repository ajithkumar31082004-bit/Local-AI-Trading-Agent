"""Paper Trading UI: Interactive simulated order execution with mandatory confirmation and risk audits."""

import streamlit as st
from typing import Optional
from database.repository import DatabaseRepository
from services.risk_manager import RiskManager, RiskCheckResult
from services.paper_broker import PaperBroker
from market_data.base import BaseMarketDataProvider


def render_paper_trading(repo: DatabaseRepository, data_provider: BaseMarketDataProvider):
    st.subheader("Paper Trading Execution Desk")
    st.caption("Simulate trade executions using virtual cash. Real-money orders are strictly disabled.")

    broker = PaperBroker(repo=repo)
    risk_manager = RiskManager()
    account = repo.get_account_state()
    positions = repo.get_positions()

    cash = account["cash_balance"]
    equity = cash + sum(p.market_value for p in positions)

    # Top Balance bar
    c1, c2, c3 = st.columns(3)
    c1.metric("Available Cash", f"${cash:,.2f}")
    c2.metric("Portfolio Equity", f"${equity:,.2f}")
    c3.metric("Open Positions", f"{len(positions)}")

    st.markdown("---")

    # Order Entry Form
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown("### Order Entry")
        symbol = st.text_input("Symbol", value="AAPL", key="pt_sym").strip().upper()
        side = st.selectbox("Order Side", options=["BUY", "SELL"], key="pt_side")

        # Fetch latest price
        with st.spinner("Fetching quote..."):
            quote_price = data_provider.get_latest_price(symbol)

        if not quote_price or quote_price <= 0:
            st.error(f"Could not retrieve price quote for '{symbol}'.")
            return

        st.info(f"Market Quote: **${quote_price:,.2f}**")

        existing_pos = repo.get_position(symbol)
        existing_qty = existing_pos.quantity if existing_pos else 0.0
        if existing_pos:
            st.caption(f"Current open position: {existing_qty:.4f} shares (Entry: ${existing_pos.avg_entry_price:,.2f})")

        quantity = st.number_input("Shares Quantity", min_value=1.0, value=10.0, step=1.0, key="pt_qty")
        strategy_tag = st.selectbox(
            "Strategy Tag",
            ["MANUAL", "Moving Average Crossover", "RSI Confirmation", "Trend Following", "Custom"],
            key="pt_strat",
        )

        with st.expander("Risk Orders (Stop Loss / Take Profit)"):
            use_custom_sl = st.checkbox("Set Custom Stop Loss", value=False)
            custom_sl = st.number_input("Stop Loss ($)", value=round(quote_price * 0.97, 2)) if use_custom_sl else None
            use_custom_tp = st.checkbox("Set Custom Take Profit", value=False)
            custom_tp = st.number_input("Take Profit ($)", value=round(quote_price * 1.05, 2)) if use_custom_tp else None

    # Pre-Trade Risk Audit & Preview
    with col_right:
        st.markdown("### Risk Audit & Trade Preview")

        risk_audit: RiskCheckResult = risk_manager.evaluate_order(
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            current_price=quote_price,
            portfolio_equity=equity,
            cash_balance=cash,
            current_open_positions_count=len(positions),
            today_realized_loss=repo.get_today_realized_loss(),
            stop_loss=custom_sl,
            take_profit=custom_tp,
            existing_position_qty=existing_qty,
        )

        with st.container(border=True):
            if risk_audit.approved:
                st.success("Risk Audit: **APPROVED**")
            else:
                st.error("Risk Audit: **REJECTED**")
                for r in risk_audit.rejection_reasons:
                    st.write(f"- :red[{r}]")

            st.write(f"**Effective Price (incl. slippage):** ${risk_audit.effective_price:,.2f}")
            st.write(f"**Estimated Trade Value:** ${risk_audit.position_value:,.2f}")
            st.write(f"**Estimated Broker Fee:** ${risk_audit.estimated_fee:,.2f}")
            st.write(f"**Maximum Allowed Size:** {risk_audit.max_allowed_quantity:.2f} shares")
            if side == "BUY":
                st.write(f"**Capital at Risk (to SL):** ${risk_audit.dollar_risk:,.2f}")
                st.write(f"**Risk/Reward Ratio:** {risk_audit.risk_reward_ratio:.2f}")

            for w in risk_audit.warning_notes:
                st.caption(f":orange[Notice: {w}]")

        # Explicit User Confirmation Gate
        st.markdown("#### Execution Confirmation")
        confirm_checkbox = st.checkbox(
            f"I confirm execution of paper order: {side} {quantity} {symbol} @ ~${risk_audit.effective_price:,.2f}",
            value=False,
            key="confirm_order_chk",
        )

        execute_button = st.button(
            "Execute Paper Trade",
            type="primary",
            disabled=not (risk_audit.approved and confirm_checkbox),
            use_container_width=True,
        )

        if execute_button:
            ok, msg, trade = broker.execute_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                current_price=quote_price,
                strategy_name=strategy_tag,
                stop_loss=risk_audit.stop_loss if side == "BUY" else None,
                take_profit=risk_audit.take_profit if side == "BUY" else None,
                notes=f"Order executed via Paper Trading desk. {strategy_tag}",
            )
            if ok:
                st.success(f"Trade Executed: {msg}")
                st.rerun()
            else:
                st.error(f"Execution Failed: {msg}")
