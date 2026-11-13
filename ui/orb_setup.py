"""Streamlit View: ORB Setup (Opening Range Breakout + EMA20 + Volume) for XAUUSD on Delta Exchange India."""
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

from config.orb_settings import ORBSettings, settings as default_orb_settings
from data.delta_candles import fetch_5m_candles, DeltaCandleError
from strategy.orb_ema_volume import evaluate_orb
from strategy.trend_following import evaluate_trend_following
from strategy.vwap_ema import evaluate_vwap_ema
from strategy.bb_rsi_mean_reversion import evaluate_bb_rsi
from strategy.atr_compression_breakout import evaluate_atr_compression_breakout
from strategy.liquidity_sweep import evaluate_liquidity_sweep
from strategy.arbitrator import arbitrate_strategies, evaluate_arbitrated_portfolio
from regime.market_regime import detect_market_regime, MarketRegime
from services.delta_risk import evaluate_delta_trade, DeltaDailyRiskStore
from services.orb_backtest import backtest_orb
from services.delta_live_executor import DeltaLiveExecutor
from services.delta_reconciliation_service import DeltaReconciliationService
from services.delta_paper_broker import DeltaPaperBroker
from services.delta_paper_bot import DeltaPaperBot

ROOT = Path(__file__).resolve().parent.parent

STRATEGY_OPTIONS = [
    "Multi-Strategy Intelligence Engine (Arbitrated)",
    "ORB (Opening Range Breakout)",
    "Trend-Following Pullback (15m + 5m)",
    "VWAP + EMA Confirmation",
    "Bollinger Bands + RSI Mean Reversion",
    "ATR Volatility Compression Breakout",
    "Liquidity Sweep Reversal",
]


def render_orb_setup():
    """Render the XAUUSD Strategy page with live checklist, chart overlays, risk guard, and backtesting."""
    st.title("🥇 XAUUSD Trading System (Delta Exchange India)")
    st.caption("Multi-Strategy Intelligence Engine & Backtester: Independent Strategies + Market Regime Classification + Arbitration.")

    # Sidebar parameters
    st.sidebar.markdown("### ⚙️ Strategy & Parameters")
    strategy_choice = st.sidebar.selectbox(
        "Active Strategy",
        options=STRATEGY_OPTIONS,
        index=0,
    )
    symbol = st.sidebar.text_input("Symbol", value="XAUTUSD").strip().upper()
    timezone_choice = st.sidebar.selectbox(
        "Timezone",
        options=["Asia/Kolkata", "America/New_York", "UTC"],
        index=0,
    )
    session_start = st.sidebar.text_input("Session Start (HH:MM)", value="09:00" if timezone_choice == "Asia/Kolkata" else "09:30")
    
    st.sidebar.markdown("### 🛡️ Risk Controls (Tiny Account)")
    account_equity_inr = st.sidebar.number_input("Account Balance (₹ INR)", min_value=10.0, value=100.0, step=10.0)
    max_risk_inr = st.sidebar.number_input("Max Risk Per Trade (₹ INR)", min_value=1.0, value=5.0, step=1.0)
    daily_loss_limit_inr = st.sidebar.number_input("Daily Loss Limit (₹ INR)", min_value=2.0, value=15.0, step=1.0)
    usd_inr_rate = st.sidebar.number_input("USD / INR Exchange Rate", min_value=50.0, value=86.5, step=0.1)
    leverage = st.sidebar.number_input("Leverage (Delta)", min_value=1.0, max_value=100.0, value=100.0, step=10.0)

    auto_refresh = st.sidebar.checkbox("Auto-refresh data (every 5 min)", value=False)
    if auto_refresh:
        # Streamlit rerun interval hint
        st.sidebar.caption("Auto-refresh active. Data will refresh when re-rendering.")

    cfg = ORBSettings(
        symbol=symbol,
        timezone=timezone_choice,
        session_start=session_start,
        orb_minutes=15,
        ema_period=20,
        volume_period=20,
        leverage=leverage,
        strategy_type=(
            "PORTFOLIO" if "Arbitrated" in strategy_choice else
            "TREND_FOLLOWING" if "Trend" in strategy_choice else
            "VWAP_EMA" if "VWAP" in strategy_choice else
            "BB_RSI" if "Bollinger" in strategy_choice else
            "ATR_BREAKOUT" if "Compression" in strategy_choice else
            "LIQUIDITY_SWEEP" if "Liquidity" in strategy_choice else
            "ORB"
        ),
    )

    tab_live, tab_backtest = st.tabs(["📊 Live Strategy Monitor", "🧪 Backtesting"])

    with tab_live:
        # Prominent PAPER TRADING Safety Banner
        st.markdown(
            "<div style='background: linear-gradient(90deg, #1E3A8A 0%, #0D9488 100%); color: white; padding: 12px 18px; border-radius: 8px; font-weight: bold; font-size: 1.05em; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between;'>"
            "<span>🛡️ PAPER TRADING – LIVE MARKET DATA – NO REAL ORDERS</span>"
            "<span style='background: rgba(255,255,255,0.2); padding: 4px 10px; border-radius: 4px; font-size: 0.8em;'>DELTA EXCHANGE INDIA (XAUTUSD)</span>"
            "</div>",
            unsafe_allow_html=True,
        )

        mode_color = "#D50000" if default_orb_settings.mode == "LIVE" else "#FF8F00" if default_orb_settings.mode == "ALERT_ONLY" else "#1E88E5"
        kill_switch_tag = ""
        if default_orb_settings.kill_switch_active:
            kill_switch_tag = " &nbsp;<span style='background-color: #B71C1C; color: white; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 0.85em;'>⚠️ KILL SWITCH ACTIVE</span>"

        col_m1, col_m2 = st.columns([3, 1])
        with col_m1:
            st.markdown(
                f"<div style='margin-bottom: 8px;'>"
                f"<span style='background-color: {mode_color}; color: white; padding: 4px 12px; border-radius: 6px; font-weight: bold; font-size: 0.85em;'>"
                f"MODE: {default_orb_settings.mode}</span>"
                f"{kill_switch_tag}"
                f"</div>",
                unsafe_allow_html=True,
            )
        with col_m2:
            reload_btn = st.button("🔄 Refresh Data", use_container_width=True)

        # Real-Time WebSocket Market Data & Paper Bot Engine Panel
        paper_bot = DeltaPaperBot.get_instance(cfg=cfg)
        bot_telem = paper_bot.get_dashboard_telemetry()
        feed_info = bot_telem.get("feed", {})
        acc_info = bot_telem.get("account", {})

        with st.container(border=True):
            col_b1, col_b2, col_b3 = st.columns([2, 1, 1])
            with col_b1:
                ws_status = "🟢 WebSocket Streaming Live" if feed_info.get("ws_connected") else ("🟡 Starting WebSocket..." if bot_telem.get("is_running") else "⚪ WebSocket Idle")
                st.markdown(f"### 📡 Delta Exchange India: {ws_status}")
                st.caption(f"Symbol: `{cfg.symbol}` | Product ID: `131253` | WebSocket: `wss://socket.india.delta.exchange` | REST: `https://api.india.delta.exchange`")
            with col_b2:
                if bot_telem.get("is_running"):
                    if st.button("⏹️ Stop Paper Bot", use_container_width=True, type="secondary"):
                        paper_bot.stop()
                        st.rerun()
                else:
                    if st.button("▶️ Start Live Paper Bot", use_container_width=True, type="primary"):
                        paper_bot.start(bootstrap_lookback=200)
                        st.rerun()
            with col_b3:
                bot_status_str = "ACTIVE" if bot_telem.get("is_running") else "STANDBY"
                st.markdown(f"<div style='text-align: right; padding-top: 8px;'><b>Status:</b> <code>{bot_status_str}</code></div>", unsafe_allow_html=True)

            # Paper Account Metrics Row
            p_col1, p_col2, p_col3, p_col4, p_col5 = st.columns(5)
            p_col1.metric("Simulated Equity", f"${acc_info.get('equity', 10000.0):,.2f}", delta=f"{acc_info.get('realized_pnl', 0.0):+,.2f} Realized P&L")
            p_col2.metric("Available Cash", f"${acc_info.get('cash_balance', 10000.0):,.2f}")
            p_col3.metric("Win Rate", f"{acc_info.get('win_rate_pct', 0.0):.1f}%", f"{acc_info.get('winning_trades', 0)}W / {acc_info.get('losing_trades', 0)}L")
            p_col4.metric("Profit Factor", f"{acc_info.get('profit_factor', 0.0):.2f}")
            p_col5.metric("Max Drawdown", f"{acc_info.get('max_drawdown_pct', 0.0):.2f}%", delta=f"-${acc_info.get('max_drawdown_cash', 0.0):,.2f}", delta_color="inverse")

            # Active Simulated Position
            open_pos = acc_info.get("open_position")
            if open_pos:
                st.info(
                    f"**Active Simulated Position**: {open_pos['direction']} {open_pos['lots']} lots ({open_pos['contract_qty']} XAUT) @ ${open_pos['entry_price']:.2f} | "
                    f"Market: ${feed_info.get('latest_price', open_pos['entry_price']):.2f} | "
                    f"Unrealized P&L: **{open_pos.get('unrealized_pnl', 0.0):+,.2f} USD** | "
                    f"SL: ${open_pos['stop_loss']:.2f} | TP: ${open_pos['take_profit']:.2f} | Strategy: `{open_pos['strategy_name']}`"
                )
            else:
                st.caption(f"⚪ No active simulated position. Evaluation Status: {bot_telem.get('last_rejection_reason', 'Awaiting candle close')}")

            # Closed Trades Expander
            closed_trades = acc_info.get("closed_trades", [])
            if closed_trades:
                with st.expander(f"📜 Simulated Paper Trade History ({len(closed_trades)} Trades)", expanded=False):
                    st.dataframe(pd.DataFrame(closed_trades), use_container_width=True)

        if default_orb_settings.mode == "LIVE":
            with st.expander("🚨 Live Order Controls & Emergency Kill-Switch", expanded=False):
                k_col1, k_col2 = st.columns([2, 1])
                with k_col1:
                    st.caption("Active in LIVE mode: Dispatches instant emergency cancellation to Delta Exchange.")
                with k_col2:
                    if st.button("🚨 EMERGENCY CANCEL ALL", type="primary", use_container_width=True):
                        executor = DeltaLiveExecutor()
                        res = executor.cancel_all_product_orders()
                        st.warning(f"Kill-switch executed. Orders marked cancelled: {res.get('orders_marked_cancelled')}")

                reconcile_svc = DeltaReconciliationService()
                active_orders = reconcile_svc.get_active_orders()
                if active_orders:
                    st.markdown("**Active Exchange Orders:**")
                    st.dataframe(pd.DataFrame([o.to_dict() for o in active_orders]), use_container_width=True)
                else:
                    st.info("No active or pending live orders currently open.")

        candles_df = None
        data_error = None

        lookback_req = 300
        with st.spinner(f"Loading latest 5m candles for {symbol}..."):
            try:
                candles_df = fetch_5m_candles(symbol=symbol, lookback_candles=lookback_req)
            except Exception as e:
                data_error = str(e)

        if candles_df is None or candles_df.empty:
            st.error(f"Could not retrieve real-time candle data: {data_error}")
            st.info("Tip: Delta Exchange market data requires an active connection or historical cached bars.")
            st.markdown("---")
            st.caption("⚠️ Educational tool, not financial advice. Alert-only.")
            return

        # Market Regime Detection
        regime_res = detect_market_regime(candles_df, cfg=cfg)
        regime_badges = {
            MarketRegime.TRENDING_BULLISH: ("#00C853", "🟢 TRENDING BULLISH"),
            MarketRegime.TRENDING_BEARISH: ("#D50000", "🔴 TRENDING BEARISH"),
            MarketRegime.RANGING: ("#29B6F6", "🔵 RANGING / MEAN-REVERTING"),
            MarketRegime.VOLATILE_BREAKOUT: ("#AB47BC", "🟣 VOLATILE BREAKOUT"),
            MarketRegime.UNCERTAIN: ("#757575", "⚪ UNCERTAIN (NO TRADE)"),
        }
        reg_color, reg_title = regime_badges.get(regime_res.regime, ("#757575", str(regime_res.regime.value)))

        st.markdown(
            f"<div style='background: rgba(255,255,255,0.03); padding: 10px 14px; border-radius: 8px; border-left: 5px solid {reg_color}; margin-bottom: 12px;'>"
            f"<div style='display: flex; justify-content: space-between; align-items: center;'>"
            f"<span style='font-size: 1.05em; font-weight: bold; color: {reg_color};'>{reg_title}</span>"
            f"<span style='font-size: 0.85em; color: #BBB;'>Confidence: <b>{regime_res.confidence * 100:.0f}%</b></span>"
            f"</div>"
            f"<div style='font-size: 0.85em; color: #DDD; margin-top: 3px;'>{regime_res.rationale}</div>"
            f"<div style='font-size: 0.75em; color: #888; margin-top: 2px;'>Allowed Strategies: {', '.join(regime_res.allowed_strategies) if regime_res.allowed_strategies else 'None (Market Uncertain)'}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Deterministic strategy evaluation & arbitration
        arbitration_res = None
        if "Arbitrated" in strategy_choice:
            arbitration_res = arbitrate_strategies(candles_df, cfg=cfg)
            signal = arbitration_res.winning_signal
        elif "Trend" in strategy_choice:
            signal = evaluate_trend_following(candles_df, cfg=cfg)
        elif "VWAP" in strategy_choice:
            signal = evaluate_vwap_ema(candles_df, cfg=cfg)
        elif "Bollinger" in strategy_choice:
            signal = evaluate_bb_rsi(candles_df, cfg=cfg)
        elif "Compression" in strategy_choice:
            signal = evaluate_atr_compression_breakout(candles_df, cfg=cfg)
        elif "Liquidity" in strategy_choice:
            signal = evaluate_liquidity_sweep(candles_df, cfg=cfg)
        else:
            signal = evaluate_orb(candles_df, cfg=cfg)
        chk = signal.checklist or {}

        # Evaluate risk for tiny account if signal or price available
        current_price = float(candles_df["Close"].iloc[-1])
        sl_val = signal.sl if signal.sl else (current_price * 0.99)
        risk_decision = evaluate_delta_trade(
            entry=signal.entry or current_price,
            sl=sl_val,
            account_equity_inr=account_equity_inr,
            available_margin_inr=account_equity_inr * 0.5,
            max_risk_per_trade_inr=max_risk_inr,
            daily_loss_limit_inr=daily_loss_limit_inr,
            usd_inr_rate=usd_inr_rate,
            leverage=leverage,
            timezone_str=cfg.timezone,
            cfg=cfg,
        )

        # Status Banner
        if signal.direction in {"LONG", "SHORT"} and risk_decision.approved:
            st.success(f"🟢 **ALERT: CONFIRMED {signal.direction} SETUP** | Entry: ${signal.entry:.2f} | SL: ${signal.sl:.2f} | TP: ${signal.tp:.2f} ({signal.r_multiple:.1f}R)")
        elif chk.get("breakout_close") and not chk.get("retest"):
            st.warning("⏳ **WAITING: Valid breakout confirmed, waiting for retest and rejection candle.**")
        else:
            st.info(f"⚪ **{signal.reason}**")

        # Top Metric Cards
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Current Price", f"${current_price:,.2f}")
        active_label = getattr(signal, "strategy_name", strategy_choice.split()[0])
        m2.metric("Selected Strategy", active_label)
        score_val = f"{getattr(signal, 'quality_score', 0.0) * 100:.0f}%" if getattr(signal, "quality_score", None) is not None else "N/A"
        m3.metric("Signal Quality", score_val)
        last_ema = float(candles_df["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
        m4.metric("EMA 20", f"${last_ema:,.2f}")
        m5.metric("Risk Guard", "Approved" if risk_decision.approved else "Blocked", delta="Safe" if risk_decision.approved else "Risk Limit")

        # Strategy Arbitration Matrix or Checklist
        if arbitration_res is not None:
            st.subheader("🤖 Strategy Arbitration Matrix")
            st.caption("Independent signal generation, regime filtering, conflict rejection, and quality scoring.")
            grid_rows = []
            for s_name, s_sig in arbitration_res.all_signals.items():
                is_selected = (
                    signal.direction in {"LONG", "SHORT"}
                    and s_sig.direction == signal.direction
                    and getattr(s_sig, "strategy_name", s_name) == getattr(signal, "strategy_name", "")
                )
                status_text = "🏆 WINNER" if is_selected else ("✅ ELIGIBLE" if s_sig in arbitration_res.eligible_signals else "⚪ FILTERED")
                grid_rows.append({
                    "Strategy": s_name,
                    "Signal": s_sig.direction,
                    "Quality Score": f"{getattr(s_sig, 'quality_score', 0.0) * 100:.0f}%",
                    "Status": status_text,
                    "Reason / Diagnostics": s_sig.reason,
                })
            st.dataframe(pd.DataFrame(grid_rows), use_container_width=True)

        def badge(ok: bool, text: str):
            if ok:
                st.markdown(f"✅ **{text}**")
            else:
                st.markdown(f"❌ <span style='color: #ef5350;'>{text}</span>", unsafe_allow_html=True)

        if "Trend" in strategy_choice:
            st.subheader("📋 7-Point Trend-Following Verification Checklist")
            col_c1, col_c2, col_c3, col_c4 = st.columns(4)
            with col_c1:
                badge(chk.get("trend_15m_aligned", False), "1. 15m Trend (EMA20 vs 50)")
                badge(chk.get("pullback_confirmed", False), "2. 5m EMA Pullback")
            with col_c2:
                badge(chk.get("continuation_candle", False), "3. Rejection / Continuation")
                badge(chk.get("volume_confirmed", False), "4. Volume Confirmed")
            with col_c3:
                badge(chk.get("not_overextended", False), "5. Not Overextended (<2.5 ATR)")
                badge(chk.get("rr_ok", False), "6. 1:2 R:R Net of Costs")
            with col_c4:
                badge(risk_decision.approved, f"7. Risk Approved (≤ ₹{max_risk_inr:.1f})")
        elif "ORB" in strategy_choice:
            st.subheader("📋 8-Point ORB Verification Checklist")
            col_c1, col_c2, col_c3, col_c4 = st.columns(4)
            with col_c1:
                badge(chk.get("orb_set", False), "1. ORB Established (15m)")
                badge(chk.get("breakout_close", False), "2. Breakout Close (No Wick)")
            with col_c2:
                badge(chk.get("ema_ok", False), "3. EMA20 Side & Slope")
                badge(chk.get("volume_ok", False), "4. Volume > 20-Avg")
            with col_c3:
                badge(chk.get("retest", False), "5. Retest Holds")
                badge(chk.get("rejection", False), "6. Rejection Candle")
            with col_c4:
                badge(chk.get("rr_ok", False), "7. 1:2 R:R Net of Costs")
                badge(risk_decision.approved, f"8. Tiny Risk (≤ ₹{max_risk_inr:.1f})")
        elif chk:
            st.subheader(f"📋 Verification Checklist: {active_label}")
            cols = st.columns(len(chk))
            for i, (k, v) in enumerate(chk.items()):
                with cols[i % len(cols)]:
                    badge(bool(v), k.replace("_", " ").title())

        # Candlestick Chart with Overlays
        st.subheader("📈 5m Interactive Session Chart")
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)

        fig.add_trace(
            go.Candlestick(
                x=candles_df.index,
                open=candles_df["Open"],
                high=candles_df["High"],
                low=candles_df["Low"],
                close=candles_df["Close"],
                name="5m OHLC",
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
            ),
            row=1, col=1,
        )

        # EMA20 Overlay
        ema20_series = candles_df["Close"].ewm(span=20, adjust=False).mean()
        fig.add_trace(
            go.Scatter(x=candles_df.index, y=ema20_series, mode="lines", name="5m EMA 20", line=dict(color="#FFB300", width=1.5)),
            row=1, col=1,
        )
        if "Trend" in strategy_choice:
            ema50_series = candles_df["Close"].ewm(span=50, adjust=False).mean()
            fig.add_trace(
                go.Scatter(x=candles_df.index, y=ema50_series, mode="lines", name="5m EMA 50", line=dict(color="#29B6F6", width=1.5)),
                row=1, col=1,
            )

        # ORB Lines
        if signal.orb_high:
            fig.add_hline(y=signal.orb_high, line_dash="dash", line_color="#00C853", annotation_text="ORB High", row=1, col=1)
        if signal.orb_low:
            fig.add_hline(y=signal.orb_low, line_dash="dash", line_color="#D50000", annotation_text="ORB Low", row=1, col=1)

        # Signal Markers
        if signal.direction in {"LONG", "SHORT"} and signal.entry:
            fig.add_trace(
                go.Scatter(
                    x=[candles_df.index[-1]],
                    y=[signal.entry],
                    mode="markers+text",
                    name=f"{signal.direction} Signal",
                    marker=dict(symbol="triangle-up" if signal.direction == "LONG" else "triangle-down", size=14, color="#00C853" if signal.direction == "LONG" else "#D50000"),
                    text=[f"{signal.direction} @ {signal.entry}"],
                    textposition="top center" if signal.direction == "LONG" else "bottom center",
                ),
                row=1, col=1,
            )

        # Volume Subplot
        vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(candles_df["Close"], candles_df["Open"])]
        fig.add_trace(go.Bar(x=candles_df.index, y=candles_df["Volume"], name="Volume", marker_color=vol_colors), row=2, col=1)

        fig.update_layout(height=650, margin=dict(l=20, r=20, t=30, b=20), xaxis_rangeslider_visible=False, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

        # Risk Breakdown Box
        with st.expander("🔍 Tiny Account Risk & Lot Sizing Details", expanded=False):
            st.write(f"**Max Risk Budget:** ₹{max_risk_inr:.2f} | **Available Margin:** ₹{account_equity_inr * 0.5:.2f}")
            st.write(f"**Calculated Lot Size:** {risk_decision.lots} Lot(s) ({risk_decision.lots * 0.001:.3f} XAUT)")
            st.write(f"**Loss at Stop-Loss:** ${risk_decision.loss_usd:.4f} USD (~₹{risk_decision.loss_inr:.2f} INR)")
            st.write(f"**Estimated Fees + Spread:** ${risk_decision.fees_usd + risk_decision.spread_slippage_usd:.4f} USD (~₹{risk_decision.fees_inr + risk_decision.spread_slippage_inr:.2f} INR)")
            st.write(f"**Required Margin:** ${risk_decision.margin_required_usd:.4f} USD (~₹{risk_decision.margin_required_inr:.2f} INR)")
            st.caption(f"Status Note: {risk_decision.reason}")

    # Tab 2: Backtesting
    with tab_backtest:
        summary_csv = ROOT / "data" / "multi_strategy_backtest_summary.csv"
        if summary_csv.exists():
            st.markdown("### 📊 Multi-Strategy Walk-Forward Benchmark Summary")
            st.caption("Empirical performance on XAUTUSD 5m (Aug 5 – Oct 30, 2026, 24,999 bars) including trading costs (0.02% fee, 0.02% spread, 0.02% slippage).")
            try:
                summary_df = pd.read_csv(summary_csv)
                st.dataframe(summary_df, use_container_width=True)
            except Exception as e:
                st.warning(f"Could not load benchmark summary: {e}")
            st.markdown("---")

        st.subheader("🧪 Deterministic Strategy Backtester")
        st.caption("Chronological historical simulation with strict next-bar execution, fees, spread, and conservative collision resolution.")

        bc0, bc1, bc2, bc3 = st.columns(4)
        with bc0:
            bt_strat_choice = st.selectbox(
                "Strategy",
                STRATEGY_OPTIONS,
                index=0,
                key="backtest_strat_select",
            )
        with bc1:
            backtest_days = st.number_input("Lookback Days", min_value=1, max_value=60, value=7)
        with bc2:
            initial_backtest_equity = st.number_input("Starting Equity ($)", min_value=10.0, value=1000.0, step=100.0)
        with bc3:
            st.write("")
            st.write("")
            run_bt_btn = st.button("🚀 Run Backtest", use_container_width=True)

        if run_bt_btn:
            with st.spinner(f"Executing chronological {bt_strat_choice} backtest replay..."):
                try:
                    bt_candles = fetch_5m_candles(symbol=symbol, lookback_candles=int(backtest_days * 24 * 12))
                    if "Arbitrated" in bt_strat_choice:
                        strat_fn = evaluate_arbitrated_portfolio
                    elif "Trend" in bt_strat_choice:
                        strat_fn = evaluate_trend_following
                    elif "VWAP" in bt_strat_choice:
                        strat_fn = evaluate_vwap_ema
                    elif "Bollinger" in bt_strat_choice:
                        strat_fn = evaluate_bb_rsi
                    elif "Compression" in bt_strat_choice:
                        strat_fn = evaluate_atr_compression_breakout
                    elif "Liquidity" in bt_strat_choice:
                        strat_fn = evaluate_liquidity_sweep
                    else:
                        strat_fn = evaluate_orb

                    res = backtest_orb(bt_candles, initial_equity=initial_backtest_equity, cfg=cfg, chart_output_path=None, strategy_fn=strat_fn)

                    st.markdown("### Backtest Performance Summary")
                    b1, b2, b3, b4 = st.columns(4)
                    b1.metric("Total Trades", res.trades)
                    b1.metric("Win Rate", f"{res.win_rate_pct:.1f}%")
                    b2.metric("Total Realized R", f"{res.total_r:+.2f} R")
                    b2.metric("Average R", f"{res.average_r:+.4f} R")
                    b3.metric("Profit Factor", f"{res.profit_factor:.2f}")
                    b3.metric("Max Drawdown (R)", f"{res.max_drawdown_r:.2f} R")
                    b4.metric("Days With No Setup", f"{res.skipped_days} ({res.no_setup_pct:.1f}%)")
                    b4.metric("Simulated Net P&L", f"${res.net_pnl:+,.2f}")

                    # Equity Curve
                    if res.equity_curve:
                        eq_df = pd.DataFrame(res.equity_curve)
                        fig_eq = go.Figure()
                        fig_eq.add_trace(go.Scatter(x=eq_df["date"], y=eq_df["equity"], mode="lines+markers", line=dict(color="#2563EB", width=2)))
                        fig_eq.update_layout(title="Account Equity Curve ($)", xaxis_title="Date", yaxis_title="Equity", template="plotly_dark", height=400)
                        st.plotly_chart(fig_eq, use_container_width=True)

                    # Trade Log
                    if res.trade_log:
                        st.subheader("📜 Detailed Trade Log")
                        st.dataframe(pd.DataFrame(res.trade_log), use_container_width=True)
                    else:
                        st.info("No completed setups met all 8 filter criteria during this backtest period.")

                except Exception as exc:
                    st.error(f"Backtest error: {exc}")

    # Always show Educational Disclaimer
    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; color: #888; font-size: 0.9em;'>"
        "⚠️ <strong>Educational tool, not financial advice. Alert-only.</strong> "
        "No real orders are placed. Past performance does not guarantee future results."
        "</div>",
        unsafe_allow_html=True,
    )

