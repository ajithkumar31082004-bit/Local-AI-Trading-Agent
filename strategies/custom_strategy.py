"""User-configurable custom strategy."""

from typing import Dict, Any, List
import pandas as pd
from strategies.base import BaseStrategy
from database.models import StrategySignal, SignalType
from services.indicators import (
    compute_sma,
    compute_ema,
    compute_rsi,
    compute_bollinger_bands,
    compute_atr,
)


class CustomStrategy(BaseStrategy):
    """Dynamically configured strategy allowing user to toggle and tune indicator rules."""

    def __init__(
        self,
        use_sma_trend: bool = True,
        sma_period: int = 50,
        use_rsi: bool = True,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
        use_bb: bool = False,
        stop_loss_pct: float = 2.5,
        take_profit_pct: float = 5.0,
    ):
        self.use_sma_trend = use_sma_trend
        self.sma_period = sma_period
        self.use_rsi = use_rsi
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.use_bb = use_bb
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    @property
    def name(self) -> str:
        return "Custom Flexible Strategy"

    @property
    def description(self) -> str:
        return "User-customized rule set combining configurable moving averages, RSI, and Bollinger bands."

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "use_sma_trend": self.use_sma_trend,
            "sma_period": self.sma_period,
            "use_rsi": self.use_rsi,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "use_bb": self.use_bb,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
        }

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> StrategySignal:
        if df.empty or len(df) < max(self.sma_period, 25):
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.NO_SIGNAL,
                rationale="Insufficient candles for custom strategy parameters.",
            )

        close = df["Close"]
        current_price = float(close.iloc[-1])
        buy_conditions_met = []
        sell_conditions_met = []
        indicators: Dict[str, Any] = {"price": current_price}

        # 1. Trend Filter
        if self.use_sma_trend:
            sma = compute_sma(close, self.sma_period)
            curr_sma = float(sma.iloc[-1])
            indicators["sma"] = curr_sma
            if current_price > curr_sma:
                buy_conditions_met.append(f"Price ({current_price:.2f}) > SMA{self.sma_period} ({curr_sma:.2f})")
            else:
                sell_conditions_met.append(f"Price ({current_price:.2f}) < SMA{self.sma_period} ({curr_sma:.2f})")

        # 2. RSI
        if self.use_rsi:
            rsi = compute_rsi(close, 14)
            curr_rsi = float(rsi.iloc[-1])
            indicators["rsi"] = curr_rsi
            if curr_rsi <= self.rsi_oversold:
                buy_conditions_met.append(f"RSI ({curr_rsi:.1f}) is below oversold threshold ({self.rsi_oversold})")
            elif curr_rsi >= self.rsi_overbought:
                sell_conditions_met.append(f"RSI ({curr_rsi:.1f}) is above overbought threshold ({self.rsi_overbought})")

        # 3. Bollinger Bands
        if self.use_bb:
            upper, mid, lower = compute_bollinger_bands(close, 20, 2.0)
            curr_lower = float(lower.iloc[-1])
            curr_upper = float(upper.iloc[-1])
            indicators["bb_lower"] = curr_lower
            indicators["bb_upper"] = curr_upper
            if current_price <= curr_lower:
                buy_conditions_met.append("Price touches lower Bollinger Band")
            elif current_price >= curr_upper:
                sell_conditions_met.append("Price touches upper Bollinger Band")

        # Determine signal based on evaluated rules
        if buy_conditions_met and not sell_conditions_met:
            sl = round(current_price * (1 - (self.stop_loss_pct / 100.0)), 2)
            tp = round(current_price * (1 + (self.take_profit_pct / 100.0)), 2)
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.BUY,
                strength=0.75,
                price=current_price,
                indicators=indicators,
                rationale="Custom BUY criteria satisfied: " + "; ".join(buy_conditions_met),
                invalidation_criteria=f"Breach of stop-loss at {sl}.",
                suggested_stop_loss=sl,
                suggested_take_profit=tp,
            )
        elif sell_conditions_met and not buy_conditions_met:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.SELL,
                strength=0.75,
                price=current_price,
                indicators=indicators,
                rationale="Custom SELL criteria satisfied: " + "; ".join(sell_conditions_met),
                invalidation_criteria="Re-entry of bullish indicator zone.",
            )

        return StrategySignal(
            symbol=symbol,
            strategy_name=self.name,
            signal=SignalType.HOLD,
            price=current_price,
            indicators=indicators,
            rationale="Custom criteria mixed or in neutral zone. Holding.",
            invalidation_criteria="N/A",
        )
