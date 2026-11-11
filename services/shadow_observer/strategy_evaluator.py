"""Pure Read-Only Strategy Evaluator for Shadow Observer.

COORDINATES EVALUATION ACROSS ALL 6 XAUTUSD STRATEGIES:
1. ORB (Opening Range Breakout)
2. EMA20/EMA50 Trend Following Pullback
3. VWAP + EMA Confirmation
4. Bollinger Bands + RSI Mean Reversion
5. ATR Volatility Compression Breakout
6. Liquidity Sweep Reversal

STRICT INVARIANTS:
- Evaluates strategies deterministically on pure completed OHLCV data.
- Never imports paper broker or calls order placement methods.
- Captures winning signals, sub-strategy signals, regime classifications, and rejection reasons.
- Produces hypothetical signal objects labeled 'HYPOTHETICAL — NOT AN ORDER'.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import logging

from config.orb_settings import ORBSettings, settings as default_orb_settings
from regime.market_regime import MarketRegime, RegimeDetectionResult, detect_market_regime
from strategy.orb_ema_volume import Signal, evaluate_orb
from strategy.trend_following import evaluate_trend_following
from strategy.vwap_ema import evaluate_vwap_ema
from strategy.bb_rsi_mean_reversion import evaluate_bb_rsi
from strategy.atr_compression_breakout import evaluate_atr_compression_breakout
from strategy.liquidity_sweep import evaluate_liquidity_sweep
from strategy.arbitrator import arbitrate_strategies, ArbitrationResult

logger = logging.getLogger("shadow_observer.strategy_evaluator")


@dataclass
class ShadowEvaluationEvent:
    event_id: str
    timestamp_utc: str
    timestamp_ist: str
    instrument: str
    candle_timeframe: str
    market_regime: str
    regime_confidence: float
    is_warming_up: bool
    winning_strategy: str
    winning_signal: str  # 'BUY', 'SELL', 'NO_SIGNAL'
    entry_reference_price: Optional[float]
    hypothetical_sl: Optional[float]
    hypothetical_tp: Optional[float]
    risk_reward_ratio: float
    signal_quality_score: float
    rejection_reason: Optional[str]
    diagnostic_code: str
    sub_strategy_signals: Dict[str, Dict[str, Any]]
    label: str = "HYPOTHETICAL — NOT AN ORDER"
    is_order: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "is_order": self.is_order,
            "event_id": self.event_id,
            "timestamp_utc": self.timestamp_utc,
            "timestamp_ist": self.timestamp_ist,
            "instrument": self.instrument,
            "candle_timeframe": self.candle_timeframe,
            "market_regime": self.market_regime,
            "regime_confidence": self.regime_confidence,
            "is_warming_up": self.is_warming_up,
            "winning_strategy": self.winning_strategy,
            "winning_signal": self.winning_signal,
            "entry_reference_price": self.entry_reference_price,
            "hypothetical_sl": self.hypothetical_sl,
            "hypothetical_tp": self.hypothetical_tp,
            "risk_reward_ratio": self.risk_reward_ratio,
            "signal_quality_score": self.signal_quality_score,
            "rejection_reason": self.rejection_reason,
            "diagnostic_code": self.diagnostic_code,
            "sub_strategy_signals": self.sub_strategy_signals,
        }


class ReadOnlyStrategyEvaluator:
    """Evaluates registered strategies on completed OHLCV candles without broker execution."""

    def __init__(self, cfg: Optional[ORBSettings] = None, instrument: str = "XAUTUSD"):
        self.cfg = cfg or default_orb_settings
        self.instrument = instrument
        self.registered_strategies = {
            "ORB": evaluate_orb,
            "TREND_FOLLOWING": evaluate_trend_following,
            "VWAP_EMA": evaluate_vwap_ema,
            "BB_RSI_MEAN_REVERSION": evaluate_bb_rsi,
            "ATR_COMPRESSION_BREAKOUT": evaluate_atr_compression_breakout,
            "LIQUIDITY_SWEEP": evaluate_liquidity_sweep,
        }
        self.evaluation_count = 0

    def evaluate_all_strategies_independently(self, df: pd.DataFrame) -> Dict[str, Signal]:
        """Run each strategy independently on the candles and collect all signals."""
        results: Dict[str, Signal] = {}
        for name, eval_fn in self.registered_strategies.items():
            try:
                sig = eval_fn(df, self.cfg)
                results[name] = sig
            except Exception as exc:
                logger.error("Error evaluating strategy %s: %s", name, exc)
                results[name] = Signal(
                    direction="NO TRADE",
                    entry=None,
                    sl=None,
                    tp=None,
                    r_multiple=0.0,
                    reason=f"EVALUATION_ERROR: {exc}",
                    checklist={},
                    timestamp=str(df.index[-1]) if not df.empty else None,
                    diagnostic_code="EVAL_EXCEPTION",
                    strategy_name=name,
                    quality_score=0.0,
                )
        return results

    def evaluate_arbitrated_shadow(
        self,
        df: pd.DataFrame,
        event_index: Optional[int] = None,
    ) -> ShadowEvaluationEvent:
        """Execute full market regime detection and strategy arbitration in pure read-only mode."""
        self.evaluation_count += 1
        now_utc = datetime.now(timezone.utc)
        now_utc_iso = now_utc.isoformat()
        
        # Convert UTC to IST for logging
        try:
            import zoneinfo
            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
            now_ist_iso = now_utc.astimezone(ist_tz).isoformat()
        except Exception:
            now_ist_iso = now_utc_iso

        # Empty data check
        if df.empty:
            return ShadowEvaluationEvent(
                event_id=f"SHADOW_EVT_{self.evaluation_count:06d}",
                timestamp_utc=now_utc_iso,
                timestamp_ist=now_ist_iso,
                instrument=self.instrument,
                candle_timeframe="5m",
                market_regime="UNKNOWN",
                regime_confidence=0.0,
                is_warming_up=True,
                winning_strategy="NONE",
                winning_signal="NO_SIGNAL",
                entry_reference_price=None,
                hypothetical_sl=None,
                hypothetical_tp=None,
                risk_reward_ratio=0.0,
                signal_quality_score=0.0,
                rejection_reason="EMPTY_CANDLE_DATA: Zero candles available for evaluation.",
                diagnostic_code="EMPTY_DATA",
                sub_strategy_signals={},
            )

        # 1. Arbitrate strategies using core arbitration engine
        arb_res: ArbitrationResult = arbitrate_strategies(df, self.cfg)
        winning_sig = arb_res.winning_signal
        regime_res = arb_res.regime_result

        # Map signal direction to standard BUY / SELL / NO_SIGNAL
        dir_clean = str(winning_sig.direction or "NO TRADE").strip().upper()
        if dir_clean in ("LONG", "BUY"):
            norm_signal = "BUY"
        elif dir_clean in ("SHORT", "SELL"):
            norm_signal = "SELL"
        else:
            norm_signal = "NO_SIGNAL"

        # Determine if system was warming up
        is_warmup = (
            "WARMING_UP" in (regime_res.rationale or "")
            or regime_res.metrics.get("readiness", {}).get("state") == "WARMING_UP"
            or winning_sig.diagnostic_code == "WARMING_UP"
        )

        # Structure sub-strategy output
        sub_signals = {}
        for name, sig in arb_res.all_signals.items():
            sub_signals[name] = {
                "direction": sig.direction,
                "entry": sig.entry,
                "sl": sig.sl,
                "tp": sig.tp,
                "r_multiple": sig.r_multiple,
                "quality_score": sig.quality_score,
                "diagnostic_code": sig.diagnostic_code,
                "reason": sig.reason,
            }

        return ShadowEvaluationEvent(
            event_id=f"SHADOW_EVT_{self.evaluation_count:06d}",
            timestamp_utc=now_utc_iso,
            timestamp_ist=now_ist_iso,
            instrument=self.instrument,
            candle_timeframe="5m",
            market_regime=regime_res.regime.value if hasattr(regime_res.regime, "value") else str(regime_res.regime),
            regime_confidence=round(float(regime_res.confidence), 4),
            is_warming_up=is_warmup,
            winning_strategy=winning_sig.strategy_name or "ARBITRATOR",
            winning_signal=norm_signal,
            entry_reference_price=float(winning_sig.entry) if winning_sig.entry else None,
            hypothetical_sl=float(winning_sig.sl) if winning_sig.sl else None,
            hypothetical_tp=float(winning_sig.tp) if winning_sig.tp else None,
            risk_reward_ratio=float(winning_sig.r_multiple) if winning_sig.r_multiple else 0.0,
            signal_quality_score=float(winning_sig.quality_score) if winning_sig.quality_score else 0.0,
            rejection_reason=winning_sig.reason if norm_signal == "NO_SIGNAL" else None,
            diagnostic_code=winning_sig.diagnostic_code or "OK",
            sub_strategy_signals=sub_signals,
        )

