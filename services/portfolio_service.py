"""Portfolio Service: Aggregates portfolio metrics, open positions, P&L, drawdown, and trade statistics."""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime

from config.settings import settings
from database.models import PositionRecord, TradeRecord, AccountSnapshot
from database.repository import DatabaseRepository
from market_data.base import BaseMarketDataProvider


@dataclass
class PortfolioSummary:
    """Consolidated financial summary for user dashboard."""
    initial_balance: float
    cash_balance: float
    positions_value: float
    total_equity: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float
    total_pnl: float
    total_fees_paid: float
    peak_equity: float
    drawdown_dollars: float
    drawdown_pct: float
    open_positions: List[PositionRecord]
    recent_trades: List[TradeRecord]
    total_trades_count: int
    winning_trades_count: int
    losing_trades_count: int
    win_rate_pct: float
    profit_factor: float


class PortfolioService:
    """Provides high-level analytics and state updates for the user's paper portfolio."""

    def __init__(self, repo: Optional[DatabaseRepository] = None, data_provider: Optional[BaseMarketDataProvider] = None):
        self.repo = repo or DatabaseRepository()
        self.data_provider = data_provider

    def mark_to_market(self, custom_price_map: Optional[Dict[str, float]] = None) -> None:
        """Fetch current prices for all open positions and update database records."""
        positions = self.repo.get_positions()
        if not positions:
            return

        price_map = custom_price_map or {}
        if self.data_provider and not custom_price_map:
            for pos in positions:
                if pos.symbol not in price_map:
                    p = self.data_provider.get_latest_price(pos.symbol)
                    if p and p > 0:
                        price_map[pos.symbol] = p

        if price_map:
            self.repo.update_positions_prices(price_map)

    def get_summary(self) -> PortfolioSummary:
        """Calculate and return full portfolio metrics, drawdown, win rate, and profit factor."""
        account = self.repo.get_account_state()
        positions = self.repo.get_positions()
        trades = self.repo.get_trades(limit=200)

        initial_bal = account["initial_balance"]
        cash = account["cash_balance"]
        realized = account["realized_pnl"]
        fees = account["total_fees_paid"]
        peak = account["peak_equity"]

        positions_val = sum(p.market_value for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions)
        total_equity = cash + positions_val

        unrealized_pct = (unrealized / initial_bal * 100.0) if initial_bal > 0 else 0.0
        total_pnl = realized + unrealized - fees

        new_peak = max(peak, total_equity)
        drawdown_dollars = max(0.0, new_peak - total_equity)
        drawdown_pct = (drawdown_dollars / new_peak * 100.0) if new_peak > 0 else 0.0

        # Calculate trade statistics from SELL trades
        sell_trades = [t for t in trades if t.side.upper() == "SELL"]
        win_count = 0
        loss_count = 0
        gross_profit = 0.0
        gross_loss = 0.0

        # Group buy costs and sell proceeds by symbol
        for t in trades:
            if t.notes and "Profit" in t.notes:
                win_count += 1
            elif t.notes and "Loss" in t.notes:
                loss_count += 1

        # Use realized PnL proxy if note tags are generic
        if win_count == 0 and loss_count == 0 and realized != 0:
            if realized > 0:
                win_count = max(1, len(sell_trades))
                gross_profit = realized
            else:
                loss_count = max(1, len(sell_trades))
                gross_loss = abs(realized)
        else:
            win_count = sum(1 for p in positions if p.realized_pnl > 0)
            loss_count = sum(1 for p in positions if p.realized_pnl < 0)

        total_closed = win_count + loss_count
        win_rate = (win_count / total_closed * 100.0) if total_closed > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (1.0 if gross_profit == 0 else 99.0)

        return PortfolioSummary(
            initial_balance=initial_bal,
            cash_balance=cash,
            positions_value=positions_val,
            total_equity=total_equity,
            unrealized_pnl=unrealized,
            unrealized_pnl_pct=unrealized_pct,
            realized_pnl=realized,
            total_pnl=total_pnl,
            total_fees_paid=fees,
            peak_equity=new_peak,
            drawdown_dollars=drawdown_dollars,
            drawdown_pct=round(drawdown_pct, 2),
            open_positions=positions,
            recent_trades=trades,
            total_trades_count=len(trades),
            winning_trades_count=win_count,
            losing_trades_count=loss_count,
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
        )
