"""Unit tests for Delta Exchange client and HMAC authentication."""

import pytest
from market_data.delta_exchange import DeltaExchangeClient


def test_delta_exchange_signature_generation():
    client = DeltaExchangeClient(
        api_key="test_key",
        api_secret="test_secret",
        base_url="https://india.delta.exchange",
        enabled=True,
    )
    sig = client._generate_signature("GET", "/v2/wallet/balances", "1700000000")
    assert len(sig) == 64  # SHA256 hex string length


def test_delta_exchange_disabled():
    client = DeltaExchangeClient(enabled=False)
    status = client.check_connection()
    assert status["available"] is False
    assert status["authenticated"] is False


def test_delta_exchange_change_order_uses_timeout(monkeypatch):
    import requests
    from unittest.mock import MagicMock

    captured = {}

    def mock_put(url, data=None, headers=None, timeout=None):
        captured["timeout"] = timeout
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "result": {"id": "123"}}
        return resp

    monkeypatch.setattr(requests, "put", mock_put)
    client = DeltaExchangeClient(api_key="test_key", api_secret="test_secret", enabled=True, timeout=7)
    res = client.change_order(order_id="123", product_id=131253, stop_price=3560.0)
    assert res["success"] is True
    assert captured["timeout"] == 7
