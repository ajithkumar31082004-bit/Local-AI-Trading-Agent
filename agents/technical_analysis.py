"""Agent 2: Technical Analysis Agent - Computes Python indicators and formats technical synthesis."""

from typing import Dict, Any, Optional
import pandas as pd
from services.indicators import get_latest_indicators, IndicatorSnapshot
from agents.ollama_client import OllamaClient


class TechnicalAnalysisAgent:
    """Agent responsible for technical indicators computation and factual explanation."""

    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.llm = ollama_client or OllamaClient()

    def analyze_indicators(self, df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """Compute all technical indicators deterministically in Python and summarize."""
        snapshot: Optional[IndicatorSnapshot] = get_latest_indicators(df)

        if not snapshot:
            return {
                "symbol": symbol,
                "status": "error",
                "error": "Insufficient candles for technical indicators calculation.",
                "indicators": {},
                "summary": "No indicators available.",
            }

        # Deterministic dictionary of values
        ind_dict = {
            "price": snapshot.price,
            "sma_20": snapshot.sma_20,
            "sma_50": snapshot.sma_50,
            "ema_12": snapshot.ema_12,
            "ema_26": snapshot.ema_26,
            "rsi_14": snapshot.rsi_14,
            "macd": snapshot.macd,
            "macd_signal": snapshot.macd_signal,
            "macd_hist": snapshot.macd_hist,
            "bb_upper": snapshot.bb_upper,
            "bb_middle": snapshot.bb_middle,
            "bb_lower": snapshot.bb_lower,
            "atr_14": snapshot.atr_14,
            "volume_ratio": snapshot.volume_ratio,
            "support": snapshot.support,
            "resistance": snapshot.resistance,
            "trend_regime": snapshot.trend_description,
        }

        # Ask local llama3.2:1b for technical narrative
        sys_prompt = (
            "You are a Chartered Market Technician (CMT). "
            "Explain what the technical indicators show factually in 2-3 sentences. "
            "Never guess or fabricate numbers; reference only the provided indicator levels."
        )
        user_prompt = (
            f"Asset: {symbol}\n"
            f"Price: ${snapshot.price:.2f}\n"
            f"RSI(14): {snapshot.rsi_14:.1f}\n"
            f"EMA(12/26): {snapshot.ema_12:.2f} / {snapshot.ema_26:.2f}\n"
            f"MACD Histogram: {snapshot.macd_hist:.3f}\n"
            f"Support/Resistance: [${snapshot.support:.2f} - ${snapshot.resistance:.2f}]\n"
            f"Trend Regime: {snapshot.trend_description}\n"
            "Summarize the technical posture."
        )

        llm_summary = self.llm.generate_response(sys_prompt, user_prompt, max_tokens=150)
        if not llm_summary:
            # Deterministic fallback narrative
            llm_summary = (
                f"{symbol} is trading at ${snapshot.price:.2f}. "
                f"RSI(14) is at {snapshot.rsi_14:.1f}, placing momentum in the "
                f"{'oversold' if (snapshot.rsi_14 or 50) < 30 else 'overbought' if (snapshot.rsi_14 or 50) > 70 else 'neutral'} range. "
                f"Trend posture: {snapshot.trend_description}. Support sits at ${snapshot.support:.2f} and resistance at ${snapshot.resistance:.2f}."
            )

        return {
            "symbol": symbol,
            "status": "success",
            "snapshot": snapshot,
            "indicators": ind_dict,
            "ai_summary": llm_summary,
        }
