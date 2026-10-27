"""Strategy Backtesting UI: Candle-by-candle simulation, equity curves, drawdown, and trade logs."""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from market_data.base import BaseMarketDataProvider, MarketDataResult
from services.backtester import BacktestEngine, BacktestResult
from strategies.moving_average import MovingAverageCrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.custom_strategy import CustomStrategy


def render_backtesting(data_provider: BaseMarketDataProvider):
    st.subheader("Quantitative Strategy Backtesting")
    st.caption("Event-driven historical simulation with next-bar execution, fees, slippage, and no look-ahead bias.")

    # Configuration Controls
    col_strat, col_sym, col_period = st.columns([2, 1, 1])
    with col_strat:
        strat_choice = st.selectbox(
            "Select Strategy",
            ["Moving Average Crossover", "RSI Confirmation", "Multi-Factor Trend Following", "Custom Flexible Strategy"],
        )
    with col_sym:
        symbol = st.text_input("Ticker Symbol", value="AAPL", key="bt_sym").strip().upper()
    with col_period:
        period = st.selectbox("Historical Lookback", ["6mo", "1y", "2y", "5y"], index=1, key="bt_period")

    # Strategy Parameters Form
    with st.expander("Strategy & Engine Parameters", expanded=False):
        p1, p2, p3, p4 = st.columns(4)
        if strat_choice == "Moving Average Crossover":
            with p1:
                fast_p = st.number_input("Fast EMA", value=12, min_value=2, max_value=50)
            with p2:
                slow_p = st.number_input("Slow EMA", value=26, min_value=10, max_value=200)
            strategy = MovingAverageCrossoverStrategy(fast_period=int(fast_p), slow_period=int(slow_p))
        elif strat_choice == "RSI Confirmation":
            with p1:
                rsi_p = st.number_input("RSI Period", value=14, min_value=2)
            with p2:
                oversold = st.number_input("Oversold Level", value=30.0, min_value=10.0, max_value=50.0)
            with p3:
                overbought = st.number_input("Overbought Level", value=70.0, min_value=50.0, max_value=90.0)
            strategy = RSIStrategy(period=int(rsi_p), oversold=float(oversold), overbought=float(overbought))
        elif strat_choice == "Multi-Factor Trend Following":
            with p1:
                trend_ma = st.number_input("Trend SMA Period", value=50, min_value=20)
            strategy = TrendFollowingStrategy(trend_ma_period=int(trend_ma))
        else:
            with p1:
                sma_p = st.number_input("SMA Trend Period", value=50, min_value=10)
            with p2:
                sl_pct = st.number_input("Stop Loss %", value=2.5, min_value=0.5)
            with p3:
                tp_pct = st.number_input("Take Profit %", value=5.0, min_value=1.0)
            strategy = CustomStrategy(sma_period=int(sma_p), stop_loss_pct=float(sl_pct), take_profit_pct=float(tp_pct))

        c_cap, c_fee, c_slip = st.columns(3)
        with c_cap:
            initial_cap = st.number_input("Starting Capital ($)", value=100000.0, step=5000.0)
        with c_fee:
            fee_pct = st.number_input("Broker Fee (%)", value=0.1, step=0.01)
        with c_slip:
            slip_pct = st.number_input("Modeled Slippage (%)", value=0.05, step=0.01)

    run_btn = st.button("Run Historical Backtest", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner(f"Loading {period} market data and simulating {strat_choice} for {symbol}..."):
            res: MarketDataResult = data_provider.get_historical_ohlcv(symbol, period=period, interval="1d")

            if not res.is_valid:
                st.error(f"Backtest halted: {res.error}")
                return

            engine = BacktestEngine(
                initial_capital=float(initial_cap),
                fee_pct=float(fee_pct),
                slippage_pct=float(slip_pct),
            )
            bt_res: BacktestResult = engine.run(strategy, res.df, symbol, warmup_period=50)

        # Performance KPIs
        st.markdown("### Simulation Results")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Ending Equity", f"${bt_res.final_equity:,.2f}", delta=f"{bt_res.total_return_pct:+.2f}%")
        k2.metric("Benchmark (Buy & Hold)", f"{bt_res.benchmark_return_pct:+.2f}%")
        k3.metric("Max Drawdown", f"{bt_res.max_drawdown_pct:.2f}%", delta_color="inverse")
        k4.metric("Sharpe Ratio", f"{bt_res.sharpe_ratio:.2f}")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Trades", f"{bt_res.total_trades}")
        m2.metric("Win Rate", f"{bt_res.win_rate_pct:.1f}%")
        m3.metric("Profit Factor", f"{bt_res.profit_factor:.2f}")
        m4.metric("Avg Win / Loss", f"${bt_res.avg_win:,.2f} / ${bt_res.avg_loss:,.2f}")

        # Interactive Equity & Drawdown Chart
        if not bt_res.equity_curve.empty:
            st.markdown("### Equity Curve vs Underlying Asset")
            eq_df = bt_res.equity_curve.copy()
            # Normalize underlying close to starting capital for comparison
            initial_close = eq_df["Close"].iloc[0]
            eq_df["Benchmark"] = (eq_df["Close"] / initial_close) * bt_res.initial_capital

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=eq_df.index, y=eq_df["Equity"], mode="lines", name="Strategy Equity", line=dict(color="#00E676", width=2)))
            fig.add_trace(go.Scatter(x=eq_df.index, y=eq_df["Benchmark"], mode="lines", name=f"{symbol} Buy & Hold", line=dict(color="#90CAF9", width=1.5, dash="dot")))
            fig.update_layout(height=450, template="plotly_dark", margin=dict(l=40, r=40, t=30, b=20))
            st.plotly_chart(fig, use_container_width=True)

        # Trade History Table
        st.markdown("### Simulated Trade Log")
        if not bt_res.trades:
            st.info("No trades were triggered during this backtesting window with the selected parameters.")
        else:
            trades_data = []
            for t in bt_res.trades:
                trades_data.append({
                    "Entry Date": t.entry_date[:10],
                    "Exit Date": t.exit_date[:10],
                    "Side": t.side,
                    "Shares": int(t.quantity),
                    "Entry Price": f"${t.entry_price:,.2f}",
                    "Exit Price": f"${t.exit_price:,.2f}",
                    "Fee": f"${t.fee_paid:.2f}",
                    "P&L ($)": f"${t.pnl:+,.2f}",
                    "Return (%)": f"{t.pnl_pct:+.2f}%",
                    "Exit Trigger": t.exit_reason,
                })
            st.dataframe(pd.DataFrame(trades_data), use_container_width=True)
