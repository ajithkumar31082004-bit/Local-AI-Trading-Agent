"""Agent 4: Risk Management Agent - Evaluates and enforces portfolio risk constraints."""

from typing import Dict, Any, Optional
from services.risk_manager import RiskManager, RiskCheckResult
from agents.ollama_client import OllamaClient


class RiskAgent:
    """Agent responsible for auditing orders against hard risk controls and explaining approvals or rejections."""

    def __init__(self, risk_manager: Optional[RiskManager] = None, ollama_client: Optional[OllamaClient] = None):
        self.risk_manager = risk_manager or RiskManager()
        self.llm = ollama_client or OllamaClient()

    def audit_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        current_price: float,
        portfolio_equity: float,
        cash_balance: float,
        current_open_positions_count: int,
        today_realized_loss: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        existing_position_qty: float = 0.0,
    ) -> Dict[str, Any]:
        """Perform deterministic risk assessment and generate explanatory risk advisory."""
        res: RiskCheckResult = self.risk_manager.evaluate_order(
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            current_price=current_price,
            portfolio_equity=portfolio_equity,
            cash_balance=cash_balance,
            current_open_positions_count=current_open_positions_count,
            today_realized_loss=today_realized_loss,
            stop_loss=stop_loss,
            take_profit=take_profit,
            existing_position_qty=existing_position_qty,
        )

        status_str = "APPROVED" if res.approved else "REJECTED"
        details_str = "; ".join(res.rejection_reasons) if not res.approved else (
            f"Position size ${res.position_value:,.2f} is within limits. Estimated fee: ${res.estimated_fee:.2f}, "
            f"slippage: ${res.estimated_slippage:.2f}, dollar risk: ${res.dollar_risk:,.2f}."
        )

        sys_prompt = (
            "You are a Chief Financial Risk Officer. "
            "Explain whether the trade is approved or rejected in 2 direct sentences. "
            "Highlight why capital preservation and risk constraints were or were not satisfied."
        )
        user_prompt = (
            f"Trade Status: {status_str}\n"
            f"Order: {side} {quantity} {symbol} @ ${res.effective_price:.2f}\n"
            f"Details: {details_str}\n"
            f"Warnings: {'; '.join(res.warning_notes)}\n"
            "State your risk assessment."
        )

        llm_exp = self.llm.generate_response(sys_prompt, user_prompt, max_tokens=150)
        explanation = llm_exp if llm_exp else f"Risk Check {status_str}: {details_str}"

        return {
            "approved": res.approved,
            "status": status_str,
            "rejection_reasons": res.rejection_reasons,
            "suggested_quantity": res.suggested_quantity,
            "max_allowed_quantity": res.max_allowed_quantity,
            "dollar_risk": res.dollar_risk,
            "position_value": res.position_value,
            "estimated_fee": res.estimated_fee,
            "estimated_slippage": res.estimated_slippage,
            "effective_price": res.effective_price,
            "stop_loss": res.stop_loss,
            "take_profit": res.take_profit,
            "risk_reward_ratio": res.risk_reward_ratio,
            "warning_notes": res.warning_notes,
            "ai_commentary": explanation,
            "result_object": res,
        }
