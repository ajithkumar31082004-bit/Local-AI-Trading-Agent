"""Agent 1: Market Research Agent - Analyzes market trends, prices, and facts vs interpretations."""

from typing import Dict, Any, Optional
from market_data.base import BaseMarketDataProvider, MarketDataResult
from agents.ollama_client import OllamaClient


class MarketResearchAgent:
    """Agent responsible for market data fact retrieval, trend analysis, and commentary."""

    def __init__(self, data_provider: BaseMarketDataProvider, ollama_client: Optional[OllamaClient] = None):
        self.data_provider = data_provider
        self.llm = ollama_client or OllamaClient()

    def research_symbol(self, symbol: str, period: str = "6mo", interval: str = "1d") -> Dict[str, Any]:
        """Retrieve market facts and generate structured analysis."""
        clean_symbol = symbol.strip().upper()
        res: MarketDataResult = self.data_provider.get_historical_ohlcv(clean_symbol, period=period, interval=interval)

        if not res.is_valid:
            return {
                "symbol": clean_symbol,
                "status": "error",
                "error": res.error or "Failed to retrieve market data.",
                "observed_facts": {},
                "ai_commentary": f"Could not analyze {clean_symbol} because market data could not be retrieved.",
            }

        df = res.df
        first_close = float(df["Close"].iloc[0])
        last_close = float(df["Close"].iloc[-1])
        period_high = float(df["High"].max())
        period_low = float(df["Low"].min())
        avg_volume = float(df["Volume"].mean())
        last_volume = float(df["Volume"].iloc[-1])
        pct_change = round(((last_close - first_close) / first_close) * 100.0, 2)

        observed_facts = {
            "symbol": clean_symbol,
            "provider": res.meta.provider,
            "latest_timestamp": res.meta.latest_timestamp,
            "latest_price": round(last_close, 2),
            "period_high": round(period_high, 2),
            "period_low": round(period_low, 2),
            "period_return_pct": pct_change,
            "average_volume": int(avg_volume),
            "latest_volume": int(last_volume),
            "warning": res.meta.warning,
        }

        # Prompt local llama3.2:1b for concise explanation
        sys_prompt = (
            "You are a factual Quantitative Market Research Analyst. "
            "Explain market conditions concisely in 2-3 sentences based strictly on the provided numbers. "
            "Clearly distinguish observed market facts from interpretation. Do not fabricate prices."
        )
        user_prompt = (
            f"Symbol: {clean_symbol}\n"
            f"Latest Price: ${last_close:.2f}\n"
            f"Period Return: {pct_change}%\n"
            f"Range: [${period_low:.2f} - ${period_high:.2f}]\n"
            f"Latest Volume: {int(last_volume):,} (Avg: {int(avg_volume):,})\n"
            "Summarize the market structure and facts succinctly."
        )

        ai_commentary = self.llm.generate_response(sys_prompt, user_prompt, max_tokens=150)
        if not ai_commentary:
            # Deterministic fallback
            ai_commentary = (
                f"{clean_symbol} closed at ${last_close:,.2f}, representing a {pct_change:+.2f}% change "
                f"over the observed period within a price boundary of ${period_low:,.2f} to ${period_high:,.2f}. "
                f"Volume was {int(last_volume):,} compared to average {int(avg_volume):,}."
            )

        return {
            "symbol": clean_symbol,
            "status": "success",
            "observed_facts": observed_facts,
            "ai_commentary": ai_commentary,
            "df": df,
        }
