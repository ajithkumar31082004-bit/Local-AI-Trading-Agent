"""SQLite database repository for trade execution, position management, and account tracking."""

import sqlite3
import threading
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from config.settings import settings
from database.models import (
    TradeRecord,
    PositionRecord,
    AccountSnapshot,
    OrderStatus,
    OrderSide,
)
from database.supabase_client import SupabaseClient


class DatabaseRepository:
    """Thread-safe SQLite repository with transaction guarantees and optional Supabase cloud sync."""

    def __init__(self, db_path: Optional[Path] = None, supabase_client: Optional[SupabaseClient] = None):
        self.db_path = db_path or settings.db_path
        self._lock = threading.Lock()
        self.supabase = supabase_client or SupabaseClient()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        """Create necessary tables and index definitions."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Account balance and global metrics
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    initial_balance REAL NOT NULL,
                    cash_balance REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0.0,
                    total_fees_paid REAL NOT NULL DEFAULT 0.0,
                    peak_equity REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Open and closed positions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    quantity REAL NOT NULL,
                    avg_entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    market_value REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL DEFAULT 0.0,
                    unrealized_pnl_pct REAL NOT NULL DEFAULT 0.0,
                    realized_pnl REAL NOT NULL DEFAULT 0.0,
                    stop_loss REAL,
                    take_profit REAL,
                    opened_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Trade execution history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL,
                    slippage REAL NOT NULL,
                    total_cost REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notes TEXT
                );
            """)

            # Daily snapshots for performance and drawdown tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE NOT NULL,
                    equity REAL NOT NULL,
                    cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    drawdown_pct REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # Initialize account if not present
            cursor.execute("SELECT id FROM account WHERE id = 1;")
            if not cursor.fetchone():
                now_str = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO account (id, initial_balance, cash_balance, realized_pnl, total_fees_paid, peak_equity, updated_at)
                    VALUES (1, ?, ?, 0.0, 0.0, ?, ?);
                """, (settings.default_starting_balance, settings.default_starting_balance, settings.default_starting_balance, now_str))

            conn.commit()

        # Cloud sync
        if self.supabase and self.supabase.enabled:
            try:
                self.supabase.reset_remote_account(balance)
            except Exception:
                pass

    def reset_account(self, starting_balance: Optional[float] = None) -> None:
        """Completely reset the paper trading account, positions, and trade history."""
        balance = starting_balance if starting_balance is not None else settings.default_starting_balance
        now_str = datetime.now().isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions;")
            cursor.execute("DELETE FROM trades;")
            cursor.execute("DELETE FROM daily_snapshots;")
            cursor.execute("""
                UPDATE account
                SET initial_balance = ?,
                    cash_balance = ?,
                    realized_pnl = 0.0,
                    total_fees_paid = 0.0,
                    peak_equity = ?,
                    updated_at = ?
                WHERE id = 1;
            """, (balance, balance, balance, now_str))
            conn.commit()

    def get_account_state(self) -> Dict[str, Any]:
        """Fetch current cash balance, initial balance, realized PnL, and fees."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT initial_balance, cash_balance, realized_pnl, total_fees_paid, peak_equity, updated_at FROM account WHERE id = 1;")
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {
                "initial_balance": settings.default_starting_balance,
                "cash_balance": settings.default_starting_balance,
                "realized_pnl": 0.0,
                "total_fees_paid": 0.0,
                "peak_equity": settings.default_starting_balance,
                "updated_at": datetime.now().isoformat(),
            }

    def get_positions(self) -> List[PositionRecord]:
        """Retrieve all active open positions (quantity > 0)."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE quantity > 0.000001 ORDER BY symbol ASC;")
            rows = cursor.fetchall()
            positions = []
            for r in rows:
                positions.append(PositionRecord(
                    id=r["id"],
                    symbol=r["symbol"],
                    quantity=r["quantity"],
                    avg_entry_price=r["avg_entry_price"],
                    current_price=r["current_price"],
                    market_value=r["market_value"],
                    unrealized_pnl=r["unrealized_pnl"],
                    unrealized_pnl_pct=r["unrealized_pnl_pct"],
                    realized_pnl=r["realized_pnl"],
                    stop_loss=r["stop_loss"],
                    take_profit=r["take_profit"],
                    opened_at=r["opened_at"],
                    updated_at=r["updated_at"],
                ))
            return positions

    def get_position(self, symbol: str) -> Optional[PositionRecord]:
        """Retrieve an open position for a given symbol."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE symbol = ? AND quantity > 0.000001;", (symbol.upper(),))
            r = cursor.fetchone()
            if not r:
                return None
            return PositionRecord(
                id=r["id"],
                symbol=r["symbol"],
                quantity=r["quantity"],
                avg_entry_price=r["avg_entry_price"],
                current_price=r["current_price"],
                market_value=r["market_value"],
                unrealized_pnl=r["unrealized_pnl"],
                unrealized_pnl_pct=r["unrealized_pnl_pct"],
                realized_pnl=r["realized_pnl"],
                stop_loss=r["stop_loss"],
                take_profit=r["take_profit"],
                opened_at=r["opened_at"],
                updated_at=r["updated_at"],
            )

    def update_positions_prices(self, price_map: Dict[str, float]) -> None:
        """Update market values and unrealized P&L for open positions given current prices."""
        now_str = datetime.now().isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            for symbol, price in price_map.items():
                if price <= 0:
                    continue
                cursor.execute("SELECT quantity, avg_entry_price FROM positions WHERE symbol = ?;", (symbol.upper(),))
                row = cursor.fetchone()
                if row and row["quantity"] > 0:
                    qty = row["quantity"]
                    avg_p = row["avg_entry_price"]
                    mkt_val = qty * price
                    unrealized = mkt_val - (qty * avg_p)
                    unrealized_pct = (unrealized / (qty * avg_p) * 100.0) if avg_p > 0 else 0.0
                    cursor.execute("""
                        UPDATE positions
                        SET current_price = ?,
                            market_value = ?,
                            unrealized_pnl = ?,
                            unrealized_pnl_pct = ?,
                            updated_at = ?
                        WHERE symbol = ?;
                    """, (price, mkt_val, unrealized, unrealized_pct, now_str, symbol.upper()))
            conn.commit()

    def record_trade_and_update_portfolio(
        self,
        trade: TradeRecord,
        new_cash: float,
        updated_position: Optional[PositionRecord],
        realized_pnl_delta: float,
        fee_delta: float,
    ) -> int:
        """Atomically execute a trade, update position, deduct/add cash, and record history."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            now_str = datetime.now().isoformat()

            # 1. Insert trade record
            cursor.execute("""
                INSERT INTO trades (symbol, side, quantity, price, fee, slippage, total_cost, timestamp, strategy, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                trade.symbol.upper(),
                trade.side,
                trade.quantity,
                trade.price,
                trade.fee,
                trade.slippage,
                trade.total_cost,
                trade.timestamp or now_str,
                trade.strategy,
                trade.status,
                trade.notes,
            ))
            trade_id = cursor.lastrowid

            # 2. Update position
            if updated_position:
                sym = updated_position.symbol.upper()
                if updated_position.quantity <= 0.000001:
                    # Closed position
                    cursor.execute("""
                        UPDATE positions
                        SET quantity = 0.0,
                            market_value = 0.0,
                            unrealized_pnl = 0.0,
                            unrealized_pnl_pct = 0.0,
                            realized_pnl = realized_pnl + ?,
                            updated_at = ?
                        WHERE symbol = ?;
                    """, (realized_pnl_delta, now_str, sym))
                else:
                    cursor.execute("SELECT id FROM positions WHERE symbol = ?;", (sym,))
                    exists = cursor.fetchone()
                    if exists:
                        cursor.execute("""
                            UPDATE positions
                            SET quantity = ?,
                                avg_entry_price = ?,
                                current_price = ?,
                                market_value = ?,
                                unrealized_pnl = ?,
                                unrealized_pnl_pct = ?,
                                realized_pnl = realized_pnl + ?,
                                stop_loss = ?,
                                take_profit = ?,
                                updated_at = ?
                            WHERE symbol = ?;
                        """, (
                            updated_position.quantity,
                            updated_position.avg_entry_price,
                            updated_position.current_price,
                            updated_position.market_value,
                            updated_position.unrealized_pnl,
                            updated_position.unrealized_pnl_pct,
                            realized_pnl_delta,
                            updated_position.stop_loss,
                            updated_position.take_profit,
                            now_str,
                            sym,
                        ))
                    else:
                        cursor.execute("""
                            INSERT INTO positions (
                                symbol, quantity, avg_entry_price, current_price, market_value,
                                unrealized_pnl, unrealized_pnl_pct, realized_pnl, stop_loss, take_profit, opened_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """, (
                            sym,
                            updated_position.quantity,
                            updated_position.avg_entry_price,
                            updated_position.current_price,
                            updated_position.market_value,
                            updated_position.unrealized_pnl,
                            updated_position.unrealized_pnl_pct,
                            realized_pnl_delta,
                            updated_position.stop_loss,
                            updated_position.take_profit,
                            now_str,
                            now_str,
                        ))

            # 3. Update account cash, realized P&L, fees, peak equity
            cursor.execute("SELECT peak_equity FROM account WHERE id = 1;")
            peak_row = cursor.fetchone()
            curr_peak = peak_row["peak_equity"] if peak_row else settings.default_starting_balance

            # Estimate total equity to update peak
            cursor.execute("SELECT SUM(market_value) as total_mv FROM positions WHERE quantity > 0;")
            mv_row = cursor.fetchone()
            total_mv = mv_row["total_mv"] if mv_row and mv_row["total_mv"] else 0.0
            curr_equity = new_cash + total_mv
            new_peak = max(curr_peak, curr_equity)

            cursor.execute("""
                UPDATE account
                SET cash_balance = ?,
                    realized_pnl = realized_pnl + ?,
                    total_fees_paid = total_fees_paid + ?,
                    peak_equity = ?,
                    updated_at = ?
                WHERE id = 1;
            """, (new_cash, realized_pnl_delta, fee_delta, new_peak, now_str))

            conn.commit()

        # Cloud synchronization with Supabase
        if self.supabase and self.supabase.enabled:
            try:
                self.supabase.sync_trade(trade)
                if updated_position:
                    self.supabase.sync_position(updated_position)
                self.supabase.sync_account(self.get_account_state())
            except Exception:
                pass

        return trade_id

    def get_trades(self, limit: int = 100) -> List[TradeRecord]:
        """Fetch historical trades ordered by timestamp descending."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?;", (limit,))
            rows = cursor.fetchall()
            trades = []
            for r in rows:
                trades.append(TradeRecord(
                    id=r["id"],
                    symbol=r["symbol"],
                    side=r["side"],
                    quantity=r["quantity"],
                    price=r["price"],
                    fee=r["fee"],
                    slippage=r["slippage"],
                    total_cost=r["total_cost"],
                    timestamp=r["timestamp"],
                    strategy=r["strategy"],
                    status=r["status"],
                    notes=r["notes"] or "",
                ))
            return trades

    def get_today_realized_loss(self) -> float:
        """Calculate total losses incurred today for daily loss limit checking."""
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            # Calculate from closed trades / realized pnl today
            cursor.execute("""
                SELECT realized_pnl FROM positions
                WHERE updated_at LIKE ? AND realized_pnl < 0;
            """, (f"{today_prefix}%",))
            rows = cursor.fetchall()
            total_loss = sum(abs(r["realized_pnl"]) for r in rows)
            return total_loss
