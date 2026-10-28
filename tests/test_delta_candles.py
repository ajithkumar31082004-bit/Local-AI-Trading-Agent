"""Unit tests for Delta Exchange public market data client with retries and gap detection."""
from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest
from data.delta_candles import fetch_5m_candles, detect_missing_candles, DeltaCandleError


class MockResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def test_delta_candle_request_uses_official_path_and_maps_ohlcv(monkeypatch):
    stamp = int(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp())
    called = {}

    def fake_get(url, **kwargs):
        called["url"] = url
        called["params"] = kwargs["params"]
        return MockResponse({
            "success": True,
            "result": [
                {"time": stamp, "open": "2600", "high": "2605", "low": "2595", "close": "2602", "volume": "15"}
            ]
        })

    monkeypatch.setattr("data.delta_candles.requests.get", fake_get)
    frame = fetch_5m_candles("XAUTUSD", 1, datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc))
    assert called["url"].endswith("/v2/history/candles")
    assert called["params"]["resolution"] == "5m"
    assert called["params"]["symbol"] == "XAUTUSD"
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert frame.iloc[0]["Close"] == 2602.0
    assert frame.index.tz is not None, "Index must be timezone-aware (UTC)"


def test_delta_rate_limit_and_retries(monkeypatch):
    attempts = []

    def fake_get(url, **kwargs):
        attempts.append(len(attempts) + 1)
        return MockResponse({}, status_code=429)

    monkeypatch.setattr("data.delta_candles.requests.get", fake_get)
    monkeypatch.setattr("data.delta_candles.time.sleep", lambda s: None)

    with pytest.raises(DeltaCandleError, match="rate limit"):
        fetch_5m_candles("XAUTUSD", 1, datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc), max_retries=3)

    assert len(attempts) == 3, "Should have attempted exactly 3 times before failing"


def test_detect_missing_candles():
    # Create continuous series from 09:00 to 09:15 (4 bars: 00, 05, 10, 15)
    idx_continuous = pd.date_range("2026-01-01 09:00", periods=4, freq="5min", tz="UTC")
    df_continuous = pd.DataFrame({"Close": [1, 2, 3, 4]}, index=idx_continuous)
    assert detect_missing_candles(df_continuous) == []

    # Series with a 10-minute gap between 09:05 and 09:15 (missing 09:10)
    idx_gap = pd.DatetimeIndex([
        pd.Timestamp("2026-01-01 09:00:00+0000", tz="UTC"),
        pd.Timestamp("2026-01-01 09:05:00+0000", tz="UTC"),
        pd.Timestamp("2026-01-01 09:15:00+0000", tz="UTC"),
    ])
    df_gap = pd.DataFrame({"Close": [1, 2, 3]}, index=idx_gap)
    gaps = detect_missing_candles(df_gap)
    assert len(gaps) == 1
    assert gaps[0][0] == pd.Timestamp("2026-01-01 09:10:00+0000", tz="UTC")


def test_incomplete_candle_excluded(monkeypatch):
    # A bar starting at 10:00:00 completes at 10:05:00. If now is 10:02:00, it is still forming.
    t_start = int(datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc).timestamp())
    now_dt = datetime(2026, 1, 1, 10, 2, tzinfo=timezone.utc)

    def fake_get(url, **kwargs):
        return MockResponse({
            "success": True,
            "result": [
                {"time": t_start, "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": "10"}
            ]
        })

    monkeypatch.setattr("data.delta_candles.requests.get", fake_get)
    with pytest.raises(DeltaCandleError, match="No completed 5-minute candles"):
        fetch_5m_candles("XAUTUSD", 1, end_time=now_dt)
