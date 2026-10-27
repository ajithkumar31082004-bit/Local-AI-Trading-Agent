"""Deterministic Financial Risk Management Engine."""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from config.settings import settings
from database.models import OrderSide


@dataclass
class RiskCheckResult:
    """Result of evaluating a proposed order against risk policies."""
    approved: bool
    rejection_reasons: List[str]
    suggested_quantity: float
    max_allowed_quantity: float
    dollar_risk: float
    position_value: float
    estimated_fee: float
    estimated_slippage: float
    effective_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    warning_notes: List[str]


class RiskManager:
    """Evaluates and enforces risk management rules on simulated trades."""

    def __init__(
        self,
        max_risk_per_trade_pct: Optional[float] = None,
        max_position_allocation_pct: Optional[float] = None,
        max_daily_loss_pct: Optional[float] = None,
        max_open_positions: Optional[int] = None,
        transaction_fee_pct: Optional[float] = None,
        default_slippage_pct: Optional[float] = None,
    ):
        self.max_risk_per_trade_pct = (
            max_risk_per_trade_pct if max_risk_per_trade_pct is not None else settings.max_risk_per_trade_pct
        )
        self.max_position_allocation_pct = (
            max_position_allocation_pct if max_position_allocation_pct is not None else settings.max_position_allocation_pct
        )
        self.max_daily_loss_pct = (
            max_daily_loss_pct if max_daily_loss_pct is not None else settings.max_daily_loss_pct
        )
        self.max_open_positions = (
            max_open_positions if max_open_positions is not None else settings.max_open_positions
        )
        self.transaction_fee_pct = (
            transaction_fee_pct if transaction_fee_pct is not None else settings.transaction_fee_pct
        )
        self.default_slippage_pct = (
            default_slippage_pct if default_slippage_pct is not None else settings.default_slippage_pct
        )

    def calculate_execution_price(self, price: float, side: str) -> Tuple[float, float]:
        """Apply modeled slippage based on trade direction. Returns (effective_price, slippage_amount)."""
        slippage_factor = self.default_slippage_pct / 100.0
        slippage_amt = round(price * slippage_factor, 4)
        if side.upper() == OrderSide.BUY.value:
            effective = round(price + slippage_amt, 4)
        else:
            effective = round(max(0.01, price - slippage_amt), 4)
        return effective, slippage_amt

    def calculate_fee(self, amount: float) -> float:
        """Calculate transaction fee for order value."""
        return round(amount * (self.transaction_fee_pct / 100.0), 2)

    def evaluate_order(
        self,
        symbol: str,
        side: str,
        requested_quantity: float,
        current_price: float,
        portfolio_equity: float,
        cash_balance: float,
        current_open_positions_count: int,
        today_realized_loss: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        existing_position_qty: float = 0.0,
    ) -> RiskCheckResult:
        """Perform comprehensive risk check and determine approval or rejection."""
        rejection_reasons: List[str] = []
        warning_notes: List[str] = []

        if current_price <= 0:
            return RiskCheckResult(
                approved=False,
                rejection_reasons=["Current price must be greater than zero."],
                suggested_quantity=0.0,
                max_allowed_quantity=0.0,
                dollar_risk=0.0,
                position_value=0.0,
                estimated_fee=0.0,
                estimated_slippage=0.0,
                effective_price=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                risk_reward_ratio=0.0,
                warning_notes=[],
            )

        # 1. Effective execution price and slippage
        effective_price, slippage_per_share = self.calculate_execution_price(current_price, side)
        clean_side = side.upper()

        # Handle SELL orders (closing/reducing positions)
        if clean_side == OrderSide.SELL.value:
            if existing_position_qty <= 0:
                rejection_reasons.append(f"Cannot SELL {symbol}: You have no open position to sell.")
            elif requested_quantity > existing_position_qty + 1e-6:
                rejection_reasons.append(
                    f"Cannot SELL {requested_quantity:.4f} shares: Exceeds current open position ({existing_position_qty:.4f})."
                )

            total_val = requested_quantity * effective_price
            fee = self.calculate_fee(total_val)
            approved = len(rejection_reasons) == 0

            return RiskCheckResult(
                approved=approved,
                rejection_reasons=rejection_reasons,
                suggested_quantity=min(requested_quantity, existing_position_qty),
                max_allowed_quantity=existing_position_qty,
                dollar_risk=0.0,
                position_value=round(total_val, 2),
                estimated_fee=fee,
                estimated_slippage=round(slippage_per_share * requested_quantity, 2),
                effective_price=effective_price,
                stop_loss=0.0,
                take_profit=0.0,
                risk_reward_ratio=0.0,
                warning_notes=warning_notes,
            )

        # BUY ORDER CHECKS

        # 2. Check Daily Max Loss limit
        daily_loss_limit_dollars = portfolio_equity * (self.max_daily_loss_pct / 100.0)
        if today_realized_loss >= daily_loss_limit_dollars:
            rejection_reasons.append(
                f"Daily Loss Limit Reached: Today's realized loss (${today_realized_loss:,.2f}) "
                f"exceeds allowed limit (${daily_loss_limit_dollars:,.2f} = {self.max_daily_loss_pct}% of equity). Trading halted for today."
            )

        # 3. Check Max Open Positions limit
        if existing_position_qty <= 0 and current_open_positions_count >= self.max_open_positions:
            rejection_reasons.append(
                f"Max Open Positions Limit Reached: Account currently holds {current_open_positions_count} "
                f"open positions (maximum allowed is {self.max_open_positions})."
            )

        # 4. Stop Loss and Position Sizing
        if stop_loss is None or stop_loss <= 0 or stop_loss >= effective_price:
            # Default stop loss if missing or invalid
            stop_loss = round(effective_price * (1 - (settings.default_stop_loss_pct / 100.0)), 2)
            warning_notes.append(
                f"No valid stop loss specified. Applied default stop loss at ${stop_loss:,.2f} ({settings.default_stop_loss_pct}% below price)."
            )

        if take_profit is None or take_profit <= effective_price:
            take_profit = round(effective_price * (1 + (settings.default_take_profit_pct / 100.0)), 2)

        stop_distance = max(0.01, effective_price - stop_loss)
        tp_distance = max(0.01, take_profit - effective_price)
        rr_ratio = round(tp_distance / stop_distance, 2)

        # Risk budget in dollars
        max_risk_dollars = portfolio_equity * (self.max_risk_per_trade_pct / 100.0)
        # Position size by risk formula: Equity * Risk% / (Price - SL)
        risk_based_qty = max_risk_dollars / stop_distance

        # Max allocation limit: Equity * Allocation% / Price
        max_allocation_dollars = portfolio_equity * (self.max_position_allocation_pct / 100.0)
        allocation_based_qty = max_allocation_dollars / effective_price

        # Available cash ceiling (accounting for fees)
        fee_buffer = 1 + (self.transaction_fee_pct / 100.0)
        cash_based_qty = max(0.0, (cash_balance / (effective_price * fee_buffer)))

        # Sizing is the minimum of risk budget, max position allocation, and available cash
        max_allowed_qty = max(0.0, min(risk_based_qty, allocation_based_qty, cash_based_qty))
        suggested_qty = round(max_allowed_qty, 4)

        # Check requested quantity against limits
        if requested_quantity <= 0:
            rejection_reasons.append("Requested quantity must be greater than zero.")
        elif requested_quantity > cash_based_qty + 1e-4:
            cost = requested_quantity * effective_price * fee_buffer
            rejection_reasons.append(
                f"Insufficient Cash: Order cost with fees is ${cost:,.2f}, but available cash is ${cash_balance:,.2f}."
            )
        elif requested_quantity > allocation_based_qty * 1.02:
            rejection_reasons.append(
                f"Position Allocation Exceeded: Requested position size (${requested_quantity * effective_price:,.2f}) "
                f"exceeds max position allocation limit (${max_allocation_dollars:,.2f} = {self.max_position_allocation_pct}% of equity)."
            )
        elif (requested_quantity * stop_distance) > (max_risk_dollars * 1.05):
            risk_amt = requested_quantity * stop_distance
            rejection_reasons.append(
                f"Risk Per Trade Limit Exceeded: Stop loss risk (${risk_amt:,.2f}) "
                f"exceeds maximum allowed dollar risk (${max_risk_dollars:,.2f} = {self.max_risk_per_trade_pct}% of equity)."
            )

        # Gap risk caution
        warning_notes.append(
            "Caution: A stop-loss order does not guarantee maximum loss during overnight gaps or high volatility."
        )

        pos_val = round(requested_quantity * effective_price, 2)
        total_fee = self.calculate_fee(pos_val)
        total_slip = round(slippage_per_share * requested_quantity, 2)
        dollar_risk = round(requested_quantity * stop_distance, 2)

        approved = len(rejection_reasons) == 0

        return RiskCheckResult(
            approved=approved,
            rejection_reasons=rejection_reasons,
            suggested_quantity=suggested_qty,
            max_allowed_quantity=round(max_allowed_qty, 4),
            dollar_risk=dollar_risk,
            position_value=pos_val,
            estimated_fee=total_fee,
            estimated_slippage=total_slip,
            effective_price=effective_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=rr_ratio,
            warning_notes=warning_notes,
        )
