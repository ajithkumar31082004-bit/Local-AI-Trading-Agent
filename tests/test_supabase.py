"""Unit tests for Supabase Client integration and resilience."""

import pytest
from database.supabase_client import SupabaseClient
from database.models import TradeRecord, PositionRecord


def test_supabase_client_initialization():
    client = SupabaseClient(
        url="https://wblqpctvipalomkypzdz.supabase.co",
        key="test_key",
        enabled=True,
    )
    assert client.url == "https://wblqpctvipalomkypzdz.supabase.co"
    assert client.enabled is True


def test_supabase_disabled_graceful_handling():
    client = SupabaseClient(enabled=False)
    assert client.check_connection()["available"] is False
    assert client.sync_account({}) is False
    assert client.sync_trade(TradeRecord()) is False
    assert client.sync_position(PositionRecord()) is False
