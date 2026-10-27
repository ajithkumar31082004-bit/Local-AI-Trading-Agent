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
