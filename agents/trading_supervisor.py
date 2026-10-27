"""Agent 6: Trading Supervisor - Orchestrates the trading research and paper-execution pipeline."""

from typing import Dict, Any, Optional
from database.repository import DatabaseRepository
from database.models import SignalType, TradeRecord
from market_data.base import BaseMarketDataProvider
from market_data.yfinance_provider import YFinanceProvider
from agents.market_research import MarketResearchAgent
from agents.technical_analysis import TechnicalAnalysisAgent
from agents.strategy_agent import StrategyAgent
from agents.risk_agent import RiskAgent
from agents.portfolio_agent import PortfolioAgent
from services.risk_manager import RiskManager
from services.paper_broker import PaperBroker
from services.portfolio_service import PortfolioService
from agents.ollama_client import OllamaClient


class TradingSupervisor:
    """Supervisory agent that coordinates specialized sub-agents through a deterministic safety workflow."""

    def __init__(
        self,
        data_provider: Optional[BaseMarketDataProvider] = None,
        repo: Optional[DatabaseRepository] = None,
        ollama_client: Optional[OllamaClient] = None,
    ):
        self.repo = repo or DatabaseRepository()
        self.data_provider = data_provider or YFinanceProvider()
        self.llm = ollama_client or OllamaClient()

        # Wire up sub-agents and deterministic services
        self.market_agent = MarketResearchAgent(self.data_provider, self.llm)
        self.tech_agent = TechnicalAnalysisAgent(self.llm)
        self.strategy_agent = StrategyAgent(ollama_client=self.llm)
        self.risk_manager = RiskManager()
        self.risk_agent = RiskAgent(self.risk_manager, self.llm)
        self.portfolio_service = PortfolioService(self.repo, self.data_provider)
        self.portfolio_agent = PortfolioAgent(self.portfolio_service, self.llm)
        self.paper_broker = PaperBroker(self.repo, self.risk_manager)

    def run_full_analysis(self, symbol: str, strategy_name: str = "Moving Average Crossover") -> Dict[str, Any]:
        """Execute full research pipeline: Market Data -> Indicators -> Strategy -> Risk."""
        clean_symbol = symbol.strip().upper()

        # Step 1: Market Research
        mkt_res = self.market_agent.research_symbol(clean_symbol)
        if mkt_res.get("status") != "success":
            return {
                "success": False,
                "stage": "market_data",
                "error": mkt_res.get("error", "Failed to retrieve market data."),
                "market_research": mkt_res,
            }

        df = mkt_res["df"]

        # Step 2: Technical Analysis
        tech_res = self.tech_agent.analyze_indicators(df, clean_symbol)
        if tech_res.get("status") != "success":
            return {
                "success": False,
                "stage": "technical_analysis",
                "error": tech_res.get("error", "Technical analysis failed."),
                "market_research": mkt_res,
                "technical_analysis": tech_res,
            }

        # Step 3: Strategy Evaluation
        strat_res = self.strategy_agent.evaluate(strategy_name, df, clean_symbol)

        # Step 4: Risk Evaluation (Proposed Sizing)
        account = self.repo.get_account_state()
        cash = account["cash_balance"]
        positions = self.repo.get_positions()
        existing_pos = self.repo.get_position(clean_symbol)
        existing_qty = existing_pos.quantity if existing_pos else 0.0
        portfolio_equity = cash + sum(p.market_value for p in positions)
        today_loss = self.repo.get_today_realized_loss()

        curr_price = strat_res["price"]
        sl = strat_res.get("suggested_stop_loss")
        tp = strat_res.get("suggested_take_profit")

        # Preliminary risk audit for 1 share or calculated sizing
        prelim_risk = self.risk_agent.audit_trade(
            symbol=clean_symbol,
            side="BUY" if strat_res["signal"] == "BUY" else "SELL",
            quantity=1.0,
            current_price=curr_price,
            portfolio_equity=portfolio_equity,
            cash_balance=cash,
            current_open_positions_count=len(positions),
            today_realized_loss=today_loss,
            stop_loss=sl,
            take_profit=tp,
            existing_position_qty=existing_qty,
        )

        suggested_qty = prelim_risk["suggested_quantity"]
        # Audit with full suggested quantity if eligible
        if suggested_qty >= 1.0 and strat_res["signal"] == "BUY":
            full_risk = self.risk_agent.audit_trade(
                symbol=clean_symbol,
                side="BUY",
                quantity=suggested_qty,
                current_price=curr_price,
                portfolio_equity=portfolio_equity,
                cash_balance=cash,
                current_open_positions_count=len(positions),
                today_realized_loss=today_loss,
                stop_loss=sl,
                take_profit=tp,
                existing_position_qty=existing_qty,
            )
        else:
            full_risk = prelim_risk

        return {
            "success": True,
            "symbol": clean_symbol,
            "strategy_name": strategy_name,
            "market_research": mkt_res,
            "technical_analysis": tech_res,
            "strategy": strat_res,
            "risk_audit": full_risk,
            "eligible_for_trade": (strat_res["signal"] in ["BUY", "SELL"]) and full_risk["approved"],
            "requires_user_confirmation": True,
        }

    def execute_confirmed_paper_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_name: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Execute a simulated paper trade strictly AFTER explicit user confirmation."""
        success, message, trade = self.paper_broker.execute_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            current_price=price,
            strategy_name=strategy_name,
            stop_loss=stop_loss,
            take_profit=take_profit,
            notes=notes,
            bypass_risk=False,  # HARD RULE: Cannot bypass risk checks
        )

        return {
            "success": success,
            "message": message,
            "trade": trade,
        }
