"""Unit tests for Supabase Client integration and resilience."""
import os

import pytest
from database.supabase_client import SupabaseClient
from database.models import TradeRecord, PositionRecord

# Read from env so no live project URL is hard-coded in source.
# Falls back to a safe placeholder so CI runs without real credentials.
_TEST_SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://example.supabase.co")


def test_supabase_client_initialization():
    client = SupabaseClient(
        url=_TEST_SUPABASE_URL,
        key="test_key",
        enabled=True,
    )
    assert client.url == _TEST_SUPABASE_URL
    assert client.enabled is True


def test_supabase_disabled_graceful_handling():
    client = SupabaseClient(enabled=False)
    assert client.check_connection()["available"] is False
    assert client.sync_account({}) is False
    assert client.sync_trade(TradeRecord()) is False
    assert client.sync_position(PositionRecord()) is False
