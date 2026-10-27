"""Agent 5: Portfolio Agent - Tracks capital, performance, positions, and drawdown."""

from typing import Dict, Any, Optional
from services.portfolio_service import PortfolioService, PortfolioSummary
from agents.ollama_client import OllamaClient


class PortfolioAgent:
    """Agent responsible for portfolio accounting, equity health, and performance reporting."""

    def __init__(self, portfolio_service: Optional[PortfolioService] = None, ollama_client: Optional[OllamaClient] = None):
        self.portfolio_service = portfolio_service or PortfolioService()
        self.llm = ollama_client or OllamaClient()

    def get_portfolio_report(self) -> Dict[str, Any]:
        """Generate high-level portfolio overview and LLM performance commentary."""
        summary: PortfolioSummary = self.portfolio_service.get_summary()

        sys_prompt = (
            "You are a Portfolio Manager. "
            "Summarize the account health and performance in 2 sentences. "
            "Highlight cash availability, drawdown, and win rate factually."
        )
        user_prompt = (
            f"Total Equity: ${summary.total_equity:,.2f}\n"
            f"Cash: ${summary.cash_balance:,.2f}\n"
            f"Unrealized P&L: ${summary.unrealized_pnl:+,.2f} ({summary.unrealized_pnl_pct:+.2f}%)\n"
            f"Realized P&L: ${summary.realized_pnl:+,.2f}\n"
            f"Drawdown: {summary.drawdown_pct:.2f}%\n"
            f"Open Positions: {len(summary.open_positions)}\n"
            f"Win Rate: {summary.win_rate_pct:.1f}% (Trades: {summary.total_trades_count})\n"
            "Summarize the portfolio stance."
        )

        llm_exp = self.llm.generate_response(sys_prompt, user_prompt, max_tokens=150)
        if not llm_exp:
            llm_exp = (
                f"Portfolio equity is ${summary.total_equity:,.2f} with ${summary.cash_balance:,.2f} in liquid cash. "
                f"Current drawdown is {summary.drawdown_pct:.2f}% across {len(summary.open_positions)} open positions, "
                f"with a win rate of {summary.win_rate_pct:.1f}% over {summary.total_trades_count} total trades."
            )

        return {
            "summary": summary,
            "ai_commentary": llm_exp,
        }
