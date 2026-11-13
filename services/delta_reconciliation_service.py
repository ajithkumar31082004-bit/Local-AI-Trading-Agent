"""Persistent order state, idempotency tracking, and exchange reconciliation."""
import sqlite3
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from config.orb_settings import ORBSettings, settings as default_orb_settings

logger = logging.getLogger(__name__)


@dataclass
class LiveOrderRecord:
    client_order_id: str
    exchange_order_id: Optional[str]
    candle_timestamp: str
    symbol: str
    side: str
    size: int
    limit_price: float
    stop_loss_price: Optional[float]
    take_profit_price: Optional[float]
    status: str  # PENDING, ACCEPTED, FILLED, PARTIALLY_FILLED, REJECTED, CANCELLED
    filled_size: int = 0
    avg_fill_price: Optional[float] = None
    created_at: str = ""
    updated_at: str = ""
    error_message: Optional[str] = None
    raw_response: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeltaReconciliationService:
    """Manages transactional SQLite storage for live orders and reconciles with Delta Exchange."""

    def __init__(self, db_path: Optional[Path] = None, cfg: Optional[ORBSettings] = None):
        self.cfg = cfg or default_orb_settings
        self.db_path = Path(db_path or (self.cfg.journal_path.parent / "orb_live_orders.sqlite3"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS live_orders (
                    client_order_id TEXT PRIMARY KEY,
                    exchange_order_id TEXT,
                    candle_timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    limit_price REAL NOT NULL,
                    stop_loss_price REAL,
                    take_profit_price REAL,
                    status TEXT NOT NULL,
                    filled_size INTEGER DEFAULT 0,
                    avg_fill_price REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_message TEXT,
                    raw_response TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_live_orders_candle ON live_orders(candle_timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_live_orders_status ON live_orders(status);")
            conn.commit()

    def is_candle_already_ordered(self, candle_timestamp: str) -> bool:
        """Idempotency check: verify if an order was already placed for this exact candle timestamp."""
        if not candle_timestamp:
            return False
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM live_orders WHERE candle_timestamp = ? AND status NOT IN ('REJECTED', 'CANCELLED');",
                (str(candle_timestamp),)
            ).fetchone()
            return row is not None

    def record_intent(
        self,
        client_order_id: str,
        candle_timestamp: str,
        symbol: str,
        side: str,
        size: int,
        limit_price: float,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> LiveOrderRecord:
        """Register order submission intent before dispatching network request."""
        now_str = datetime.now(timezone.utc).isoformat()
        rec = LiveOrderRecord(
            client_order_id=client_order_id,
            exchange_order_id=None,
            candle_timestamp=candle_timestamp,
            symbol=symbol,
            side=side,
            size=size,
            limit_price=limit_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            status="PENDING",
            created_at=now_str,
            updated_at=now_str,
        )
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO live_orders (
                    client_order_id, exchange_order_id, candle_timestamp, symbol, side,
                    size, limit_price, stop_loss_price, take_profit_price, status,
                    filled_size, avg_fill_price, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                rec.client_order_id, rec.exchange_order_id, rec.candle_timestamp, rec.symbol, rec.side,
                rec.size, rec.limit_price, rec.stop_loss_price, rec.take_profit_price, rec.status,
                rec.filled_size, rec.avg_fill_price, rec.created_at, rec.updated_at,
            ))
            conn.commit()
        return rec

    def update_result(
        self,
        client_order_id: str,
        status: str,
        exchange_order_id: Optional[str] = None,
        filled_size: int = 0,
        avg_fill_price: Optional[float] = None,
        error_message: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update order outcome after exchange response or reconciliation."""
        now_str = datetime.now(timezone.utc).isoformat()
        raw_str = json.dumps(raw_response) if raw_response else None
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE live_orders
                SET status = ?,
                    exchange_order_id = COALESCE(?, exchange_order_id),
                    filled_size = ?,
                    avg_fill_price = ?,
                    error_message = ?,
                    raw_response = ?,
                    updated_at = ?
                WHERE client_order_id = ?;
            """, (status, exchange_order_id, filled_size, avg_fill_price, error_message, raw_str, now_str, client_order_id))
            conn.commit()

    def get_order(self, client_order_id: str) -> Optional[LiveOrderRecord]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM live_orders WHERE client_order_id = ?;", (client_order_id,)).fetchone()
            if not row:
                return None
            return LiveOrderRecord(**dict(row))

    def get_active_orders(self) -> List[LiveOrderRecord]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM live_orders WHERE status IN ('PENDING', 'ACCEPTED', 'PARTIALLY_FILLED') ORDER BY created_at DESC;"
            ).fetchall()
            return [LiveOrderRecord(**dict(r)) for r in rows]

    def reconcile_with_exchange(self, client_order_id: str, client: Any) -> Optional[LiveOrderRecord]:
        """Reconcile a specific order against Delta Exchange via client_order_id lookup."""
        rec = self.get_order(client_order_id)
        if not rec:
            logger.warning("Reconciliation target %s not found in local database.", client_order_id)
            return None

        res = client.get_order_by_client_id(client_order_id)
        if res.get("success") and res.get("result"):
            order_data = res["result"]
            exchange_id = str(order_data.get("id", ""))
            delta_state = str(order_data.get("state", "UNKNOWN")).upper()
            total_size = int(order_data.get("size", rec.size))
            unfilled = int(order_data.get("unfilled_size", 0))
            filled = max(0, total_size - unfilled)
            avg_fill = float(order_data["avg_fill_price"]) if order_data.get("avg_fill_price") else None

            # Normalize Delta order state
            status_map = {
                "OPEN": "ACCEPTED",
                "FILLED": "FILLED",
                "CANCELLED": "CANCELLED",
                "REJECTED": "REJECTED",
                "PARTIALLY_FILLED": "PARTIALLY_FILLED",
            }
            norm_status = status_map.get(delta_state, delta_state)

            self.update_result(
                client_order_id=client_order_id,
                status=norm_status,
                exchange_order_id=exchange_id,
                filled_size=filled,
                avg_fill_price=avg_fill,
                raw_response=order_data,
            )
            logger.info("Order %s reconciled to %s (Exchange ID: %s)", client_order_id, norm_status, exchange_id)
            return self.get_order(client_order_id)

        if res.get("status_code") == 404:
            # Order never existed on exchange
            err = f"Exchange 404: Order {client_order_id} not found on Delta Exchange."
            logger.warning(err)
            self.update_result(
                client_order_id=client_order_id,
                status="NOT_FOUND_ON_EXCHANGE",
                error_message=err,
            )
            return self.get_order(client_order_id)

        logger.warning("Reconciliation lookup failed for %s: %s", client_order_id, res.get("error"))
        return rec

    def reconcile_all_pending(self, client: Any) -> List[LiveOrderRecord]:
        """Query and reconcile all orders in PENDING, PENDING_RECONCILIATION, or TIMEOUT_HOLD states."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT client_order_id FROM live_orders WHERE status IN ('PENDING', 'PENDING_RECONCILIATION', 'TIMEOUT_HOLD');"
            ).fetchall()

        results = []
        for r in rows:
            cid = r["client_order_id"]
            updated = self.reconcile_with_exchange(cid, client)
            if updated:
                results.append(updated)
        return results

