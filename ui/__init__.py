"""UI package for Streamlit trading dashboard."""

from ui.dashboard import render_dashboard
from ui.market_watch import render_market_watch
from ui.signals import render_signals
from ui.paper_trading import render_paper_trading
from ui.backtesting import render_backtesting
from ui.risk_settings import render_risk_settings
from ui.assistant import render_assistant

__all__ = [
    "render_dashboard",
    "render_market_watch",
    "render_signals",
    "render_paper_trading",
    "render_backtesting",
    "render_risk_settings",
    "render_assistant",
]
