"""Public, unauthenticated Delta Exchange India OHLCV client."""
from datetime import datetime, timedelta, timezone
from typing import Optional
import time
import requests
import pandas as pd
from config.orb_settings import settings

CANDLE_URL_PATH = "/v2/history/candles"
RESOLUTION_SECONDS = 300
MAX_CANDLES_PER_REQUEST = 2000

class DeltaCandleError(RuntimeError):
    pass

def fetch_5m_candles(symbol: str = "XAUTUSD", lookback_candles: int = 500,
                     end_time: Optional[datetime] = None) -> pd.DataFrame:
    """Fetch 5m candles; chunks requests to the documented 2,000-candle limit."""
    if lookback_candles < 1:
        raise ValueError("lookback_candles must be positive")
    end_dt = end_time or datetime.now(timezone.utc)
    if end_dt.tzinfo is None:
        raise ValueError("end_time must be timezone-aware")
    end_ts = int(end_dt.astimezone(timezone.utc).timestamp())
    remaining = lookback_candles
    chunks = []
    while remaining > 0:
        count = min(remaining, MAX_CANDLES_PER_REQUEST)
        start_ts = end_ts - count * RESOLUTION_SECONDS
        params = {"resolution": "5m", "symbol": symbol.upper(), "start": start_ts, "end": end_ts}
        try:
            response = requests.get(
                f"{settings.api_base_url}{CANDLE_URL_PATH}",
                params=params,
                headers={"Accept": "application/json", "User-Agent": "LocalAITradingAgent/1.0"},
                timeout=settings.request_timeout_seconds,
            )
            if response.status_code == 429:
                raise DeltaCandleError("Delta API rate limit (HTTP 429); wait before retrying.")
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DeltaCandleError(f"Delta candle request failed: {exc}") from exc
        if payload.get("success") is False:
            raise DeltaCandleError(f"Delta API rejected candle request: {payload}")
        rows = payload.get("result", [])
        if not rows:
            break
        chunks.extend(rows)
        remaining -= len(rows)
        oldest = min(int(row["time"]) for row in rows if "time" in row)
        if oldest <= start_ts or len(rows) < count:
            break
        end_ts = oldest
        time.sleep(0.1)
    if not chunks:
        raise DeltaCandleError(f"No candles returned for {symbol.upper()}. Check symbol, time range, and API status.")
    frame = pd.DataFrame(chunks).drop_duplicates(subset=["time"]).sort_values("time")
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise DeltaCandleError(f"Unexpected candle schema; missing {sorted(required - set(frame.columns))}")
    frame.index = pd.to_datetime(frame.pop("time").astype("int64"), unit="s", utc=True)
    frame.index.name = "timestamp"
    frame = frame.rename(columns={"open":"Open", "high":"High", "low":"Low", "close":"Close", "volume":"Volume"})
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna()
    # Exclude a still-forming bar. A candle is complete five minutes after its start timestamp.
    now_utc = datetime.now(timezone.utc)
    if end_time is not None:
        now_utc = end_time.astimezone(timezone.utc)
    frame = frame[frame.index + timedelta(seconds=RESOLUTION_SECONDS) <= pd.Timestamp(now_utc)]
    if frame.empty:
        raise DeltaCandleError("No completed 5-minute candles are available yet.")
    return frame

