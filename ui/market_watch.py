"""Market Watch UI: Symbol search, interactive candlestick charts, and technical indicators."""

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from typing import Optional

from market_data.base import BaseMarketDataProvider, MarketDataResult
from services.indicators import add_all_indicators, get_latest_indicators


def render_market_watch(data_provider: BaseMarketDataProvider):
    st.subheader("Market Watch & Technical Charting")
    st.caption("Interactive candlestick analysis with deterministic technical indicators.")

    # Search & Timeframe Controls
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        popular_symbols = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS"]
        custom_sym = st.text_input("Enter Ticker Symbol (US or Indian NSE with .NS)", value="AAPL")
        symbol = custom_sym.strip().upper() if custom_sym else "AAPL"
    with c2:
        period = st.selectbox("Period", options=["1mo", "3mo", "6mo", "1y", "2y", "5y"], index=2)
    with c3:
        interval = st.selectbox("Interval", options=["1d", "1wk", "1h", "15m"], index=0)
    with c4:
        st.write("")
        st.write("")
        refresh = st.button("Fetch Data", use_container_width=True)

    with st.spinner(f"Retrieving market candles for {symbol}..."):
        res: MarketDataResult = data_provider.get_historical_ohlcv(symbol, period=period, interval=interval)

    if not res.is_valid:
        st.error(f"Failed to load market data for '{symbol}': {res.error}")
        return

    df = res.df
    meta = res.meta

    # Provider & Staleness Badges
    b1, b2, b3 = st.columns([2, 2, 2])
    with b1:
        st.info(f"**Provider**: {meta.provider}")
    with b2:
        st.info(f"**Latest Candle**: {meta.latest_timestamp[:19] if meta.latest_timestamp else 'N/A'}")
    with b3:
        if meta.warning:
            st.warning(f"**Data Notice**: {meta.warning}")

    # Compute technical indicators
    enriched_df = add_all_indicators(df)
    snapshot = get_latest_indicators(df)

    # Latest Quote Header
    last_close = float(df["Close"].iloc[-1])
    first_close = float(df["Close"].iloc[0])
    chg = last_close - first_close
    chg_pct = (chg / first_close) * 100.0

    st.markdown(
        f"### {symbol}: **${last_close:,.2f}** "
        f"<span style='color: {'#00C853' if chg >= 0 else '#D50000'}; font-size: 0.9em;'>"
        f"{'+' if chg >= 0 else ''}${chg:,.2f} ({chg_pct:+.2f}%)</span>",
        unsafe_allow_html=True,
    )

    # Toggle indicator overlays
    with st.expander("Chart Overlays & Indicators", expanded=True):
        col_t1, col_t2, col_t3, col_t4 = st.columns(4)
        with col_t1:
            show_sma = st.checkbox("SMA (20 & 50)", value=True)
        with col_t2:
            show_ema = st.checkbox("EMA (12 & 26)", value=True)
        with col_t3:
            show_bb = st.checkbox("Bollinger Bands (20, 2)", value=False)
        with col_t4:
            show_macd = st.checkbox("MACD Subplot", value=True)

    # Build 3-panel Plotly Chart: (1) Price & Overlays, (2) Volume, (3) RSI or MACD
    rows = 3
    row_heights = [0.6, 0.2, 0.2]
    subplot_titles = [f"{symbol} Price Action", "Volume", "RSI (14) & Momentum"]

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
    )

    # Panel 1: Candlestick
    fig.add_trace(
        go.Candlestick(
            x=enriched_df.index,
            open=enriched_df["Open"],
            high=enriched_df["High"],
            low=enriched_df["Low"],
            close=enriched_df["Close"],
            name="OHLC",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1,
        col=1,
    )

    # Overlays
    if show_sma and "SMA_20" in enriched_df.columns:
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["SMA_20"], mode="lines", name="SMA 20", line=dict(color="#FFB300", width=1.5)),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["SMA_50"], mode="lines", name="SMA 50", line=dict(color="#29B6F6", width=1.5)),
            row=1,
            col=1,
        )

    if show_ema and "EMA_12" in enriched_df.columns:
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["EMA_12"], mode="lines", name="EMA 12", line=dict(color="#AB47BC", width=1.5)),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["EMA_26"], mode="lines", name="EMA 26", line=dict(color="#FF7043", width=1.5)),
            row=1,
            col=1,
        )

    if show_bb and "BB_Upper" in enriched_df.columns:
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["BB_Upper"], mode="lines", name="BB Upper", line=dict(color="rgba(150, 150, 150, 0.5)", dash="dash")),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["BB_Lower"], mode="lines", name="BB Lower", line=dict(color="rgba(150, 150, 150, 0.5)", dash="dash")),
            row=1,
            col=1,
        )

    # Panel 2: Volume
    colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(enriched_df["Close"], enriched_df["Open"])]
    fig.add_trace(
        go.Bar(x=enriched_df.index, y=enriched_df["Volume"], name="Volume", marker_color=colors),
        row=2,
        col=1,
    )
    if "Volume_SMA_20" in enriched_df.columns:
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["Volume_SMA_20"], name="Vol SMA 20", line=dict(color="#FFA726", width=1)),
            row=2,
            col=1,
        )

    # Panel 3: RSI (14)
    if "RSI_14" in enriched_df.columns:
        fig.add_trace(
            go.Scatter(x=enriched_df.index, y=enriched_df["RSI_14"], name="RSI (14)", line=dict(color="#8E24AA", width=1.5)),
            row=3,
            col=1,
        )
        fig.add_hline(y=70, line_dash="dot", line_color="red", row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="green", row=3, col=1)

    fig.update_layout(
        height=750,
        margin=dict(l=40, r=40, t=40, b=20),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_dark",
    )

    st.plotly_chart(fig, use_container_width=True)

    # Snapshot Table
    if snapshot:
        st.markdown("### Technical Metrics Snapshot")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("RSI (14)", f"{snapshot.rsi_14:.1f}" if snapshot.rsi_14 else "N/A")
        m2.metric("MACD Hist", f"{snapshot.macd_hist:.3f}" if snapshot.macd_hist else "N/A")
        m3.metric("ATR (14 Volatility)", f"${snapshot.atr_14:.2f}" if snapshot.atr_14 else "N/A")
        m4.metric("Support / Resistance", f"${snapshot.support:.2f} / ${snapshot.resistance:.2f}")
