"""Central Hard Position-Sizing Safety Guard for Delta Paper Trading.

Enforces strict, non-negotiable order size restrictions across all paper trading
execution pathways. Under Phase 3.23 specification, PAPER trading allows exactly
ONE micro-lot (1 lot = 0.001 XAUT).

CRITICAL FINANCIAL SAFETY INVARIANT:
Oversized orders (e.g., 2, 10, 100, 484, 495 lots) are NEVER silently clamped.
They are DETERMINISTICALLY REJECTED with an auditable PositionSizeViolation.
"""

from dataclasses import dataclass
import logging
import math
from typing import Any, Tuple, Optional

logger = logging.getLogger("risk.position_size_guard")

# NON-NEGOTIABLE POLICY LIMITS
APPROVED_MAX_PAPER_LOTS: int = 1
APPROVED_MIN_PAPER_LOTS: int = 1
STANDARD_CONTRACT_SIZE_XAUT: float = 0.001  # 1 lot = 0.001 XAUT


class PositionSizeViolation(ValueError):
    """Raised when an order quantity violates position sizing safety policy."""

    def __init__(self, message: str, violation_code: str, requested_quantity: Any):
        super().__init__(message)
        self.violation_code = violation_code
        self.requested_quantity = requested_quantity


@dataclass(frozen=True)
class PositionGuardDecision:
    """Immutable audit result of position sizing guard evaluation."""
    approved: bool
    lots: int
    violation_code: Optional[str]
    rejection_reason: Optional[str]


def validate_paper_order_lots(lots: Any) -> int:
    """Validate that order quantity conforms to strict single-lot paper policy.
    
    Parameters:
        lots: Proposed lot quantity of any type.
        
    Returns:
        int: Exactly 1 lot if approved.
        
    Raises:
        PositionSizeViolation: If lots violates ANY safety rule.
    """
    # 1. Missing / None check
    if lots is None:
        msg = "SECURITY SIZING REJECTION: Proposed lot quantity is None."
        logger.error("[SECURITY_SIZING_VIOLATION] code=MISSING_QUANTITY requested=None")
        raise PositionSizeViolation(msg, violation_code="MISSING_QUANTITY", requested_quantity=None)

    # 2. Boolean masquerade check (isinstance(True, int) is True in Python!)
    if isinstance(lots, bool):
        msg = f"SECURITY SIZING REJECTION: Boolean '{lots}' is not a valid lot quantity."
        logger.error(f"[SECURITY_SIZING_VIOLATION] code=BOOLEAN_TYPE_REJECTED requested={lots}")
        raise PositionSizeViolation(msg, violation_code="BOOLEAN_TYPE_REJECTED", requested_quantity=lots)

    # 3. Float, NaN, Infinity rejection
    if isinstance(lots, float):
        if math.isnan(lots):
            msg = "SECURITY SIZING REJECTION: Lot quantity is NaN."
            logger.error("[SECURITY_SIZING_VIOLATION] code=NAN_QUANTITY_REJECTED requested=NaN")
            raise PositionSizeViolation(msg, violation_code="NAN_QUANTITY_REJECTED", requested_quantity=lots)
        if math.isinf(lots):
            msg = "SECURITY SIZING REJECTION: Lot quantity is Infinity."
            logger.error("[SECURITY_SIZING_VIOLATION] code=INF_QUANTITY_REJECTED requested=Inf")
            raise PositionSizeViolation(msg, violation_code="INF_QUANTITY_REJECTED", requested_quantity=lots)

        msg = (
            f"SECURITY SIZING REJECTION: Float quantity ({lots}) is strictly prohibited. "
            f"Derivatives exchange requires discrete integer lots."
        )
        logger.error(f"[SECURITY_SIZING_VIOLATION] code=FLOAT_QUANTITY_REJECTED requested={lots}")
        raise PositionSizeViolation(msg, violation_code="FLOAT_QUANTITY_REJECTED", requested_quantity=lots)

    # 4. Strict Type Check: must be python int
    if not isinstance(lots, int):
        msg = (
            f"SECURITY SIZING REJECTION: Unexpected quantity type '{type(lots).__name__}' "
            f"for value: {lots!r}. Expected int."
        )
        logger.error(f"[SECURITY_SIZING_VIOLATION] code=INVALID_TYPE requested_type={type(lots).__name__} val={lots!r}")
        raise PositionSizeViolation(msg, violation_code="INVALID_TYPE", requested_quantity=lots)

    # 5. Non-positive quantities
    if lots <= 0:
        msg = f"SECURITY SIZING REJECTION: Lot quantity ({lots}) must be strictly positive (>= 1)."
        logger.error(f"[SECURITY_SIZING_VIOLATION] code=NON_POSITIVE_LOTS requested={lots}")
        raise PositionSizeViolation(msg, violation_code="NON_POSITIVE_LOTS", requested_quantity=lots)

    # 6. Oversized quantities (e.g. 2, 10, 100, 484, 495)
    # ZERO SILENT CLAMPING: An oversized quantity must fail immediately.
    if lots > APPROVED_MAX_PAPER_LOTS:
        msg = (
            f"SECURITY SIZING REJECTION: Requested {lots} lots exceeds the authorized forward-test "
            f"maximum of {APPROVED_MAX_PAPER_LOTS} lot ({STANDARD_CONTRACT_SIZE_XAUT} XAUT). "
            f"Oversized order is deterministically rejected. Silent clamping is prohibited."
        )
        logger.error(f"[SECURITY_SIZING_VIOLATION] code=OVERSIZED_QUANTITY requested_lots={lots} limit={APPROVED_MAX_PAPER_LOTS}")
        raise PositionSizeViolation(msg, violation_code="OVERSIZED_QUANTITY", requested_quantity=lots)

    # 7. Exact 1-lot approval
    if lots != APPROVED_MAX_PAPER_LOTS:
        msg = f"SECURITY SIZING REJECTION: Exactly {APPROVED_MAX_PAPER_LOTS} lot required, got {lots}."
        logger.error(f"[SECURITY_SIZING_VIOLATION] code=UNAPPROVED_LOT_VALUE requested_lots={lots}")
        raise PositionSizeViolation(msg, violation_code="UNAPPROVED_LOT_VALUE", requested_quantity=lots)

    return lots


def evaluate_paper_order_lots(lots: Any) -> PositionGuardDecision:
    """Non-throwing evaluation function returning a structured PositionGuardDecision.
    
    Safe for inspection, pre-flight checks, and UI alerts.
    """
    try:
        validated_lots = validate_paper_order_lots(lots)
        return PositionGuardDecision(
            approved=True,
            lots=validated_lots,
            violation_code=None,
            rejection_reason=None,
        )
    except PositionSizeViolation as e:
        return PositionGuardDecision(
            approved=False,
            lots=0,
            violation_code=e.violation_code,
            rejection_reason=str(e),
        )


def validate_contract_quantity(contract_qty: Any, contract_size_xaut: float = STANDARD_CONTRACT_SIZE_XAUT) -> float:
    """Validate that physical contract quantity equals exactly 1 micro-lot (0.001 XAUT).
    
    Protects against downstream or physical contract conversions inflating the position.
    """
    if contract_qty is None or isinstance(contract_qty, bool) or not isinstance(contract_qty, (int, float)):
        raise PositionSizeViolation(
            f"SECURITY SIZING REJECTION: Invalid contract quantity type: {type(contract_qty).__name__}",
            violation_code="INVALID_CONTRACT_TYPE",
            requested_quantity=contract_qty,
        )

    if math.isnan(contract_qty) or math.isinf(contract_qty):
        raise PositionSizeViolation(
            "SECURITY SIZING REJECTION: Contract quantity cannot be NaN or Infinity.",
            violation_code="NAN_CONTRACT_QTY",
            requested_quantity=contract_qty,
        )

    expected_qty = APPROVED_MAX_PAPER_LOTS * contract_size_xaut
    if abs(contract_qty - expected_qty) > 1e-9:
        raise PositionSizeViolation(
            f"SECURITY SIZING REJECTION: Physical contract quantity ({contract_qty:.6f} XAUT) "
            f"does not match authorized 1-lot specification ({expected_qty:.6f} XAUT). "
            f"Oversized contract conversion rejected.",
            violation_code="OVERSIZED_CONTRACT_CONVERSION",
            requested_quantity=contract_qty,
        )

    return float(contract_qty)
