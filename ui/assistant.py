"""AI Trading Assistant UI: Local Ollama conversational reasoning grounded in deterministic tools."""

import re
from typing import Optional
import streamlit as st
from agents.trading_supervisor import TradingSupervisor
from agents.ollama_client import OllamaClient
from config.settings import settings
from agents.orb_tools import (
    orb_current_signal,
    explain_no_trade,
    show_today_orb,
    explain_last_signal,
    backtest_orb_csv,
    assess_orb_risk,
    journal_summary,
    generate_orb_explanation,
)


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
    q_cols = st.columns(4)
    preset_prompt = None
    if q_cols[0].button("Why no trade now?"):
        preset_prompt = "Why no trade now?"
    if q_cols[1].button("What is today's ORB?"):
        preset_prompt = "What is today's ORB?"
    if q_cols[2].button("Explain the last signal"):
        preset_prompt = "Explain the last signal"
    if q_cols[3].button("Summarize my journal"):
        preset_prompt = "Summarize my journal"

    # Chat history session state
    if "agent_chat_messages" not in st.session_state:
        st.session_state.agent_chat_messages = [
            {
                "role": "assistant",
                "content": (
                    "Hello! I am your Local AI Trading Assistant. "
                    "You can ask me questions like: \n"
                    "- *'Why no trade now?'*\n"
                    "- *'What is today's ORB?'*\n"
                    "- *'Explain the last signal'*\n"
                    "- *'Summarize my journal'*\n"
                    "All numbers and signals are grounded in deterministic Python calculations. "
                    "Educational tool, not financial advice. Alert-only."
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

        lowered = user_input.lower().strip()
        orb_intent = any(term in lowered for term in (
            "why no trade", "today's orb", "today orb", "last signal", "summarize my journal",
            "journal", "orb", "xautusd", "opening range", "backtest"
        ))
        symbol_match = re.search(r"\banalyze\s+([A-Za-z0-9\.\-\_\^]+)", user_input, re.IGNORECASE)
        tool_data = None
        assistant_reply = ""

        with st.chat_message("assistant"):
            with st.spinner("Analyzing with local agents..."):
                if orb_intent:
                    try:
                        query_type = "ORB Status"
                        if "why no trade" in lowered or "no trade" in lowered:
                            query_type = "Why No Trade Explanation"
                            tool_data = explain_no_trade()
                        elif "today" in lowered and "orb" in lowered:
                            query_type = "Today's ORB Range"
                            tool_data = show_today_orb()
                        elif "last signal" in lowered or "explain the signal" in lowered:
                            query_type = "Last Signal Rationale"
                            tool_data = explain_last_signal()
                        elif "journal" in lowered:
                            query_type = "Journal Summary"
                            tool_data = journal_summary()
                        elif "backtest" in lowered:
                            query_type = "Backtest Results"
                            csv_paths = [token.strip(" '\"(),") for token in user_input.split() if token.lower().endswith(".csv")]
                            if not csv_paths:
                                raise ValueError("For a deterministic backtest, include the local 5m CSV path, e.g. data/xautusd_5m.csv.")
                            tool_data = backtest_orb_csv(csv_paths[0])
                        elif "risk" in lowered and all(label in lowered for label in ("entry", "stop", "equity")):
                            query_type = "Risk Assessment"
                            entry_match = re.search(r"\bentry[=:\s]+\$?(\d+(?:\.\d+)?)", user_input, re.I)
                            stop_match = re.search(r"\bstop[=:\s]+\$?(\d+(?:\.\d+)?)", user_input, re.I)
                            equity_match = re.search(r"\bequity[=:\s]+\$?(\d+(?:\.\d+)?)", user_input, re.I)
                            if not (entry_match and stop_match and equity_match):
                                raise ValueError("Specify numeric entry, stop, and equity values.")
                            tool_data = assess_orb_risk(float(entry_match.group(1)), float(stop_match.group(1)), float(equity_match.group(1)))
                        else:
                            query_type = "Current ORB Signal"
                            tool_data = orb_current_signal()

                        assistant_reply = generate_orb_explanation(tool_data, query_type=query_type, ollama_client=ollama)

                    except Exception as exc:
                        assistant_reply = f"ORB strategy tool error: {exc}. No signal is assumed. Educational alert-only tool."
                        tool_data = {"direction": "NO TRADE", "reason": str(exc)}

                elif symbol_match:
                    target_symbol = symbol_match.group(1).upper()
                    strat = "Moving Average Crossover"
                    if "trend" in lowered:
                        strat = "Multi-Factor Trend Following"
                    elif "rsi" in lowered:
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

                        llm_out = ollama.generate_response(sys_prompt, u_prompt, temperature=0.2)
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
                    sys_prompt = (
                        "You are a helpful, professional AI Quantitative Trading Assistant. "
                        "Explain financial concepts clearly, concisely, and factually under 3 sentences. "
                        "Never invent price levels or guarantee returns. Educational alert-only tool."
                    )
                    llm_out = ollama.generate_response(sys_prompt, user_input, temperature=0.2)
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
