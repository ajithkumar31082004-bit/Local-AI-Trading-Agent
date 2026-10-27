from datetime import datetime, timezone
import pandas as pd
import pytest
from data.delta_candles import fetch_5m_candles, DeltaCandleError

class Response:
    status_code = 200
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload

def test_delta_candle_request_uses_official_path_and_maps_ohlcv(monkeypatch):
    stamp = int(datetime(2025,1,1,0,0,tzinfo=timezone.utc).timestamp())
    called = {}
    def fake_get(url, **kwargs):
        called["url"] = url; called["params"] = kwargs["params"]
        return Response({"success": True, "result": [
            {"time": stamp, "open":"2600","high":"2605","low":"2595","close":"2602","volume":"15"}
        ]})
    monkeypatch.setattr("data.delta_candles.requests.get", fake_get)
    frame = fetch_5m_candles("XAUTUSD", 1, datetime(2025,1,1,0,10,tzinfo=timezone.utc))
    assert called["url"].endswith("/v2/history/candles")
    assert called["params"]["resolution"] == "5m"
    assert called["params"]["symbol"] == "XAUTUSD"
    assert list(frame.columns) == ["Open","High","Low","Close","Volume"]
    assert frame.iloc[0]["Close"] == 2602

def test_delta_rate_limit_is_explicit(monkeypatch):
    def fake_get(url, **kwargs):
        return Response({"success":False,"error":"rate limited"})
    monkeypatch.setattr("data.delta_candles.requests.get", fake_get)
    # HTTP 429 must be distinguished from an ordinary API error.
    def rate_limited(url, **kwargs):
        r=Response({})
        r.status_code=429
        return r
    monkeypatch.setattr("data.delta_candles.requests.get", rate_limited)
    with pytest.raises(DeltaCandleError, match="rate limit"):
        fetch_5m_candles("XAUTUSD", 1, datetime(2025,1,1,0,10,tzinfo=timezone.utc))
