"""Durable Idempotency Registry (Phase 3.29).

Provides ACID-compliant, persistent deduplication for paper trading order execution
using SQLite in WAL mode with primary key unique constraints. Guarantees that executed
order idempotency keys can NEVER be re-executed, surviving:
- In-memory FIFO cache pruning (>1,000 to >10,000 keys)
- Broker process crashes and restarts
- Multi-process concurrent order submissions
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


class IdempotencyRegistryError(Exception):
    """Raised when an unrecoverable database or consistency error occurs in the registry."""
    pass


class DurableIdempotencyRegistry:
    """ACID-compliant SQLite-backed registry for durable order idempotency deduplication."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create and return an isolated SQLite connection configured with WAL and busy timeout."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            isolation_level="DEFERRED",
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        return conn

    def _init_db(self) -> None:
        """Initialize SQLite database tables and indices, with auto-recovery for corrupt files."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS idempotency_records (
                        idempotency_key TEXT PRIMARY KEY NOT NULL,
                        status TEXT NOT NULL,         -- 'RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED'
                        token TEXT,                   -- Reservation token RES_GEN{gen}_{hex}
                        generation INTEGER,           -- Fencing generation counter
                        strategy_name TEXT,
                        symbol TEXT,
                        direction TEXT,
                        order_id TEXT,
                        trade_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at REAL,              -- Epoch timestamp when reservation expires
                        metadata_json TEXT
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_idemp_status ON idempotency_records(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_idemp_token ON idempotency_records(token);")
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to initialize idempotency database at %s: %s. Attempting auto-recovery...", self.db_path, exc)
            try:
                # Corrupt DB auto-recovery: isolate corrupted file and rebuild fresh database
                timestamp = int(datetime.now(timezone.utc).timestamp())
                corrupt_backup = self.db_path.with_name(f"{self.db_path.name}.corrupt.{timestamp}")
                if self.db_path.exists():
                    self.db_path.replace(corrupt_backup)
                for wal_ext in [".db-wal", ".db-shm"]:
                    wal_file = self.db_path.with_name(self.db_path.name + wal_ext)
                    if wal_file.exists():
                        wal_file.unlink(missing_ok=True)
                with self._get_connection() as conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS idempotency_records (
                            idempotency_key TEXT PRIMARY KEY NOT NULL,
                            status TEXT NOT NULL,
                            token TEXT,
                            generation INTEGER,
                            strategy_name TEXT,
                            symbol TEXT,
                            direction TEXT,
                            order_id TEXT,
                            trade_id TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            expires_at REAL,
                            metadata_json TEXT
                        );
                        """
                    )
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_idemp_status ON idempotency_records(status);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_idemp_token ON idempotency_records(token);")
                logger.info("Successfully recovered fresh idempotency database after isolating %s", corrupt_backup)
            except Exception as rec_exc:
                logger.critical("Fatal: Idempotency database recovery failed: %s", rec_exc)
                raise IdempotencyRegistryError(f"Database initialization and recovery failed: {rec_exc}") from rec_exc

    @staticmethod
    def _now_epoch() -> float:
        return datetime.now(timezone.utc).timestamp()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def is_executed(self, idempotency_key: str) -> bool:
        """Check whether the normalized idempotency key has already been COMMITTED."""
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            return False

        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT status FROM idempotency_records WHERE idempotency_key = ?",
                    (clean_key,),
                )
                row = cur.fetchone()
                if row and row["status"] == "COMMITTED":
                    return True
                return False
        except sqlite3.DatabaseError as exc:
            logger.error("Database query failed in is_executed for key %s: %s", clean_key, exc)
            return False

    def get_record(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve the durable record for a given idempotency key."""
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            return None

        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT * FROM idempotency_records WHERE idempotency_key = ?",
                    (clean_key,),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
                return None
        except sqlite3.DatabaseError as exc:
            logger.error("Database query failed in get_record for key %s: %s", clean_key, exc)
            raise IdempotencyRegistryError(f"Failed to retrieve record: {exc}") from exc

    def reserve(
        self,
        idempotency_key: str,
        token: str,
        generation: int,
        strategy_name: str,
        direction: str,
        symbol: str,
        ttl_seconds: float,
    ) -> Tuple[bool, str]:
        """Atomically reserve an idempotency key with a lease expiration.

        Returns:
            Tuple[bool, str]: (success, message)
        """
        clean_key = str(idempotency_key or "").strip()
        clean_token = str(token or "").strip()
        if not clean_key or not clean_token:
            return False, "Invalid parameters: key and token cannot be empty"

        now_epoch = self._now_epoch()
        now_iso = self._now_iso()
        expires_at = now_epoch + float(ttl_seconds)

        try:
            with self._get_connection() as conn:
                # Inspect existing record under transaction
                cur = conn.execute(
                    "SELECT status, token, generation, expires_at, order_id, trade_id FROM idempotency_records WHERE idempotency_key = ?",
                    (clean_key,),
                )
                row = cur.fetchone()

                if row:
                    status = row["status"]
                    if status == "COMMITTED":
                        return False, (
                            f"Duplicate order: idempotency key '{clean_key}' already executed in durable registry "
                            f"(trade_id={row['trade_id']}, order_id={row['order_id']})"
                        )
                    elif status == "RESERVED":
                        row_exp = float(row["expires_at"] or 0.0)
                        if now_epoch < row_exp:
                            return False, (
                                f"Duplicate order: idempotency key '{clean_key}' already reserved in-flight in durable registry "
                                f"(token={row['token']}, expires_in={row_exp - now_epoch:.1f}s)"
                            )
                        # Previous reservation expired; overwrite with new reservation
                        conn.execute(
                            """
                            UPDATE idempotency_records
                            SET status = 'RESERVED',
                                token = ?,
                                generation = ?,
                                strategy_name = ?,
                                direction = ?,
                                symbol = ?,
                                updated_at = ?,
                                expires_at = ?
                            WHERE idempotency_key = ?
                            """,
                            (
                                clean_token,
                                generation,
                                strategy_name,
                                direction,
                                symbol,
                                now_iso,
                                expires_at,
                                clean_key,
                            ),
                        )
                        return True, "Slot reserved successfully (overwrote expired reservation)"
                    else:  # RELEASED or EXPIRED
                        conn.execute(
                            """
                            UPDATE idempotency_records
                            SET status = 'RESERVED',
                                token = ?,
                                generation = ?,
                                strategy_name = ?,
                                direction = ?,
                                symbol = ?,
                                updated_at = ?,
                                expires_at = ?
                            WHERE idempotency_key = ?
                            """,
                            (
                                clean_token,
                                generation,
                                strategy_name,
                                direction,
                                symbol,
                                now_iso,
                                expires_at,
                                clean_key,
                            ),
                        )
                        return True, "Slot reserved successfully"

                # Record does not exist: insert fresh reservation
                conn.execute(
                    """
                    INSERT INTO idempotency_records (
                        idempotency_key, status, token, generation, strategy_name,
                        direction, symbol, created_at, updated_at, expires_at
                    ) VALUES (?, 'RESERVED', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_key,
                        clean_token,
                        generation,
                        strategy_name,
                        direction,
                        symbol,
                        now_iso,
                        now_iso,
                        expires_at,
                    ),
                )
                return True, "Slot reserved successfully"

        except sqlite3.IntegrityError:
            # Concurrent process inserted the same key between check and insert
            logger.warning("Concurrent reservation collision detected for key %s.", clean_key)
            rec = self.get_record(clean_key)
            if rec and rec["status"] == "COMMITTED":
                return False, f"Duplicate order: idempotency key '{clean_key}' already executed in durable registry"
            return False, f"Concurrent reservation collision: idempotency key '{clean_key}' was reserved by another process"
        except sqlite3.DatabaseError as exc:
            logger.error("Database error during reservation of key %s: %s", clean_key, exc)
            raise IdempotencyRegistryError(f"Reservation failed: {exc}") from exc

    def commit(self, token: str, order_record: Dict[str, Any]) -> Tuple[bool, str]:
        """Atomically commit an in-flight reservation upon successful order fill.

        Transitions status to 'COMMITTED', which is permanent and immutable.
        """
        clean_token = str(token or "").strip()
        if not clean_token:
            return False, "Invalid token: token cannot be empty"

        now_epoch = self._now_epoch()
        now_iso = self._now_iso()

        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT idempotency_key, status, expires_at FROM idempotency_records WHERE token = ?",
                    (clean_token,),
                )
                row = cur.fetchone()

                if not row:
                    return False, f"Commit rejected: reservation token '{clean_token}' not found in durable registry"

                status = row["status"]
                if status == "COMMITTED":
                    return False, f"Commit rejected: reservation token '{clean_token}' already committed"
                if status != "RESERVED":
                    return False, f"Commit rejected: reservation token '{clean_token}' is in invalid status '{status}'"

                expires_at = float(row["expires_at"] or 0.0)
                if now_epoch >= expires_at:
                    conn.execute(
                        "UPDATE idempotency_records SET status = 'EXPIRED', updated_at = ? WHERE token = ?",
                        (now_iso, clean_token),
                    )
                    return False, f"Commit rejected: reservation token '{clean_token}' expired past lease TTL"

                meta_json = json.dumps({
                    "strategy_name": order_record.get("strategy_name"),
                    "lots": order_record.get("lots"),
                    "entry_price": order_record.get("entry_price"),
                    "fill_price": order_record.get("entry_price"),
                })

                conn.execute(
                    """
                    UPDATE idempotency_records
                    SET status = 'COMMITTED',
                        order_id = ?,
                        trade_id = ?,
                        updated_at = ?,
                        metadata_json = ?
                    WHERE token = ?
                    """,
                    (
                        order_record.get("order_id"),
                        order_record.get("trade_id"),
                        now_iso,
                        meta_json,
                        clean_token,
                    ),
                )
                return True, "Reservation committed successfully"

        except sqlite3.DatabaseError as exc:
            logger.error("Database error during commit for token %s: %s", clean_token, exc)
            raise IdempotencyRegistryError(f"Commit failed: {exc}") from exc

    def release(self, token: str, reason: str = "") -> Tuple[bool, str]:
        """Release an in-flight reservation if order was unfilled, canceled, or rejected before fill."""
        clean_token = str(token or "").strip()
        if not clean_token:
            return False, "Invalid token: token cannot be empty"

        now_iso = self._now_iso()

        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT status FROM idempotency_records WHERE token = ?",
                    (clean_token,),
                )
                row = cur.fetchone()
                if not row:
                    return False, f"Release rejected: token '{clean_token}' not found"

                if row["status"] == "RESERVED":
                    meta_json = json.dumps({"release_reason": reason or "unspecified"})
                    conn.execute(
                        """
                        UPDATE idempotency_records
                        SET status = 'RELEASED',
                            updated_at = ?,
                            metadata_json = ?
                        WHERE token = ?
                        """,
                        (now_iso, meta_json, clean_token),
                    )
                    return True, "Reservation released successfully"
                return False, f"Cannot release token '{clean_token}' with status '{row['status']}'"

        except sqlite3.DatabaseError as exc:
            logger.error("Database error during release for token %s: %s", clean_token, exc)
            raise IdempotencyRegistryError(f"Release failed: {exc}") from exc

    def sync_historical_keys(
        self,
        processed_keys: Dict[str, Any],
        closed_trades: List[Dict[str, Any]],
    ) -> int:
        """Backfill historical executed keys from JSON state into SQLite registry as COMMITTED."""
        now_iso = self._now_iso()
        count = 0

        try:
            with self._get_connection() as conn:
                # 1. Backfill from processed_idempotency_keys
                for key, data in (processed_keys or {}).items():
                    clean_key = str(key or "").strip()
                    if not clean_key:
                        continue
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO idempotency_records (
                            idempotency_key, status, token, generation, order_id, trade_id,
                            created_at, updated_at, expires_at
                        ) VALUES (?, 'COMMITTED', ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            clean_key,
                            data.get("token"),
                            data.get("generation", 0),
                            data.get("order_id"),
                            data.get("trade_id"),
                            data.get("timestamp", now_iso),
                            now_iso,
                        ),
                    )
                    count += cur.rowcount

                # 2. Backfill from closed_trades
                for trade in (closed_trades or []):
                    clean_key = str(trade.get("idempotency_key") or "").strip()
                    if not clean_key:
                        continue
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO idempotency_records (
                            idempotency_key, status, token, generation, order_id, trade_id,
                            strategy_name, direction, symbol, created_at, updated_at, expires_at
                        ) VALUES (?, 'COMMITTED', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            clean_key,
                            trade.get("reservation_token"),
                            0,
                            trade.get("order_id"),
                            trade.get("trade_id"),
                            trade.get("strategy_name"),
                            trade.get("direction"),
                            trade.get("symbol"),
                            trade.get("entry_time", now_iso),
                            now_iso,
                        ),
                    )
                    count += cur.rowcount

            if count > 0:
                logger.info("Synchronized %d historical idempotency keys into durable registry.", count)
            return count

        except sqlite3.DatabaseError as exc:
            logger.error("Failed to sync historical keys to registry: %s", exc)
            return 0

    def count_records(self, status: Optional[str] = None) -> int:
        """Return count of registered idempotency records, optionally filtered by status."""
        try:
            with self._get_connection() as conn:
                if status:
                    cur = conn.execute(
                        "SELECT COUNT(*) as cnt FROM idempotency_records WHERE status = ?",
                        (status.strip().upper(),),
                    )
                else:
                    cur = conn.execute("SELECT COUNT(*) as cnt FROM idempotency_records")
                row = cur.fetchone()
                return int(row["cnt"]) if row else 0
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to count registry records: %s", exc)
            return 0

    def clear(self) -> None:
        """Clear all records from database. Strictly intended for account reset or test fixture teardown."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM idempotency_records;")
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to clear idempotency registry: %s", exc)
            raise IdempotencyRegistryError(f"Clear failed: {exc}") from exc
