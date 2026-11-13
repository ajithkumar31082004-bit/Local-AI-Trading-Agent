"""CSV loader for historical OHLCV backtests."""
from pathlib import Path
import pandas as pd

ALIASES = {
    "timestamp": "timestamp", "time": "timestamp", "datetime": "timestamp", "date": "timestamp",
    "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
}

def load_candles_csv(path: str | Path, timezone: str = "Asia/Kolkata") -> pd.DataFrame:
    frame = pd.read_csv(path)
    rename = {col: ALIASES.get(str(col).strip().lower(), col) for col in frame.columns}
    frame = frame.rename(columns=rename)
    if "timestamp" not in frame.columns:
        raise ValueError("CSV needs a timestamp/time/datetime/date column.")
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError(f"CSV is missing OHLCV columns: {missing}")
    idx = pd.to_datetime(frame.pop("timestamp"), errors="coerce")
    if idx.isna().any():
        raise ValueError("CSV contains invalid timestamps.")
    if idx.dt.tz is None:
        idx = idx.dt.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
    else:
        idx = idx.dt.tz_convert(timezone)
    frame.index = pd.DatetimeIndex(idx, name="timestamp")
    frame = frame[required].apply(pd.to_numeric, errors="coerce").dropna().sort_index()
    if frame.index.has_duplicates:
        frame = frame[~frame.index.duplicated(keep="last")]
    if (frame[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (frame["Volume"] < 0).any():
        raise ValueError("Volume cannot be negative.")
    return frame
