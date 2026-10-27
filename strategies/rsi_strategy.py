"""RSI Mean Reversion with Confirmation Strategy."""

from typing import Dict, Any
import pandas as pd
from strategies.base import BaseStrategy
from database.models import StrategySignal, SignalType
from services.indicators import compute_rsi, compute_atr, compute_sma


class RSIStrategy(BaseStrategy):
    """RSI Strategy requiring momentum confirmation to avoid catching falling knives."""

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        require_volume_confirmation: bool = True,
    ):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.require_volume_confirmation = require_volume_confirmation

    @property
    def name(self) -> str:
        return "RSI Confirmation"

    @property
    def description(self) -> str:
        return (
            f"Evaluates {self.period}-period RSI. Triggers BUY only when RSI reverses upward "
            f"across oversold threshold ({self.oversold}). Triggers SELL when RSI crosses below "
            f"overbought threshold ({self.overbought}). Does NOT assume an oversold asset must rise."
        )

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "period": self.period,
            "oversold": self.oversold,
            "overbought": self.overbought,
            "require_volume_confirmation": self.require_volume_confirmation,
        }

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> StrategySignal:
        if df.empty or len(df) < self.period + 5:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.NO_SIGNAL,
                rationale=f"Insufficient candles ({len(df)}) for RSI calculation period {self.period}.",
            )

        rsi = compute_rsi(df["Close"], self.period)
        atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
        vol_sma = compute_sma(df["Volume"], 20)

        current_price = float(df["Close"].iloc[-1])
        curr_rsi = float(rsi.iloc[-1])
        prev_rsi = float(rsi.iloc[-2])
        curr_atr = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else (current_price * 0.02)
        curr_vol = float(df["Volume"].iloc[-1])
        avg_vol = float(vol_sma.iloc[-1]) if pd.notna(vol_sma.iloc[-1]) else curr_vol

        # Confirmation condition: Volume above 80% of 20-day average if required
        volume_ok = (curr_vol >= avg_vol * 0.8) if self.require_volume_confirmation else True

        # Bullish recovery: crossed back above oversold threshold
        if prev_rsi <= self.oversold and curr_rsi > self.oversold and volume_ok:
            stop_loss = max(0.01, round(current_price - (curr_atr * 1.5), 2))
            take_profit = round(current_price + (curr_atr * 3.0), 2)
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.BUY,
                strength=0.8,
                price=current_price,
                indicators={
                    "rsi": curr_rsi,
                    "prev_rsi": prev_rsi,
                    "oversold": self.oversold,
                    "volume": curr_vol,
                    "volume_avg": avg_vol,
                },
                rationale=(
                    f"RSI Bullish Rebound: RSI recovered from {prev_rsi:.1f} <= {self.oversold} "
                    f"to {curr_rsi:.1f} with confirmed volume turnover."
                ),
                invalidation_criteria=f"Signal invalidated if RSI slips back below {self.oversold} or price breaches SL {stop_loss}.",
                suggested_stop_loss=stop_loss,
                suggested_take_profit=take_profit,
            )

        # Bearish exhaust: crossed back below overbought threshold
        elif prev_rsi >= self.overbought and curr_rsi < self.overbought:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.SELL,
                strength=0.8,
                price=current_price,
                indicators={
                    "rsi": curr_rsi,
                    "prev_rsi": prev_rsi,
                    "overbought": self.overbought,
                },
                rationale=(
                    f"RSI Bearish Exhaustion: RSI dipped below overbought ceiling "
                    f"({prev_rsi:.1f} down to {curr_rsi:.1f}). Take profit or reduce long exposure."
                ),
                invalidation_criteria="Invalidated if RSI surges back above overbought ceiling.",
            )

        # Deep oversold without reversal yet
        if curr_rsi <= self.oversold:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.HOLD,
                price=current_price,
                indicators={"rsi": curr_rsi},
                rationale=f"RSI is oversold ({curr_rsi:.1f}), but momentum has NOT confirmed an upward reversal yet. Waiting.",
                invalidation_criteria="N/A",
            )

        # Deep overbought without reversal yet
        if curr_rsi >= self.overbought:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.HOLD,
                price=current_price,
                indicators={"rsi": curr_rsi},
                rationale=f"RSI is overbought ({curr_rsi:.1f}). Strong upward momentum persists; awaiting exit trigger.",
                invalidation_criteria="N/A",
            )

        return StrategySignal(
            symbol=symbol,
            strategy_name=self.name,
            signal=SignalType.HOLD,
            price=current_price,
            indicators={"rsi": curr_rsi},
            rationale=f"RSI is in neutral territory at {curr_rsi:.1f}. No actionable reversal signal.",
            invalidation_criteria="N/A",
        )
