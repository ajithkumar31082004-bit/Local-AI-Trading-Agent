"""Risk Settings UI: Parameter adjustment and paper account reset."""

import streamlit as st
from config.settings import settings
from database.repository import DatabaseRepository


def render_risk_settings(repo: DatabaseRepository):
    st.subheader("Risk Management Controls & Settings")
    st.caption("Configure hard capital preservation constraints and manage paper trading balances.")

    with st.container(border=True):
        st.markdown("### Risk Limits Configuration")
        c1, c2 = st.columns(2)
        with c1:
            risk_pct = st.number_input(
                "Max Risk Per Trade (%)",
                min_value=0.5,
                max_value=10.0,
                value=float(settings.max_risk_per_trade_pct),
                step=0.5,
                help="Maximum % of portfolio equity risked between entry and stop-loss level.",
            )
            alloc_pct = st.number_input(
                "Max Position Allocation (%)",
                min_value=5.0,
                max_value=100.0,
                value=float(settings.max_position_allocation_pct),
                step=5.0,
                help="Maximum total capital allocated to any single stock.",
            )
            daily_loss_pct = st.number_input(
                "Max Daily Loss Limit (%)",
                min_value=1.0,
                max_value=20.0,
                value=float(settings.max_daily_loss_pct),
                step=1.0,
                help="If realized losses today reach this threshold, new BUY orders are blocked.",
            )
        with c2:
            max_pos = st.number_input(
                "Max Open Positions",
                min_value=1,
                max_value=20,
                value=int(settings.max_open_positions),
                step=1,
            )
            fee_pct = st.number_input(
                "Transaction Fee (%)",
                min_value=0.0,
                max_value=1.0,
                value=float(settings.transaction_fee_pct),
                step=0.01,
            )
            slip_pct = st.number_input(
                "Modeled Slippage (%)",
                min_value=0.0,
                max_value=1.0,
                value=float(settings.default_slippage_pct),
                step=0.01,
            )

        if st.button("Save Risk Parameters", type="primary"):
            # Update active runtime settings
            settings.max_risk_per_trade_pct = risk_pct
            settings.max_position_allocation_pct = alloc_pct
            settings.max_daily_loss_pct = daily_loss_pct
            settings.max_open_positions = int(max_pos)
            settings.transaction_fee_pct = fee_pct
            settings.default_slippage_pct = slip_pct
            st.success("Risk settings successfully updated.")

    st.markdown("---")

    # Paper Account Reset Section
    with st.container(border=True):
        st.markdown("### Reset Paper Account")
        st.warning("Resetting the account permanently clears all simulated positions, open orders, and trade history.")

        new_bal = st.number_input("Starting Capital for Reset ($)", value=100000.0, step=10000.0)
        confirm_reset = st.checkbox("I understand this will erase all current positions and trade history.")

        if st.button("Confirm & Reset Portfolio", type="secondary", disabled=not confirm_reset):
            repo.reset_account(starting_balance=new_bal)
            st.success(f"Paper portfolio reset to ${new_bal:,.2f} starting capital.")
            st.rerun()
