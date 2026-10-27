"""Multi-Factor Trend Following Strategy."""

from typing import Dict, Any
import pandas as pd
from strategies.base import BaseStrategy
from database.models import StrategySignal, SignalType
from services.indicators import (
    compute_sma,
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_atr,
)


class TrendFollowingStrategy(BaseStrategy):
    """Trend following combining moving average structure, RSI momentum, and volume expansion."""

    def __init__(
        self,
        trend_ma_period: int = 50,
        rsi_min: float = 50.0,
        rsi_max: float = 70.0,
        volume_expansion_factor: float = 1.05,
    ):
        self.trend_ma_period = trend_ma_period
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max
        self.volume_expansion_factor = volume_expansion_factor

    @property
    def name(self) -> str:
        return "Multi-Factor Trend Following"

    @property
    def description(self) -> str:
        return (
            f"Requires price > {self.trend_ma_period}-SMA, EMA(12) > EMA(26), "
            f"RSI between {self.rsi_min} and {self.rsi_max}, positive MACD histogram, "
            f"and volume > {self.volume_expansion_factor}x 20-day average."
        )

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "trend_ma_period": self.trend_ma_period,
            "rsi_min": self.rsi_min,
            "rsi_max": self.rsi_max,
            "volume_expansion_factor": self.volume_expansion_factor,
        }

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> StrategySignal:
        if df.empty or len(df) < self.trend_ma_period + 5:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.NO_SIGNAL,
                rationale=f"Insufficient candles ({len(df)}) for trend filter ({self.trend_ma_period}).",
            )

        close = df["Close"]
        sma_trend = compute_sma(close, self.trend_ma_period)
        ema_12 = compute_ema(close, 12)
        ema_26 = compute_ema(close, 26)
        rsi = compute_rsi(close, 14)
        _, _, macd_hist = compute_macd(close, 12, 26, 9)
        vol_sma = compute_sma(df["Volume"], 20)
        atr = compute_atr(df["High"], df["Low"], close, 14)

        current_price = float(close.iloc[-1])
        curr_trend_ma = float(sma_trend.iloc[-1])
        curr_ema12 = float(ema_12.iloc[-1])
        curr_ema26 = float(ema_26.iloc[-1])
        curr_rsi = float(rsi.iloc[-1])
        curr_hist = float(macd_hist.iloc[-1])
        curr_vol = float(df["Volume"].iloc[-1])
        curr_vol_sma = float(vol_sma.iloc[-1]) if pd.notna(vol_sma.iloc[-1]) else curr_vol
        curr_atr = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else (current_price * 0.02)

        # Conditions
        trend_bullish = current_price > curr_trend_ma and curr_ema12 > curr_ema26
        momentum_bullish = self.rsi_min <= curr_rsi <= self.rsi_max and curr_hist > 0
        volume_confirmed = curr_vol >= (curr_vol_sma * self.volume_expansion_factor)

        if trend_bullish and momentum_bullish and volume_confirmed:
            stop_loss = max(0.01, round(max(curr_trend_ma, current_price - (curr_atr * 2.0)), 2))
            take_profit = round(current_price + (curr_atr * 4.0), 2)
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.BUY,
                strength=0.9,
                price=current_price,
                indicators={
                    "price": current_price,
                    "trend_ma": curr_trend_ma,
                    "ema_12": curr_ema12,
                    "ema_26": curr_ema26,
                    "rsi": curr_rsi,
                    "macd_hist": curr_hist,
                    "volume_ratio": round(curr_vol / (curr_vol_sma or 1.0), 2),
                },
                rationale=(
                    f"Confluence of Trend, Momentum & Volume: Price ({current_price:.2f}) > "
                    f"SMA{self.trend_ma_period} ({curr_trend_ma:.2f}), RSI is healthy at {curr_rsi:.1f}, "
                    f"MACD histogram positive ({curr_hist:.3f}), and volume expanded {curr_vol/curr_vol_sma:.1f}x."
                ),
                invalidation_criteria=f"Invalidated if price closes below trend support {stop_loss}.",
                suggested_stop_loss=stop_loss,
                suggested_take_profit=take_profit,
            )

        # Exit / Sell condition: price broke below trend MA or EMA12 fell below EMA26
        elif current_price < curr_trend_ma or curr_ema12 < curr_ema26:
            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                signal=SignalType.SELL,
                strength=0.8,
                price=current_price,
                indicators={
                    "price": current_price,
                    "trend_ma": curr_trend_ma,
                    "ema_12": curr_ema12,
                    "ema_26": curr_ema26,
                },
                rationale=f"Trend breakdown: Price {current_price:.2f} is under SMA{self.trend_ma_period} or EMA12 < EMA26.",
                invalidation_criteria="Invalidated if price recovers above trend moving average.",
            )

        ref_sl = max(0.01, round(max(curr_trend_ma, current_price - (curr_atr * 2.0)), 2)) if current_price > curr_trend_ma else None
        return StrategySignal(
            symbol=symbol,
            strategy_name=self.name,
            signal=SignalType.HOLD,
            price=current_price,
            indicators={"rsi": curr_rsi, "trend_ma": curr_trend_ma},
            rationale="Trend conditions incomplete or awaiting volume confirmation. Holding / waiting.",
            invalidation_criteria="N/A",
            suggested_stop_loss=ref_sl,
        )
