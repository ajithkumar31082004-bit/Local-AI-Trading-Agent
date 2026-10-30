"""Append-only SQLite journal for signals, skipped setups, and paper results with CSV export and Telegram alerts."""
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
import tempfile
import pandas as pd
import requests
from config.orb_settings import settings

logger = logging.getLogger(__name__)

CONFIG_VERSION = "1.0.0-orb-ema20-vol"


def send_telegram_alert(
    message: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    timeout: int = 5,
) -> bool:
    """Send alert to Telegram chat if credentials are configured in .env.
    
    Off by default if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not provided.
    Token is never logged.
    """
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    target_chat = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not target_chat:
        return False  # Disabled by default

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        res = requests.post(url, json=payload, timeout=timeout)
        if res.status_code == 200:
            logger.info("Telegram alert delivered successfully.")
            return True
        logger.warning("Telegram alert failed with HTTP %d: %s", res.status_code, res.text[:100])
        return False
    except Exception as exc:
        logger.warning("Could not send Telegram alert: %s", exc)
        return False


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
        send_telegram: bool = True,
    ):
        """Record an event, signal, skipped setup, or paper-trade outcome and optionally send alert."""
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

        # Trigger optional Telegram notification on valid completed signal
        if send_telegram and direction in {"LONG", "SHORT"}:
            msg = (
                f"🔔 <b>ORB Signal Alert: {symbol} {direction}</b>\n"
                f"<b>Entry:</b> ${entry}\n"
                f"<b>Stop-Loss:</b> ${sl}\n"
                f"<b>Take-Profit:</b> ${tp}\n"
                f"<b>Reason:</b> {reason}\n"
                f"<i>Educational tool, not financial advice. Alert-only.</i>"
            )
            send_telegram_alert(msg)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent events sorted by id descending."""
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM orb_events ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def export_csv(self, output_path: str | Path, _safe_root: Optional[Path] = None) -> Path:
        """Export all journal records to a CSV file.

        output_path is resolved and restricted to allowed directories (workspace or system temp directory)
        to prevent path traversal when the argument originates from untrusted input.
        """
        out = Path(output_path).resolve()
        safe_bases = [Path.cwd().resolve(), Path(tempfile.gettempdir()).resolve()]
        if _safe_root:
            safe_bases.append(Path(_safe_root).resolve())
        if not any(out.is_relative_to(base) for base in safe_bases):
            raise ValueError(f"export_csv path outside allowed directories: {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            df = pd.read_sql_query("SELECT * FROM orb_events ORDER BY id ASC", conn)
        df.to_csv(out, index=False)
        return out
