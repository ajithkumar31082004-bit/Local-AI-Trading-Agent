"""Append-only SQLite journal for signals, skipped setups, and paper results with CSV export."""
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import json
import sqlite3
import pandas as pd
from config.orb_settings import settings

CONFIG_VERSION = "1.0.0-orb-ema20-vol"


class ORBJournal:
    """Persistent SQLite journal recording strategy decisions, checklists, and outcomes."""

    def __init__(self, path: Path = settings.journal_path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orb_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    orb_high REAL,
                    orb_low REAL,
                    entry REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    risk_amount REAL,
                    fees REAL,
                    slippage REAL,
                    config_version TEXT NOT NULL,
                    checklist_json TEXT,
                    payload_json TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_orb_events_timestamp ON orb_events(timestamp)")

    def log(
        self,
        event_type: str,
        signal: Any,
        symbol: str = settings.symbol,
        extra: Optional[Dict[str, Any]] = None,
    ):
        """Record an event, signal, skipped setup, or paper-trade outcome."""
        payload = signal.to_dict() if hasattr(signal, "to_dict") else dict(signal)
        if extra:
            payload.update(extra)

        ts = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
        direction = str(payload.get("direction", "NO TRADE"))
        reason = str(payload.get("reason", ""))
        orb_high = payload.get("orb_high")
        orb_low = payload.get("orb_low")
        entry = payload.get("entry")
        sl = payload.get("sl")
        tp = payload.get("tp")

        risk_data = payload.get("risk") or {}
        risk_amount = risk_data.get("estimated_loss_at_sl")
        fees = risk_data.get("estimated_fees")
        slippage = risk_data.get("estimated_spread_slippage")

        checklist_json = json.dumps(payload.get("checklist", {}), default=str)
        payload_json = json.dumps(payload, default=str)

        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO orb_events (
                    timestamp, event_type, symbol, direction, reason,
                    orb_high, orb_low, entry, stop_loss, take_profit,
                    risk_amount, fees, slippage, config_version,
                    checklist_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(ts),
                    event_type,
                    symbol,
                    direction,
                    reason,
                    orb_high,
                    orb_low,
                    entry,
                    sl,
                    tp,
                    risk_amount,
                    fees,
                    slippage,
                    CONFIG_VERSION,
                    checklist_json,
                    payload_json,
                ),
            )

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent events sorted by id descending."""
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM orb_events ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def export_csv(self, output_path: str | Path) -> Path:
        """Export all journal records to a CSV file."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            df = pd.read_sql_query("SELECT * FROM orb_events ORDER BY id ASC", conn)
        df.to_csv(out, index=False)
        return out
