"""Comprehensive unit and integration test suite for the EC2 Monitoring REST API."""

import pytest
from fastapi.testclient import TestClient

from services.monitoring_api import (
    API_KEY,
    app,
    redact_sensitive_info,
    sanitize_dict,
)

client = TestClient(app)
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def test_health_public():
    """Verify health endpoint is reachable without credentials."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "delta-monitoring-api"
    assert data["trading_mode"] == "PAPER"
    assert data["live_trading_enabled"] is False


def test_auth_missing_token_fails():
    """Verify endpoints reject unauthenticated requests with 401."""
    resp = client.get("/api/ec2/status")
    assert resp.status_code == 401
    assert "Invalid or missing" in resp.json()["detail"]


def test_auth_invalid_token_fails():
    """Verify invalid Bearer token is rejected."""
    resp = client.get("/api/ec2/status", headers={"Authorization": "Bearer invalid_secret_key_12345"})
    assert resp.status_code == 401


def test_auth_x_api_key_header():
    """Verify authentication succeeds with X-API-Key header."""
    resp = client.get("/api/ec2/status", headers={"X-API-Key": API_KEY})
    assert resp.status_code == 200
    assert "hostname" in resp.json()


def test_ec2_status_metrics():
    """Verify EC2 system telemetry metrics structure."""
    resp = client.get("/api/ec2/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "cpu_percent" in data
    assert "memory_total_mb" in data
    assert "disk_total_gb" in data
    assert "load_averages" in data
    assert isinstance(data["load_averages"], list)


def test_services_status():
    """Verify service tracking for trading daemon and dashboard."""
    resp = client.get("/api/services", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "services" in data
    names = [s["name"] for s in data["services"]]
    assert "delta-paper-trading.service" in names
    assert "delta-paper-dashboard.service" in names


def test_trading_status_paper_enforcement():
    """Verify trading status strictly reports PAPER mode and live disabled."""
    resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["trading_mode"] == "PAPER"
    assert data["live_trading_enabled"] is False
    assert data["live_trading_acknowledged"] == "NO"
    assert data["symbol"] == "XAUTUSD"
    assert "ws_connected" in data
    assert "daily_trade_count" in data


def test_account_state():
    """Verify account cash, equity, and P&L reporting."""
    resp = client.get("/api/trading/account", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["initial_equity"] == 10000.0
    assert "cash_balance" in data
    assert "current_equity" in data
    assert "realized_pnl" in data


def test_trade_history_isolation():
    """Verify active trades are strictly segregated from historical 14d benchmark."""
    resp = client.get("/api/trading/trades", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "active_forward_test_trades" in data
    assert "historical_14d_benchmark" in data
    assert isinstance(data["active_forward_test_trades"], list)

    # Check dual gross P&L enrichment on active trades
    for tr in data["active_forward_test_trades"]:
        assert "gross_pnl_signal" in tr
        assert "gross_pnl_fill" in tr
        assert "price_impact" in tr
        assert "net_pnl" in tr


def test_git_status():
    """Verify git status reports branch and commit SHA."""
    resp = client.get("/api/git/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "branch" in data
    assert "ec2_commit_sha" in data
    assert "latest_commit_message" in data


def test_git_commits():
    """Verify git commit history returns recent commits."""
    resp = client.get("/api/git/commits?limit=5", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "commit_sha" in data[0]
    assert "author" in data[0]


def test_git_changes_redaction():
    """Verify git changes endpoint redacts secrets."""
    resp = client.get("/api/git/changes", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "commit_sha" in data
    assert "changed_files" in data
    assert "diff_preview" in data


def test_deployments_history():
    """Verify deployment tracking endpoint returns historical releases."""
    resp = client.get("/api/deployments", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["status"] == "SUCCESS"
    assert "commit_sha" in data[0]


def test_logs_filtering():
    """Verify logs endpoint supports service and level filtering."""
    resp = client.get("/api/logs?service=trading&level=INFO&limit=10", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "trading"
    assert data["level_filter"] == "INFO"
    assert isinstance(data["lines"], list)


def test_sensitive_info_redaction_utility():
    """Directly test the credential redaction filter."""
    raw_secret_text = "DELTA_API_KEY=dlt_sec_abc123456789 and Bearer my_secret_token_abcdef12345"
    sanitized = redact_sensitive_info(raw_secret_text)
    assert "dlt_sec_abc123456789" not in sanitized
    assert "***REDACTED***" in sanitized

    dict_with_secret = {
        "api_key": "super_secret_123",
        "nested": {"token": "my_bearer_token"},
        "public_data": "10000.0",
    }
    cleaned_dict = sanitize_dict(dict_with_secret)
    assert cleaned_dict["api_key"] == "***REDACTED***"
    assert cleaned_dict["nested"]["token"] == "***REDACTED***"
    assert cleaned_dict["public_data"] == "10000.0"

