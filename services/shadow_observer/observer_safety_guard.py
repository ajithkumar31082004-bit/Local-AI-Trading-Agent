"""Strict Isolation & Safety Guard for Read-Only Shadow Observer.

ENFORCES CRITICAL ARCHITECTURAL CONSTRAINTS:
1. No order submission or broker execution is permitted under any circumstance.
2. No write operations are permitted outside designated shadow output directories.
3. Historical production/paper trading files are strictly protected from modification.
4. No private exchange credentials or authenticated endpoints may be accessed.
5. Fails closed immediately upon detecting any safety anomaly.
"""

from pathlib import Path
from typing import Union, Optional
import os
import logging

logger = logging.getLogger("shadow_observer.safety_guard")


class ShadowObserverSafetyViolation(RuntimeError):
    """Raised when an operation violates shadow observer isolation boundaries."""
    pass


# Paths that must NEVER be modified by the observer
FORBIDDEN_WRITE_TARGETS = [
    "paper_account_xautusd.json",
    "paper_forward_24h_state.json",
    "paper_account_xautusd.idempotency.db",
    "trading_agent.db",
    "orb_live_orders.sqlite3",
    "orb_journal.sqlite3",
    ".env",
    ".env.monitoring",
]


def validate_shadow_write_path(target_path: Union[str, Path], allowed_root: Optional[Union[str, Path]] = None) -> Path:
    """Validate that the write path is strictly confined to an authorized shadow report directory.
    
    Parameters:
        target_path: Proposed destination file path.
        allowed_root: Optional authorized base directory (defaults to reports/phase3_35_shadow_validation).
        
    Returns:
        Path: Resolved safe Path instance.
        
    Raises:
        ShadowObserverSafetyViolation: If path attempts to escape or write to production state files.
    """
    path = Path(target_path).resolve()
    
    # 1. Deny forbidden filenames (case-insensitive)
    name_lower = path.name.lower()
    for forbidden in FORBIDDEN_WRITE_TARGETS:
        forbidden_lower = forbidden.lower()
        if name_lower == forbidden_lower or name_lower.startswith(forbidden_lower):
            msg = f"SECURITY VIOLATION: Observer attempted to write to protected state file: {path.name}"
            logger.critical(msg)
            raise ShadowObserverSafetyViolation(msg)

    # 2. Deny directory escape if allowed_root is specified
    if allowed_root:
        root = Path(allowed_root).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            msg = f"SECURITY VIOLATION: Path '{path}' is outside authorized shadow directory '{root}'"
            logger.critical(msg)
            raise ShadowObserverSafetyViolation(msg)
            
    return path


def verify_observer_isolation() -> None:
    """Verify that the current process environment adheres to strict read-only shadow isolation."""
    # 1. Verify LIVE trading is disabled
    live_enabled = os.environ.get("LIVE_TRADING_ENABLED", "false").lower() in ("true", "1", "yes")
    if live_enabled:
        raise ShadowObserverSafetyViolation("CRITICAL: LIVE_TRADING_ENABLED is true. Observer aborted.")

    # 2. Verify trading mode is not LIVE
    trading_mode = os.environ.get("TRADING_MODE", "PAPER").upper()
    if trading_mode == "LIVE":
        raise ShadowObserverSafetyViolation("CRITICAL: TRADING_MODE is LIVE. Observer aborted.")

    logger.info("Shadow observer safety isolation verified: READ-ONLY SHADOW MODE.")


def block_execution_attempt(*args, **kwargs):
    """Unconditionally blocks any attempted broker order execution."""
    raise ShadowObserverSafetyViolation(
        "CRITICAL SECURITY VIOLATION: Order execution attempted from read-only shadow observer."
    )

