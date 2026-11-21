"""Deterministic trading strategy modules for XAUTUSD on Delta Exchange India."""
from .orb_ema_volume import evaluate_orb, Signal
from .trend_following import evaluate_trend_following
from .vwap_ema import evaluate_vwap_ema
from .bb_rsi_mean_reversion import evaluate_bb_rsi
from .atr_compression_breakout import evaluate_atr_compression_breakout
from .liquidity_sweep import evaluate_liquidity_sweep
from .arbitrator import arbitrate_strategies, evaluate_arbitrated_portfolio, ArbitrationResult

__all__ = [
    "evaluate_orb",
    "evaluate_trend_following",
    "evaluate_vwap_ema",
    "evaluate_bb_rsi",
    "evaluate_atr_compression_breakout",
    "evaluate_liquidity_sweep",
    "arbitrate_strategies",
    "evaluate_arbitrated_portfolio",
    "ArbitrationResult",
    "Signal",
]
