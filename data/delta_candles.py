"""Public, unauthenticated Delta Exchange India OHLCV client with bounded retries and gap detection."""
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple
import time
import requests
import pandas as pd
from config.orb_settings import settings

CANDLE_URL_PATH = "/v2/history/candles"
RESOLUTION_SECONDS = 300  # 5 minutes in seconds
MAX_CANDLES_PER_REQUEST = 2000


class DeltaCandleError(RuntimeError):
    """Raised when Delta Exchange candle fetching fails."""
    pass


def detect_missing_candles(frame: pd.DataFrame, expected_seconds: int = RESOLUTION_SECONDS) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Detect gaps in a continuous 5-minute candle series.
    
    Returns a list of (expected_start, actual_next) intervals for each missing candle sequence.
    """
    if frame.empty or len(frame) < 2:
        return []
    diffs = frame.index.to_series().diff().iloc[1:]
    expected_delta = pd.Timedelta(seconds=expected_seconds)
    gap_mask = diffs > expected_delta
    gaps = []
    for ts, is_gap in gap_mask.items():
        if is_gap:
            idx_loc = frame.index.get_loc(ts)
            prev_ts = frame.index[idx_loc - 1]
            gaps.append((prev_ts + expected_delta, ts))
    return gaps


def fetch_5m_candles(
    symbol: str = "XAUTUSD",
    lookback_candles: int = 500,
    end_time: Optional[datetime] = None,
    max_retries: int = 3,
    initial_retry_delay: float = 0.5,
) -> pd.DataFrame:
    """Fetch completed 5m OHLCV candles from Delta Exchange India public API with bounded retries.
    
    - Strips incomplete currently-forming candles.
    - Removes duplicate and out-of-order bars.
    - Stores index as UTC Timestamp.
    - Bounded retries on transient connection failure or HTTP 429 rate limits.
    """
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
        params = {
            "resolution": "5m",
            "symbol": symbol.upper(),
            "start": start_ts,
            "end": end_ts,
        }

        success = False
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(
                    f"{settings.api_base_url}{CANDLE_URL_PATH}",
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": "LocalAITradingAgent/1.0"},
                    timeout=settings.request_timeout_seconds,
                )
                if response.status_code == 429:
                    if attempt == max_retries:
                        raise DeltaCandleError("Delta API rate limit (HTTP 429); wait before retrying.")
                    time.sleep(initial_retry_delay * (2 ** (attempt - 1)))
                    continue

                response.raise_for_status()
                payload = response.json()

                if payload.get("success") is False:
                    err_msg = payload.get("error", payload)
                    raise DeltaCandleError(f"Delta API rejected candle request: {err_msg}")

                rows = payload.get("result", [])
                if rows:
                    chunks.extend(rows)
                    remaining -= len(rows)
                    oldest = min(int(row["time"]) for row in rows if "time" in row)
                    if oldest <= start_ts or len(rows) < count:
                        remaining = 0  # Reached earliest available data
                    end_ts = oldest
                else:
                    remaining = 0

                success = True
                break

            except requests.RequestException as exc:
                last_error = exc
                if attempt == max_retries:
                    raise DeltaCandleError(f"Delta candle request failed after {max_retries} retries: {exc}") from exc
                time.sleep(initial_retry_delay * (2 ** (attempt - 1)))

        if not success and last_error:
            raise DeltaCandleError(f"Delta candle request failed: {last_error}") from last_error

        time.sleep(0.05)

    if not chunks:
        raise DeltaCandleError(
            f"No candles returned for {symbol.upper()}. Check instrument symbol, date range, or exchange availability."
        )

    frame = pd.DataFrame(chunks).drop_duplicates(subset=["time"]).sort_values("time")
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise DeltaCandleError(f"Unexpected candle schema; missing {sorted(required - set(frame.columns))}")

    frame.index = pd.to_datetime(frame.pop("time").astype("int64"), unit="s", utc=True)
    frame.index.name = "timestamp"
    frame = frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna()

    # Filter out still-forming bar: bar starting at T completes at T + 300s.
    now_utc = datetime.now(timezone.utc) if end_time is None else end_time.astimezone(timezone.utc)
    frame = frame[frame.index + timedelta(seconds=RESOLUTION_SECONDS) <= pd.Timestamp(now_utc)]

    if frame.empty:
        raise DeltaCandleError("No completed 5-minute candles are available yet.")

    return frame
