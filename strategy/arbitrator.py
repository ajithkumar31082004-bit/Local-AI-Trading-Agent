"""Deterministic Strategy Arbitration Engine for XAUTUSD on Delta Exchange India.

Responsibilities:
1. Coordinates independent evaluation across all 6 strategies:
   - ORB (Opening Range Breakout)
   - EMA20/EMA50 Trend-Following Pullback
   - VWAP + EMA Confirmation
   - Bollinger Bands + RSI Mean Reversion
   - ATR Volatility Compression Breakout
   - Liquidity Sweep Reversal
2. Evaluates the independent Market Regime Detector.
3. Filters strategies by regime suitability.
4. Detects and rejects conflicting signals (simultaneous LONG and SHORT).
5. Compares transparent signal quality scores to select the single best eligible signal.
6. Enforces duplicate entry prevention and safety guards.
"""
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

from config.orb_settings import ORBSettings, settings
from regime.market_regime import MarketRegime, RegimeDetectionResult, detect_market_regime
from strategy.orb_ema_volume import Signal, evaluate_orb
from strategy.trend_following import evaluate_trend_following
from strategy.vwap_ema import evaluate_vwap_ema
from strategy.bb_rsi_mean_reversion import evaluate_bb_rsi
from strategy.atr_compression_breakout import evaluate_atr_compression_breakout
from strategy.liquidity_sweep import evaluate_liquidity_sweep


@dataclass
class ArbitrationResult:
    winning_signal: Signal
    all_signals: Dict[str, Signal]
    regime_result: RegimeDetectionResult
    eligible_signals: List[Signal]
    rejected_strategies: List[Tuple[str, str]]
    decision: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "winning_signal": self.winning_signal.to_dict(),
            "all_signals": {k: v.to_dict() for k, v in self.all_signals.items()},
            "regime": self.regime_result.to_dict(),
            "eligible_count": len(self.eligible_signals),
            "rejected_strategies": self.rejected_strategies,
            "decision": self.decision,
        }


def arbitrate_strategies(frame: pd.DataFrame, cfg: ORBSettings = settings) -> ArbitrationResult:
    """Evaluate registered strategies conforming to market regime and select the highest quality signal."""
    # 1. Market Regime Evaluation
    regime_res = detect_market_regime(frame, cfg)

    # If regime is UNCERTAIN, block all trades immediately without computing sub-strategies
    if regime_res.regime == MarketRegime.UNCERTAIN or regime_res.confidence < 0.60:
        all_strat_names = ["ORB", "TREND_FOLLOWING", "VWAP_EMA", "BB_RSI_MEAN_REVERSION", "ATR_COMPRESSION_BREAKOUT", "LIQUIDITY_SWEEP"]
        dummy_signals = {
            name: Signal(
                direction="NO TRADE",
                entry=None,
                sl=None,
                tp=None,
                r_multiple=0.0,
                reason=f"NO TRADE: market regime is UNCERTAIN ({regime_res.rationale}).",
                checklist={},
                timestamp=str(frame.index[-1]) if not frame.empty else None,
                diagnostic_code="REGIME_UNCERTAIN",
                strategy_name=name,
                quality_score=0.0,
            )
            for name in all_strat_names
        }
        no_trade_sig = Signal(
            direction="NO TRADE",
            entry=None,
            sl=None,
            tp=None,
            r_multiple=0.0,
            reason=f"NO TRADE: market regime is UNCERTAIN ({regime_res.rationale}). All strategies blocked for capital preservation.",
            checklist={k: False for k in ["regime_approved", "no_conflict", "quality_threshold"]},
            timestamp=str(frame.index[-1]) if not frame.empty else None,
            diagnostic_code="REGIME_UNCERTAIN",
            strategy_name="ARBITRATOR",
            quality_score=0.0,
        )
        return ArbitrationResult(
            winning_signal=no_trade_sig,
            all_signals=dummy_signals,
            regime_result=regime_res,
            eligible_signals=[],
            rejected_strategies=[(name, "REGIME_UNCERTAIN") for name in all_strat_names],
            decision="REGIME_UNCERTAIN",
        )

    # 2. Evaluate only strategies allowed by the identified regime
    # Phase 3 mandate: BB_RSI_MEAN_REVERSION is kept disabled in the portfolio until positive expectancy is demonstrated
    disabled_in_portfolio = {"BB_RSI_MEAN_REVERSION"}

    eval_dispatch = {
        "ORB": evaluate_orb,
        "TREND_FOLLOWING": evaluate_trend_following,
        "VWAP_EMA": evaluate_vwap_ema,
        "BB_RSI_MEAN_REVERSION": evaluate_bb_rsi,
        "ATR_COMPRESSION_BREAKOUT": evaluate_atr_compression_breakout,
        "LIQUIDITY_SWEEP": evaluate_liquidity_sweep,
    }

    raw_signals: Dict[str, Signal] = {}
    for name, fn in eval_dispatch.items():
        if name in disabled_in_portfolio:
            raw_signals[name] = Signal(
                direction="NO TRADE",
                entry=None,
                sl=None,
                tp=None,
                r_multiple=0.0,
                reason=f"NO TRADE: {name} disabled in portfolio pending out-of-sample positive expectancy.",
                checklist={},
                timestamp=str(frame.index[-1]) if not frame.empty else None,
                diagnostic_code="STRATEGY_DISABLED_EXPERIMENTAL",
                strategy_name=name,
                quality_score=0.0,
            )
        elif name in regime_res.allowed_strategies:
            sig = fn(frame, cfg)
            if not sig.strategy_name:
                sig.strategy_name = name
            raw_signals[name] = sig
        else:
            raw_signals[name] = Signal(
                direction="NO TRADE",
                entry=None,
                sl=None,
                tp=None,
                r_multiple=0.0,
                reason=f"NO TRADE: disallowed by market regime ({regime_res.regime.value}).",
                checklist={},
                timestamp=str(frame.index[-1]) if not frame.empty else None,
                diagnostic_code=f"DISALLOWED_IN_{regime_res.regime.value}",
                strategy_name=name,
                quality_score=0.0,
            )

    rejected: List[Tuple[str, str]] = []
    eligible: List[Signal] = []

    # 3. Filter by Regime Suitability
    for name, sig in raw_signals.items():
        if sig.direction not in {"LONG", "SHORT"}:
            rejected.append((name, sig.diagnostic_code or "NO_SIGNAL"))
            continue

        # Check regime compatibility
        if name in regime_res.disallowed_strategies:
            rejected.append((name, f"DISALLOWED_IN_{regime_res.regime.value}"))
            continue

        if name not in regime_res.allowed_strategies:
            rejected.append((name, f"NOT_SUITABLE_FOR_{regime_res.regime.value}"))
            continue

        # In trending regimes, ensure trade direction aligns with trend
        if regime_res.regime == MarketRegime.TRENDING_BULLISH and sig.direction != "LONG":
            rejected.append((name, "COUNTER_TREND_SHORT_FORBIDDEN"))
            continue
        if regime_res.regime == MarketRegime.TRENDING_BEARISH and sig.direction != "SHORT":
            rejected.append((name, "COUNTER_TREND_LONG_FORBIDDEN"))
            continue

        eligible.append(sig)

    # 4. Check for Conflicting Signals (Simultaneous LONG and SHORT)
    has_long = any(s.direction == "LONG" for s in eligible)
    has_short = any(s.direction == "SHORT" for s in eligible)

    if has_long and has_short:
        no_trade_sig = Signal(
            direction="NO TRADE",
            entry=None,
            sl=None,
            tp=None,
            r_multiple=0.0,
            reason="NO TRADE: conflicting strategy signals detected simultaneously (LONG vs SHORT). Arbitrator rejects conflicting entries.",
            checklist={k: False for k in ["no_conflict"]},
            timestamp=str(frame.index[-1]) if not frame.empty else None,
            diagnostic_code="CONFLICTING_SIGNALS",
            strategy_name="ARBITRATOR",
            quality_score=0.0,
        )
        return ArbitrationResult(
            winning_signal=no_trade_sig,
            all_signals=raw_signals,
            regime_result=regime_res,
            eligible_signals=eligible,
            rejected_strategies=[(s.strategy_name or "UNKNOWN", "CONFLICT_REJECTED") for s in eligible],
            decision="CONFLICT_REJECTED",
        )

    # 5. Select Best Eligible Signal by Quality Score
    min_quality = getattr(cfg, "min_signal_quality_score", 0.60)
    high_quality_signals = [s for s in eligible if s.quality_score >= min_quality]

    if not high_quality_signals:
        no_trade_sig = Signal(
            direction="NO TRADE",
            entry=None,
            sl=None,
            tp=None,
            r_multiple=0.0,
            reason=f"NO TRADE: no eligible strategy passed regime filters and quality score threshold (min {min_quality:.2f}).",
            checklist={k: False for k in ["quality_threshold"]},
            timestamp=str(frame.index[-1]) if not frame.empty else None,
            diagnostic_code="NO_ELIGIBLE_HIGH_QUALITY_SIGNAL",
            strategy_name="ARBITRATOR",
            quality_score=0.0,
        )
        return ArbitrationResult(
            winning_signal=no_trade_sig,
            all_signals=raw_signals,
            regime_result=regime_res,
            eligible_signals=eligible,
            rejected_strategies=rejected,
            decision="NO_ELIGIBLE_SIGNAL",
        )

    # Sort by quality_score descending, then r_multiple descending
    high_quality_signals.sort(key=lambda s: (s.quality_score, s.r_multiple), reverse=True)
    best_signal = high_quality_signals[0]

    return ArbitrationResult(
        winning_signal=best_signal,
        all_signals=raw_signals,
        regime_result=regime_res,
        eligible_signals=high_quality_signals,
        rejected_strategies=rejected,
        decision="TRADE_SELECTED",
    )


def evaluate_arbitrated_portfolio(frame: pd.DataFrame, cfg: ORBSettings = settings) -> Signal:
    """Convenience function returning the winning Signal for seamless drop-in backtest integration."""
    arb_res = arbitrate_strategies(frame, cfg)
    return arb_res.winning_signal

