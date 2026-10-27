"""Append-only SQLite journal for signals, skipped setups and paper results."""
import json, sqlite3
from pathlib import Path
from datetime import datetime, timezone
from config.orb_settings import settings

class ORBJournal:
    def __init__(self, path: Path = settings.journal_path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS orb_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL,
                reason TEXT NOT NULL, payload_json TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_orb_events_timestamp ON orb_events(timestamp)")
    def log(self, event_type: str, signal, symbol: str = settings.symbol, extra: dict | None = None):
        payload=signal.to_dict() if hasattr(signal,"to_dict") else dict(signal)
        if extra: payload.update(extra)
        ts=payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO orb_events(timestamp,event_type,symbol,direction,reason,payload_json) VALUES(?,?,?,?,?,?)",
                (str(ts),event_type,symbol,str(payload.get("direction","NO TRADE")),str(payload.get("reason","")),json.dumps(payload,default=str)))
    def recent(self, limit: int = 50):
        with sqlite3.connect(self.path) as c:
            c.row_factory=sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM orb_events ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall()]
