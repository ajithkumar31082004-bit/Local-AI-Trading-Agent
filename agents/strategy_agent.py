"""Agent 3: Strategy Agent - Evaluates strategies and produces explainable signals."""

from typing import Dict, Any, Optional
import pandas as pd
from database.models import StrategySignal, SignalType
from services.signal_engine import SignalEngine
from agents.ollama_client import OllamaClient


class StrategyAgent:
    """Agent that runs deterministic strategy algorithms and crafts clear signal explanations."""

    def __init__(self, signal_engine: Optional[SignalEngine] = None, ollama_client: Optional[OllamaClient] = None):
        self.signal_engine = signal_engine or SignalEngine()
        self.llm = ollama_client or OllamaClient()

    def evaluate(self, strategy_name: str, df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """Compute signal deterministically in Python and generate transparent rationale."""
        signal: StrategySignal = self.signal_engine.evaluate_strategy(strategy_name, df, symbol)

        # Ask local llama3.2:1b to explain rules and invalidation criteria concisely
        sys_prompt = (
            "You are a Quantitative Strategy Evaluator. "
            "Explain the strategy signal in 2 clear sentences. "
            "State the deterministic rule triggered, the invalidation conditions, and make clear outcomes are not guaranteed."
        )
        user_prompt = (
            f"Strategy: {signal.strategy_name}\n"
            f"Symbol: {signal.symbol}\n"
            f"Signal: {signal.signal.value}\n"
            f"Price: ${signal.price:.2f}\n"
            f"Rule Output: {signal.rationale}\n"
            f"Invalidation: {signal.invalidation_criteria}\n"
            f"SL/TP: {signal.suggested_stop_loss} / {signal.suggested_take_profit}\n"
            "Provide the strategy summary."
        )

        llm_exp = self.llm.generate_response(sys_prompt, user_prompt, max_tokens=150)
        explanation = llm_exp if llm_exp else signal.rationale

        return {
            "symbol": signal.symbol,
            "strategy_name": signal.strategy_name,
            "signal": signal.signal.value,
            "price": signal.price,
            "suggested_stop_loss": signal.suggested_stop_loss,
            "suggested_take_profit": signal.suggested_take_profit,
            "indicators": signal.indicators,
            "rationale": explanation,
            "invalidation_criteria": signal.invalidation_criteria,
            "signal_object": signal,
        }
