"""Paper Broker: Simulates order execution with slippage, transaction fees, and atomic persistence."""

import threading
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from config.settings import settings
from database.models import (
    TradeRecord,
    PositionRecord,
    OrderStatus,
    OrderSide,
)
from database.repository import DatabaseRepository
from services.risk_manager import RiskManager, RiskCheckResult


class PaperBroker:
    """Simulates paper trading execution with strict verification and SQLite transaction safety."""

    def __init__(self, repo: Optional[DatabaseRepository] = None, risk_manager: Optional[RiskManager] = None):
        self.repo = repo or DatabaseRepository()
        self.risk_manager = risk_manager or RiskManager()
        self._lock = threading.Lock()

    def execute_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        current_price: float,
        strategy_name: str = "MANUAL",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        notes: str = "",
        bypass_risk: bool = False,
    ) -> Tuple[bool, str, Optional[TradeRecord]]:
        """Atomically execute a paper trade after validating risk and confirming funds."""
        clean_symbol = symbol.strip().upper()
        clean_side = side.strip().upper()

        if quantity <= 0:
            return False, "Quantity must be strictly positive.", None
        if current_price <= 0:
            return False, "Invalid price quote.", None

        with self._lock:
            # 1. Fetch current account state and positions
            account = self.repo.get_account_state()
            cash_balance = account["cash_balance"]
            positions = self.repo.get_positions()
            existing_pos = self.repo.get_position(clean_symbol)
            existing_qty = existing_pos.quantity if existing_pos else 0.0

            total_positions_val = sum(p.market_value for p in positions)
            portfolio_equity = cash_balance + total_positions_val
            today_realized_loss = self.repo.get_today_realized_loss()

            # 2. Risk checks (cannot be bypassed unless explicitly testing)
            if not bypass_risk:
                risk_res: RiskCheckResult = self.risk_manager.evaluate_order(
                    symbol=clean_symbol,
                    side=clean_side,
                    requested_quantity=quantity,
                    current_price=current_price,
                    portfolio_equity=portfolio_equity,
                    cash_balance=cash_balance,
                    current_open_positions_count=len(positions),
                    today_realized_loss=today_realized_loss,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    existing_position_qty=existing_qty,
                )
                if not risk_res.approved:
                    reasons = " | ".join(risk_res.rejection_reasons)
                    return False, f"Risk check failed: {reasons}", None
                
                effective_price = risk_res.effective_price
                slippage = risk_res.estimated_slippage
                fee = risk_res.estimated_fee
            else:
                effective_price, slip = self.risk_manager.calculate_execution_price(current_price, clean_side)
                slippage = round(slip * quantity, 2)
                fee = self.risk_manager.calculate_fee(quantity * effective_price)

            trade_value = round(quantity * effective_price, 2)

            # 3. Calculate position transitions and cash delta
            now_str = datetime.now().isoformat()
            realized_pnl_delta = 0.0

            if clean_side == OrderSide.BUY.value:
                total_trade_outflow = trade_value + fee
                if cash_balance < total_trade_outflow - 1e-4:
                    return False, f"Insufficient cash for trade: required ${total_trade_outflow:,.2f}, have ${cash_balance:,.2f}.", None

                new_cash = round(cash_balance - total_trade_outflow, 2)

                if existing_pos and existing_qty > 0:
                    new_qty = existing_qty + quantity
                    new_avg_price = round(
                        ((existing_qty * existing_pos.avg_entry_price) + trade_value) / new_qty, 4
                    )
                    new_mkt_val = round(new_qty * effective_price, 2)
                    unrealized = round(new_mkt_val - (new_qty * new_avg_price), 2)
                    unrealized_pct = round((unrealized / (new_qty * new_avg_price)) * 100.0, 2)
                    updated_position = PositionRecord(
                        symbol=clean_symbol,
                        quantity=new_qty,
                        avg_entry_price=new_avg_price,
                        current_price=effective_price,
                        market_value=new_mkt_val,
                        unrealized_pnl=unrealized,
                        unrealized_pnl_pct=unrealized_pct,
                        realized_pnl=existing_pos.realized_pnl,
                        stop_loss=stop_loss or existing_pos.stop_loss,
                        take_profit=take_profit or existing_pos.take_profit,
                        opened_at=existing_pos.opened_at,
                        updated_at=now_str,
                    )
                else:
                    new_mkt_val = trade_value
                    updated_position = PositionRecord(
                        symbol=clean_symbol,
                        quantity=quantity,
                        avg_entry_price=effective_price,
                        current_price=effective_price,
                        market_value=new_mkt_val,
                        unrealized_pnl=0.0,
                        unrealized_pnl_pct=0.0,
                        realized_pnl=0.0,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        opened_at=now_str,
                        updated_at=now_str,
                    )

            else:  # SELL
                if existing_qty < quantity - 1e-6:
                    return False, f"Cannot sell {quantity}: position is only {existing_qty}.", None

                net_inflow = round(trade_value - fee, 2)
                new_cash = round(cash_balance + net_inflow, 2)

                # Realized P&L calculation: (effective_sell_price - avg_entry_price) * quantity
                cost_basis = quantity * existing_pos.avg_entry_price
                gross_pnl = trade_value - cost_basis
                realized_pnl_delta = round(gross_pnl, 2)

                rem_qty = round(existing_qty - quantity, 6)
                if rem_qty <= 0.0001:
                    updated_position = PositionRecord(
                        symbol=clean_symbol,
                        quantity=0.0,
                        avg_entry_price=0.0,
                        current_price=effective_price,
                        market_value=0.0,
                        unrealized_pnl=0.0,
                        unrealized_pnl_pct=0.0,
                        realized_pnl=existing_pos.realized_pnl + realized_pnl_delta,
                        updated_at=now_str,
                    )
                else:
                    new_mkt_val = round(rem_qty * effective_price, 2)
                    unrealized = round(new_mkt_val - (rem_qty * existing_pos.avg_entry_price), 2)
                    unrealized_pct = round((unrealized / (rem_qty * existing_pos.avg_entry_price)) * 100.0, 2)
                    updated_position = PositionRecord(
                        symbol=clean_symbol,
                        quantity=rem_qty,
                        avg_entry_price=existing_pos.avg_entry_price,
                        current_price=effective_price,
                        market_value=new_mkt_val,
                        unrealized_pnl=unrealized,
                        unrealized_pnl_pct=unrealized_pct,
                        realized_pnl=existing_pos.realized_pnl + realized_pnl_delta,
                        stop_loss=existing_pos.stop_loss,
                        take_profit=existing_pos.take_profit,
                        opened_at=existing_pos.opened_at,
                        updated_at=now_str,
                    )

            # 4. Construct and record trade
            trade = TradeRecord(
                symbol=clean_symbol,
                side=clean_side,
                quantity=quantity,
                price=effective_price,
                fee=fee,
                slippage=slippage,
                total_cost=trade_value,
                timestamp=now_str,
                strategy=strategy_name,
                status=OrderStatus.FILLED.value,
                notes=notes,
            )

            trade_id = self.repo.record_trade_and_update_portfolio(
                trade=trade,
                new_cash=new_cash,
                updated_position=updated_position,
                realized_pnl_delta=realized_pnl_delta,
                fee_delta=fee,
            )
            trade.id = trade_id

            msg = (
                f"Successfully filled {clean_side} {quantity} {clean_symbol} @ ${effective_price:,.2f} "
                f"(Fee: ${fee:.2f}, Slip: ${slippage:.2f})."
            )
            return True, msg, trade
