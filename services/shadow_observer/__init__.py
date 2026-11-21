"""Read-Only Shadow Observer Module for XAUTUSD on Delta Exchange India."""

from .observer_safety_guard import (
    ShadowObserverSafetyViolation,
    validate_shadow_write_path,
    verify_observer_isolation,
    block_execution_attempt,
)
from .candle_validator import (
    CandleValidator,
    CandleValidationReport,
)
from .strategy_evaluator import (
    ReadOnlyStrategyEvaluator,
    ShadowEvaluationEvent,
)
from .signal_recorder import (
    ShadowSignalRecorder,
)
from .market_data_reader import (
    PublicMarketDataReader,
    PUBLIC_REST_URL,
    PUBLIC_WS_URL,
    TARGET_INSTRUMENT,
)
from .shadow_report import (
    ShadowReportGenerator,
)
from .dry_run_runner import (
    DryRunConfig,
    execute_preflight_safety_gates,
    ShadowDryRunRunner,
)

__all__ = [
    "ShadowObserverSafetyViolation",
    "validate_shadow_write_path",
    "verify_observer_isolation",
    "block_execution_attempt",
    "CandleValidator",
    "CandleValidationReport",
    "ReadOnlyStrategyEvaluator",
    "ShadowEvaluationEvent",
    "ShadowSignalRecorder",
    "PublicMarketDataReader",
    "PUBLIC_REST_URL",
    "PUBLIC_WS_URL",
    "TARGET_INSTRUMENT",
    "ShadowReportGenerator",
    "DryRunConfig",
    "execute_preflight_safety_gates",
    "ShadowDryRunRunner",
]

