"""AI Trading Assistant UI: Local Ollama conversational reasoning grounded in deterministic tools."""

import re
import streamlit as st
from typing import Optional
from agents.trading_supervisor import TradingSupervisor
from agents.ollama_client import OllamaClient
from config.settings import settings\nfrom agents.orb_tools import orb_current_signal, explain_no_trade


def render_assistant(supervisor: TradingSupervisor):
    st.subheader("AI Quantitative Trading Assistant")
    st.caption("Powered by local Ollama (`llama3.2:1b`). Grounded in deterministic Python indicators and risk models.")

    ollama = supervisor.llm
    conn_status = ollama.check_connection()

    # Model status telemetry banner
    if conn_status["available"] and conn_status["model_present"]:
        st.success(f"Ollama Active: Connected to local `{settings.ollama_model}` at `{settings.ollama_base_url}`.")
    elif conn_status["available"]:
        st.warning(f"Ollama running, but model `{settings.ollama_model}` is not pulled. Run `ollama pull {settings.ollama_model}`.")
    else:
        st.error(f"Cannot reach Ollama at `{settings.ollama_base_url}`. Start with `ollama serve`.")

    # Preset Quick Prompts
    st.markdown("**Quick Prompts:**")
    q_cols = st.columns(7)
    preset_prompt = None
    if q_cols[0].button("Analyze AAPL"):
        preset_prompt = "Analyze AAPL with Moving Average Crossover"
    if q_cols[1].button("Analyze TCS.NS"):
        preset_prompt = "Analyze TCS.NS with Trend Following"
    if q_cols[2].button("Explain RSI"):
        preset_prompt = "Explain how the RSI indicator works and how to avoid false oversold signals."
    if q_cols[3].button("Portfolio Risk"):
        preset_prompt = "What are the core risk limits in place to protect my paper capital?"
    if q_cols[4].button("Golden Cross?"):
        preset_prompt = "What is a Golden Cross in technical analysis?"

    # Chat history session state
    if "agent_chat_messages" not in st.session_state:
        st.session_state.agent_chat_messages = [
            {
                "role": "assistant",
                "content": (
                    "Hello! I am your Local AI Trading Assistant. "
                    "You can ask me to analyze any symbol (e.g., 'Analyze AAPL' or 'Analyze TCS.NS'), "
                    "explain technical indicators, or review risk constraints. "
                    "All numbers and signals are grounded in deterministic Python calculations."
                ),
            }
        ]

    # Render previous messages
    for msg in st.session_state.agent_chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("tool_data"):
                with st.expander("Grounded Tool Output"):
                    st.json(msg["tool_data"])

    # User chat input
    user_input = st.chat_input("Ask about an asset, strategy, or indicator...") or preset_prompt

    if user_input:
        st.session_state.agent_chat_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Check for symbol analysis intent
        symbol_match = re.search(r"\banalyze\s+([A-Za-z0-9\.\-\_\^]+)", user_input, re.IGNORECASE)
        tool_data = None
        assistant_reply = ""

        with st.chat_message("assistant"):
            with st.spinner("Analyzing with local agents..."):
                if symbol_match:
                    target_symbol = symbol_match.group(1).upper()
                    # Determine strategy if mentioned
                    strat = "Moving Average Crossover"
                    if "trend" in user_input.lower():
                        strat = "Multi-Factor Trend Following"
                    elif "rsi" in user_input.lower():
                        strat = "RSI Confirmation"

                    analysis = supervisor.run_full_analysis(target_symbol, strategy_name=strat)
                    if analysis["success"]:
                        mkt = analysis["market_research"]["observed_facts"]
                        tech = analysis["technical_analysis"]["indicators"]
                        strat_info = analysis["strategy"]
                        risk = analysis["risk_audit"]

                        tool_data = {
                            "Symbol": target_symbol,
                            "Price": mkt.get("latest_price"),
                            "Period Return": f"{mkt.get('period_return_pct')}%",
                            "RSI 14": tech.get("rsi_14"),
                            "Trend Regime": tech.get("trend_regime"),
                            "Strategy": strat_info.get("strategy_name"),
                            "Signal": strat_info.get("signal"),
                            "Stop Loss": strat_info.get("suggested_stop_loss"),
                            "Risk Approval": risk.get("status"),
                            "Max Allowed Shares": risk.get("suggested_quantity"),
                        }

                        # Construct prompt for local llama3.2:1b
                        sys_prompt = (
                            "You are an AI Trading Assistant. Synthesize the provided quantitative facts "
                            "into a clear, grounded 3-sentence summary for the user. "
                            "Explicitly mention the signal, why it was produced, and the stop-loss level. "
                            "Emphasize that this is paper trading research, not guaranteed advice."
                        )
                        u_prompt = (
                            f"Asset: {target_symbol}\n"
                            f"Latest Price: ${mkt.get('latest_price')}\n"
                            f"Signal: {strat_info.get('signal')} ({strat})\n"
                            f"Rationale: {strat_info.get('rationale')}\n"
                            f"Suggested SL: ${strat_info.get('suggested_stop_loss')}\n"
                            f"Risk Assessment: {risk.get('status')}\n"
                            "Synthesize for the trader."
                        )

                        llm_out = ollama.generate_response(sys_prompt, u_prompt)
                        if not llm_out:
                            llm_out = (
                                f"**{target_symbol}** is currently priced at **${mkt.get('latest_price'):,.2f}**.\n\n"
                                f"- **Strategy Signal:** `{strat_info.get('signal')}` ({strat})\n"
                                f"- **Rationale:** {strat_info.get('rationale')}\n"
                                f"- **Suggested Stop-Loss:** ${strat_info.get('suggested_stop_loss')}\n"
                                f"- **Risk Assessment:** {risk.get('status')} (Max safe size: {risk.get('suggested_quantity')} shares)\n\n"
                                "*Note: Market data is delayed ~15m. Paper research only.*"
                            )
                        assistant_reply = llm_out
                    else:
                        assistant_reply = f"Could not complete analysis for {target_symbol}: {analysis.get('error')}"

                else:
                    # General financial or indicator query
                    sys_prompt = (
                        "You are a helpful, professional AI Quantitative Trading Assistant. "
                        "Explain financial concepts clearly, concisely, and factually. "
                        "Remind users that past performance does not guarantee future results."
                    )
                    llm_out = ollama.generate_response(sys_prompt, user_input)
                    if not llm_out:
                        assistant_reply = (
                            "I am currently operating in offline deterministic mode. "
                            "Please ensure `ollama serve` is running with `llama3.2:1b`."
                        )
                    else:
                        assistant_reply = llm_out

            st.markdown(assistant_reply)
            if tool_data:
                with st.expander("Grounded Tool Telemetry"):
                    st.json(tool_data)

        st.session_state.agent_chat_messages.append({
            "role": "assistant",
            "content": assistant_reply,
            "tool_data": tool_data,
        })
