"""Trading Signals UI: Deterministic strategy evaluation, badges, rationale, and invalidation rules."""

import streamlit as st
import pandas as pd
from market_data.base import BaseMarketDataProvider, MarketDataResult
from services.signal_engine import SignalEngine
from database.models import StrategySignal, SignalType


def render_signals(data_provider: BaseMarketDataProvider):
    st.subheader("Strategy Signals & Quantitative Rules")
    st.caption("Deterministic signal generation based on mathematical rules. No model hallucinations.")

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        symbol = st.text_input("Ticker Symbol", value="AAPL", key="sig_sym").strip().upper()
    with c2:
        period = st.selectbox("Historical Lookback", ["3mo", "6mo", "1y"], index=1, key="sig_period")
    with c3:
        st.write("")
        st.write("")
        eval_btn = st.button("Generate Signals", use_container_width=True)

    with st.spinner(f"Evaluating strategies for {symbol}..."):
        res: MarketDataResult = data_provider.get_historical_ohlcv(symbol, period=period, interval="1d")

    if not res.is_valid:
        st.error(f"Cannot generate signals: {res.error}")
        return

    df = res.df
    engine = SignalEngine()
    signals = engine.evaluate_all(df, symbol)

    # Strategy Cards
    for s in signals:
        badge_color = "#00C853" if s.signal == SignalType.BUY else "#D50000" if s.signal == SignalType.SELL else "#757575"
        with st.container(border=True):
            col_hdr1, col_hdr2 = st.columns([3, 1])
            with col_hdr1:
                st.markdown(f"#### {s.strategy_name}")
            with col_hdr2:
                st.markdown(
                    f"<div style='text-align: right;'>"
                    f"<span style='background-color: {badge_color}; color: white; padding: 4px 12px; border-radius: 6px; font-weight: bold;'>"
                    f"{s.signal.value}</span></div>",
                    unsafe_allow_html=True,
                )

            st.write(f"**Current Price:** ${s.price:,.2f}")
            st.write(f"**Strategy Rationale:** {s.rationale}")

            if s.suggested_stop_loss:
                col_sl, col_tp = st.columns(2)
                col_sl.info(f"**Suggested Stop-Loss:** ${s.suggested_stop_loss:,.2f}")
                if s.suggested_take_profit:
                    col_tp.success(f"**Suggested Take-Profit:** ${s.suggested_take_profit:,.2f}")

            st.warning(f"**Invalidation Criteria:** {s.invalidation_criteria}")

            if s.indicators:
                with st.expander("Computed Indicator Levels"):
                    st.json({k: round(v, 4) if isinstance(v, float) else v for k, v in s.indicators.items()})
