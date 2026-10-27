"""Local AI Trading Agent - Main Streamlit Application."""

import streamlit as st
from config.settings import settings
from database.repository import DatabaseRepository
from market_data.yfinance_provider import YFinanceProvider
from agents.trading_supervisor import TradingSupervisor
from agents.ollama_client import OllamaClient
from ui.dashboard import render_dashboard
from ui.market_watch import render_market_watch
from ui.signals import render_signals
from ui.paper_trading import render_paper_trading
from ui.backtesting import render_backtesting
from ui.risk_settings import render_risk_settings
from ui.assistant import render_assistant

# Page Config
st.set_page_config(
    page_title="Local AI Trading Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_shared_resources():
    """Initialize singleton instances of database, market data feed, and supervisor."""
    repo = DatabaseRepository()
    data_provider = YFinanceProvider(cache_ttl_minutes=settings.data_cache_expiry_minutes)
    ollama_client = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )
    supervisor = TradingSupervisor(
        data_provider=data_provider,
        repo=repo,
        ollama_client=ollama_client,
    )
    return repo, data_provider, supervisor


def main():
    repo, data_provider, supervisor = get_shared_resources()

    # Sidebar Navigation
    st.sidebar.title("📈 AI Trading Agent")
    st.sidebar.markdown(
        "<div style='background-color: #1E3A8A; color: white; padding: 6px 12px; border-radius: 6px; font-weight: bold; text-align: center; margin-bottom: 12px;'>"
        "🛡️ PAPER TRADING ONLY</div>",
        unsafe_allow_html=True,
    )

    # Navigation Selection
    menu_options = [
        "Dashboard",
        "Market Watch",
        "AI Assistant",
        "Trading Signals",
        "Paper Trading",
        "Strategy Backtesting",
        "Risk Settings",
    ]

    choice = st.sidebar.radio("Navigation", menu_options, index=0)

    st.sidebar.markdown("---")

    # System Status in Sidebar
    st.sidebar.markdown("### System Telemetry")
    account = repo.get_account_state()
    st.sidebar.metric("Cash Balance", f"${account['cash_balance']:,.2f}")

    ollama_status = supervisor.llm.check_connection()
    if ollama_status["available"] and ollama_status["model_present"]:
        st.sidebar.success(f"Ollama: `{settings.ollama_model}` Online")
    elif ollama_status["available"]:
        st.sidebar.warning(f"Ollama: Model `{settings.ollama_model}` Missing")
    else:
        st.sidebar.error("Ollama: Disconnected")

    # Supabase Cloud Status
    if repo.supabase and repo.supabase.enabled:
        sb_status = repo.supabase.check_connection()
        if sb_status["available"]:
            st.sidebar.success("Supabase: Cloud Sync Active")
        else:
            st.sidebar.info("Supabase: Configured (Local fallback)")

    st.sidebar.caption(f"Data Provider: {data_provider.provider_name}")
    st.sidebar.caption("Orders recorded in local SQLite with optional Supabase sync.")

    # Render selected view
    if choice == "Dashboard":
        render_dashboard(repo, data_provider)
    elif choice == "Market Watch":
        render_market_watch(data_provider)
    elif choice == "AI Assistant":
        render_assistant(supervisor)
    elif choice == "Trading Signals":
        render_signals(data_provider)
    elif choice == "Paper Trading":
        render_paper_trading(repo, data_provider)
    elif choice == "Strategy Backtesting":
        render_backtesting(data_provider)
    elif choice == "Risk Settings":
        render_risk_settings(repo)


if __name__ == "__main__":
    main()
