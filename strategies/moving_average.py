"""Moving Average Crossover Strategy."""

from typing import Dict, Any
import pandas as pd
from strategies.base import BaseStrategy
from database.models import StrategySignal, SignalType
from services.indicators import compute_ema, compute_atr


class MovingAverageCrossoverStrategy(BaseStrategy):
    """Generates signals based on EMA fast/slow crossovers with ATR-based stop levels."""

    def __init__(self, fast_period: int = 12, slow_period: int = 26, atr_multiplier: float = 2.0):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.atr_multiplier = atr_multiplier

    @property
    def name(self) -> str:
        return "Moving Average Crossover"

    @property
    def description(self) -> str:
        return (
            f"Bullish when {self.fast_period}-EMA crosses above {self.slow_period}-EMA. "
            f"Bearish when {self.fast_period}-EMA crosses below {self.slow_period}-EMA. "
            "Requires confirmation of previous candle state to prevent false whipsaws."
        )

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "atr_multiplier": self.atr_multiplier,
        }

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> StrategySignal:
        if df.empty or len(df) < self.slow_period + 2:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.NO_SIGNAL,
                rationale=f"Insufficient candles ({len(df)}) for slow EMA period ({self.slow_period}).",
            )

        fast_ema = compute_ema(df["Close"], self.fast_period)
        slow_ema = compute_ema(df["Close"], self.slow_period)
        atr = compute_atr(df["High"], df["Low"], df["Close"], 14)

        current_price = float(df["Close"].iloc[-1])
        curr_fast = float(fast_ema.iloc[-1])
        curr_slow = float(slow_ema.iloc[-1])
        prev_fast = float(fast_ema.iloc[-2])
        prev_slow = float(slow_ema.iloc[-2])
        curr_atr = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else (current_price * 0.02)

        # Bullish Crossover: prev_fast <= prev_slow and curr_fast > curr_slow
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            stop_loss = max(0.01, round(current_price - (curr_atr * self.atr_multiplier), 2))
            take_profit = round(current_price + (curr_atr * self.atr_multiplier * 2.0), 2)
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.BUY,
                strength=0.85,
                price=current_price,
                indicators={
                    "fast_ema": curr_fast,
                    "slow_ema": curr_slow,
                    "prev_fast": prev_fast,
                    "prev_slow": prev_slow,
                    "atr": curr_atr,
                },
                rationale=(
                    f"Golden Cross detected: Fast EMA ({self.fast_period}) crossed above "
                    f"Slow EMA ({self.slow_period}) from {prev_fast:.2f} <= {prev_slow:.2f} to {curr_fast:.2f} > {curr_slow:.2f}."
                ),
                invalidation_criteria=f"Signal invalidated if price falls below stop-loss at {stop_loss} or fast EMA falls back below slow EMA.",
                suggested_stop_loss=stop_loss,
                suggested_take_profit=take_profit,
            )

        # Bearish Crossover: prev_fast >= prev_slow and curr_fast < curr_slow
        elif prev_fast >= prev_slow and curr_fast < curr_slow:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.SELL,
                strength=0.85,
                price=current_price,
                indicators={
                    "fast_ema": curr_fast,
                    "slow_ema": curr_slow,
                    "prev_fast": prev_fast,
                    "prev_slow": prev_slow,
                    "atr": curr_atr,
                },
                rationale=(
                    f"Death Cross detected: Fast EMA ({self.fast_period}) crossed below "
                    f"Slow EMA ({self.slow_period}) from {prev_fast:.2f} >= {prev_slow:.2f} to {curr_fast:.2f} < {curr_slow:.2f}."
                ),
                invalidation_criteria="Signal invalidated if fast EMA immediately regains position above slow EMA.",
            )

        # In steady state
        if curr_fast > curr_slow:
            state = "Fast EMA remains above Slow EMA (Existing Bullish Regime)."
            ref_sl = max(0.01, round(current_price - (curr_atr * self.atr_multiplier), 2))
            ref_tp = round(current_price + (curr_atr * self.atr_multiplier * 2.0), 2)
        else:
            state = "Fast EMA remains below Slow EMA (Existing Bearish Regime)."
            ref_sl = None
            ref_tp = None

        return StrategySignal(
            symbol=symbol,
            strategy_name=self.name,
            signal=SignalType.HOLD,
            strength=0.5,
            price=current_price,
            indicators={
                "fast_ema": curr_fast,
                "slow_ema": curr_slow,
            },
            rationale=f"No new crossover on latest bar. {state}",
            invalidation_criteria="N/A (Holding position or waiting for fresh crossover).",
            suggested_stop_loss=ref_sl,
            suggested_take_profit=ref_tp,
        )
