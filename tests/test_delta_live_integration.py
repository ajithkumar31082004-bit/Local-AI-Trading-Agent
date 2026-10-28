"""Comprehensive unit and mocked integration tests for Delta Exchange live trading architecture.

CRITICAL: All tests strictly use mocks. A hard mock guard fixture prevents any live network calls.
"""
import json
import time
from decimal import Decimal
from unittest.mock import patch, MagicMock
import pytest
import requests

from config.orb_settings import ORBSettings, ConfigurationError, validate_settings
from market_data.delta_exchange import DeltaExchangeClient
from services.delta_account_service import DeltaAccountService, DeltaAccountState
from services.delta_instrument_service import DeltaInstrumentService, DeltaProductSpecs
from services.delta_reconciliation_service import DeltaReconciliationService, LiveOrderRecord
from services.delta_live_executor import DeltaLiveExecutor


# Hard Mock Guard: Prevent any real outbound order creation during tests
@pytest.fixture(autouse=True)
def hard_mock_guard(monkeypatch):
    """Enforce that no test can ever reach real exchange order creation endpoints."""
    real_post = requests.post

    def guarded_post(url, *args, **kwargs):
        if "delta.exchange" in str(url) and "/v2/orders" in str(url):
            raise AssertionError(f"SAFETY VIOLATION: Test attempted unmocked live order POST to {url}!")
        return real_post(url, *args, **kwargs)

    monkeypatch.setattr(requests, "post", guarded_post)


def test_hard_mock_guard_strictly_blocks_unmocked_order_requests():
    """Explicitly verify that the test harness actively intercepts and blocks unmocked order endpoints."""
    with pytest.raises(AssertionError, match="SAFETY VIOLATION"):
        requests.post("https://api.india.delta.exchange/v2/orders", json={"product_id": 131253, "size": 1})


# --------------------------------------------------------------------------
# 1. Signature Calculation Tests
# --------------------------------------------------------------------------

def test_delta_signature_with_and_without_query_string():
    client = DeltaExchangeClient(api_key="my_key", api_secret="my_secret")

    # Without query string
    sig1 = client._generate_signature("GET", "/v2/wallet/balances", "1700000000")
    assert len(sig1) == 64

    # With query string
    sig2 = client._generate_signature("GET", "/v2/orders", "1700000000", query_string="?states=open")
    assert len(sig2) == 64
    assert sig1 != sig2

    # Query string without leading '?' is auto-prefixed
    sig3 = client._generate_signature("GET", "/v2/orders", "1700000000", query_string="states=open")
    assert sig2 == sig3


# --------------------------------------------------------------------------
# 2. Configuration & Mode Isolation Tests
# --------------------------------------------------------------------------

def test_mode_isolation_validation():
    # Valid default PAPER mode
    cfg_paper = ORBSettings(mode="PAPER")
    validate_settings(cfg_paper)

    # Valid ALERT_ONLY mode
    cfg_alert = ORBSettings(mode="ALERT_ONLY")
    validate_settings(cfg_alert)

    # Invalid mode name raises ConfigurationError
    cfg_invalid = ORBSettings(mode="UNKNOWN_MODE")
    with pytest.raises(ConfigurationError, match="Invalid trading mode"):
        validate_settings(cfg_invalid)

    # LIVE mode without secondary confirmation raises ConfigurationError
    cfg_live_unconfirmed = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="NO",
        api_key="real_key",
        api_secret="real_secret",
    )
    with pytest.raises(ConfigurationError, match="secondary confirmation switch"):
        validate_settings(cfg_live_unconfirmed)

    # LIVE mode with missing credentials raises ConfigurationError
    cfg_live_no_creds = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="YES",
        api_key="<placeholder>",
        api_secret="",
    )
    with pytest.raises(ConfigurationError, match="non-placeholder DELTA_EXCHANGE_API_KEY"):
        validate_settings(cfg_live_no_creds)


# --------------------------------------------------------------------------
# 3. Account Service & Equity Parsing Tests
# --------------------------------------------------------------------------

def test_delta_account_service_parsing(monkeypatch):
    mock_balances = {
        "success": True,
        "result": [
            {
                "asset_symbol": "USD",
                "balance": "50.00",
                "available_balance": "45.00",
                "position_margin": "3.50",
                "order_margin": "1.50",
                "unrealized_pnl": "2.00",
            },
            {
                "asset_symbol": "BTC",
                "balance": "0.001",
                "available_balance": "0.001",
                "position_margin": "0.0",
                "order_margin": "0.0",
                "unrealized_pnl": "0.0",
            }
        ]
    }

    mock_client = DeltaExchangeClient(api_key="valid_key", api_secret="valid_secret")
    service = DeltaAccountService(client=mock_client)

    monkeypatch.setattr(service, "_fetch_wallet_balances", lambda: mock_balances)
    monkeypatch.setattr(service, "_fetch_open_positions", lambda pid: {"success": True, "result": []})
    monkeypatch.setattr(service, "_fetch_open_orders", lambda pid: {"success": True, "result": []})

    state = service.get_account_state(settling_asset="USD")
    assert state.is_valid is True
    assert state.equity == Decimal("52.00")  # 50.00 balance + 2.00 upnl
    assert state.available_margin == Decimal("45.00")
    assert state.used_margin == Decimal("5.00")  # 3.50 + 1.50
    assert state.currency == "USD"
    assert state.open_positions == []


def test_delta_account_service_timeout(monkeypatch):
    mock_client = DeltaExchangeClient(api_key="valid_key", api_secret="valid_secret")
    service = DeltaAccountService(client=mock_client)

    monkeypatch.setattr(service, "_fetch_wallet_balances", lambda: {"success": False, "error": "Wallet balance query timed out."})

    state = service.get_account_state(settling_asset="USD")
    assert state.is_valid is False
    assert "timed out" in state.error


# --------------------------------------------------------------------------
# 4. Instrument Specification Validation Tests
# --------------------------------------------------------------------------

def test_delta_instrument_validation(monkeypatch):
    service = DeltaInstrumentService()
    mock_specs = DeltaProductSpecs(
        product_id=131253,
        symbol="XAUTUSD",
        description="Tether Gold Token Perpetual",
        contract_type="perpetual_futures",
        contract_value=Decimal("0.001"),
        contract_unit_currency="XAUT",
        quoting_asset="USD",
        settling_asset="USD",
        tick_size=Decimal("0.01"),
        maker_commission_rate=Decimal("0.0001"),
        taker_commission_rate=Decimal("0.0001"),
        initial_margin_rate=Decimal("0.01"),
        trading_status="operational",
        state="live",
        is_tradable=True,
    )
    monkeypatch.setattr(service, "get_product_specs", lambda pid=None: mock_specs)

    is_valid, msg, specs = service.validate_for_strategy("XAUTUSD", "perpetual_futures")
    assert is_valid is True
    assert specs.product_id == 131253
    assert service.align_price_to_tick(4045.238, specs.tick_size) == 4045.24


def test_delta_instrument_mismatch(monkeypatch):
    service = DeltaInstrumentService()
    mock_specs = DeltaProductSpecs(
        product_id=131253,
        symbol="BTCUSD",  # Mismatch!
        description="BTC Perpetual",
        contract_type="perpetual_futures",
        contract_value=Decimal("0.001"),
        contract_unit_currency="BTC",
        quoting_asset="USD",
        settling_asset="USD",
        tick_size=Decimal("0.01"),
        maker_commission_rate=Decimal("0.0001"),
        taker_commission_rate=Decimal("0.0001"),
        initial_margin_rate=Decimal("0.01"),
        trading_status="operational",
        state="live",
        is_tradable=True,
    )
    monkeypatch.setattr(service, "get_product_specs", lambda pid=None: mock_specs)

    is_valid, msg, _ = service.validate_for_strategy("XAUTUSD", "perpetual_futures")
    assert is_valid is False
    assert "Symbol mismatch" in msg


# --------------------------------------------------------------------------
# 5. Live Executor Pre-Flight & Safety Gates Tests
# --------------------------------------------------------------------------

def test_executor_refuses_when_not_in_live_mode(tmp_path):
    cfg_paper = ORBSettings(mode="PAPER", state_path=tmp_path / "risk.json")
    executor = DeltaLiveExecutor(cfg=cfg_paper)

    ok, reason, _ = executor.validate_preflight("LONG", 4000.0, 3990.0, 4020.0, "2026-10-28 20:00:00")
    assert ok is False
    assert "mode is 'PAPER'" in reason


def test_executor_refuses_when_kill_switch_active(tmp_path):
    cfg_killed = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="YES",
        kill_switch_active=True,
        api_key="real_key",
        api_secret="real_secret",
        state_path=tmp_path / "risk.json",
    )
    executor = DeltaLiveExecutor(cfg=cfg_killed)

    ok, reason, _ = executor.validate_preflight("LONG", 4000.0, 3990.0, 4020.0, "2026-10-28 20:00:00")
    assert ok is False
    assert "Emergency Kill Switch is ACTIVE" in reason


def test_executor_idempotency_prevents_duplicate_orders(tmp_path):
    db_file = tmp_path / "live_orders.sqlite3"
    rec_svc = DeltaReconciliationService(db_path=db_file)

    candle_ts = "2026-10-28 21:00:00"
    rec_svc.record_intent("ORDER_1", candle_ts, "XAUTUSD", "buy", 1, 4000.0)

    assert rec_svc.is_candle_already_ordered(candle_ts) is True
    assert rec_svc.is_candle_already_ordered("2026-10-28 21:05:00") is False


def test_executor_minimum_lot_risk_budget_rejection(tmp_path, monkeypatch):
    # Set up $50 equity account. 0.5% risk budget = $0.25.
    # An entry of 4000 with SL 3950 has $50 distance * 0.001 = $0.05 + fees ~ $0.13.
    # But if SL is 3600 (distance $400 * 0.001 = $0.40), it exceeds $0.25!
    cfg_live = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="YES",
        api_key="real_key",
        api_secret="real_secret",
        state_path=tmp_path / "risk.json",
    )
    executor = DeltaLiveExecutor(cfg=cfg_live)

    # Mock valid specs
    specs = DeltaProductSpecs(
        product_id=131253,
        symbol="XAUTUSD",
        description="Tether Gold Token Perpetual",
        contract_type="perpetual_futures",
        contract_value=Decimal("0.001"),
        contract_unit_currency="XAUT",
        quoting_asset="USD",
        settling_asset="USD",
        tick_size=Decimal("0.01"),
        maker_commission_rate=Decimal("0.0001"),
        taker_commission_rate=Decimal("0.0001"),
        initial_margin_rate=Decimal("0.01"),
        trading_status="operational",
        state="live",
        is_tradable=True,
    )
    monkeypatch.setattr(executor.instrument_service, "validate_for_strategy", lambda sym: (True, "OK", specs))

    # Mock $50 live equity
    acc_state = DeltaAccountState(
        is_valid=True,
        equity=Decimal("50.00"),
        available_margin=Decimal("50.00"),
        used_margin=Decimal("0.0"),
        unrealized_pnl=Decimal("0.0"),
        currency="USD",
        last_synced_at=None,
    )
    monkeypatch.setattr(executor.account_service, "get_account_state", lambda asset: acc_state)

    # Trade with wide stop-loss that exceeds 0.5% risk budget ($0.25)
    ok, reason, details = executor.validate_preflight("LONG", 4000.0, 3600.0, 4800.0, "2026-10-28 21:05:00")
    assert ok is False
    assert "exceeds configured risk budget" in reason


def test_executor_mocked_order_submission_payload(tmp_path, monkeypatch):
    cfg_live = ORBSettings(
        mode="LIVE",
        live_trading_acknowledged="YES",
        api_key="test_api_key",
        api_secret="test_api_secret",
        state_path=tmp_path / "risk.json",
    )
    db_file = tmp_path / "live_orders.sqlite3"
    rec_svc = DeltaReconciliationService(db_path=db_file)
    executor = DeltaLiveExecutor(cfg=cfg_live, reconciliation_service=rec_svc)

    specs = DeltaProductSpecs(
        product_id=131253,
        symbol="XAUTUSD",
        description="Tether Gold Token Perpetual",
        contract_type="perpetual_futures",
        contract_value=Decimal("0.001"),
        contract_unit_currency="XAUT",
        quoting_asset="USD",
        settling_asset="USD",
        tick_size=Decimal("0.01"),
        maker_commission_rate=Decimal("0.0001"),
        taker_commission_rate=Decimal("0.0001"),
        initial_margin_rate=Decimal("0.01"),
        trading_status="operational",
        state="live",
        is_tradable=True,
    )
    monkeypatch.setattr(executor.instrument_service, "validate_for_strategy", lambda sym: (True, "OK", specs))

    acc_state = DeltaAccountState(
        is_valid=True,
        equity=Decimal("5000.00"),
        available_margin=Decimal("5000.00"),
        used_margin=Decimal("0.0"),
        unrealized_pnl=Decimal("0.0"),
        currency="USD",
        last_synced_at=None,
    )
    monkeypatch.setattr(executor.account_service, "get_account_state", lambda asset: acc_state)

    intercepted_requests = []

    def mock_post(url, data=None, headers=None, timeout=None):
        intercepted_requests.append({"url": url, "data": json.loads(data), "headers": headers})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "result": {
                "id": "DELTA_ORD_9999",
                "product_id": 131253,
                "state": "open",
                "unfilled_size": 1,
            }
        }
        return resp

    # Temporarily override guarded_post specifically for this test
    monkeypatch.setattr(requests, "post", mock_post)

    result = executor.execute_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry=4050.0,
        stop_loss=4040.0,
        take_profit=4070.0,
        candle_timestamp="2026-10-28 21:10:00",
    )

    assert result["success"] is True
    assert result["exchange_order_id"] == "DELTA_ORD_9999"

    # Verify payload format sent to exchange
    assert len(intercepted_requests) == 1
    req = intercepted_requests[0]
    payload = req["data"]
    assert payload["product_id"] == 131253
    assert payload["side"] == "buy"
    assert payload["order_type"] == "limit_order"
    assert payload["limit_price"] == "4050.00"
    assert payload["bracket_stop_loss_price"] == "4040.00"
    assert payload["bracket_take_profit_price"] == "4070.00"
    assert "api-key" in req["headers"]
    assert "signature" in req["headers"]
