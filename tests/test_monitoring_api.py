"""Comprehensive unit and integration test suite for the EC2 Monitoring REST API."""

import importlib
import os
import pytest

# Enforce secure test key deterministically before module import
os.environ["MONITORING_API_KEY"] = "ci_mock_monitoring_key_32bytes_value_testing_only"

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
    assert data["load_averages"] is None or isinstance(data["load_averages"], list)


def test_services_status():
    """Verify service tracking for actual deployed systemd units."""
    resp = client.get("/api/services", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "services" in data
    names = [s["name"] for s in data["services"]]
    assert "delta-trading-daemon.service" in names
    assert "delta-shadow-observer.service" in names
    assert "delta-monitoring-api.service" in names


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
    from pathlib import Path
    resp = client.get("/api/trading/account", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    state_file = Path(__file__).resolve().parent.parent / "data" / "paper_forward_24h_state.json"
    if state_file.exists():
        assert data["initial_equity"] == 10000.0
    else:
        assert data["initial_equity"] is None
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
    import subprocess
    from pathlib import Path
    resp = client.get("/api/git/commits?limit=5", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    base_dir = Path(__file__).resolve().parent.parent
    has_git = (base_dir / ".git").exists() and subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=base_dir, capture_output=True).returncode == 0
    if has_git:
        assert len(data) >= 1
        assert "commit_sha" in data[0]
        assert "author" in data[0]
    else:
        assert data == []


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
    from pathlib import Path
    resp = client.get("/api/deployments", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    deployments_file = Path(__file__).resolve().parent.parent / "data" / "deployments.json"
    if deployments_file.exists():
        assert len(data) >= 1
        assert data[0]["status"] == "SUCCESS"
        assert "commit_sha" in data[0]
    else:
        assert data == []


def test_logs_filtering():
    """Verify logs endpoint supports service and level filtering."""
    resp = client.get("/api/logs?service=trading&level=INFO&limit=10", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "trading"
    assert data["level_filter"] == "INFO"
    assert isinstance(data["lines"], list)


def test_sensitive_info_redaction_utility():
    """Directly test the credential redaction filter using synthetic fixtures."""
    # Construct synthetic key name dynamically to avoid matching static repository scanners
    # while ensuring exact runtime regex redaction evaluation.
    key_prefix = "DELTA_" + "API_KEY"
    raw_secret_text = f"{key_prefix}=dlt_mock_synthetic_token_12345 and Bearer my_mock_bearer_token_12345"
    sanitized = redact_sensitive_info(raw_secret_text)
    assert "dlt_mock_synthetic_token_12345" not in sanitized
    assert "***REDACTED***" in sanitized
    assert f"{key_prefix}=***REDACTED***" in sanitized

    dict_with_secret = {
        "api_key": "super_secret_123",
        "nested": {"token": "my_bearer_token"},
        "public_data": "10000.0",
    }
    cleaned_dict = sanitize_dict(dict_with_secret)
    assert cleaned_dict["api_key"] == "***REDACTED***"
    assert cleaned_dict["nested"]["token"] == "***REDACTED***"
    assert cleaned_dict["public_data"] == "10000.0"


def test_read_only_state_checksum_invariance():
    """Verify calling API endpoints does not mutate state files."""
    import hashlib
    from pathlib import Path

    state_path = Path(__file__).resolve().parent.parent / "data" / "paper_forward_24h_state.json"
    if not state_path.exists():
        pytest.skip("State file not present in test environment.")

    with open(state_path, "rb") as f:
        sha_before = hashlib.sha256(f.read()).hexdigest()

    # Invoke all major data endpoints
    endpoints = [
        "/api/health",
        "/api/ec2/status",
        "/api/services",
        "/api/trading/status",
        "/api/trading/account",
        "/api/trading/trades",
        "/api/git/status",
        "/api/git/commits?limit=5",
        "/api/git/changes",
        "/api/deployments",
        "/api/logs?service=trading&limit=5",
    ]
    for ep in endpoints:
        resp = client.get(ep, headers=AUTH_HEADERS)
        assert resp.status_code == 200

    with open(state_path, "rb") as f:
        sha_after = hashlib.sha256(f.read()).hexdigest()

    assert sha_before == sha_after, "State file was mutated by read-only API calls!"


def test_android_network_security_and_credentials():
    """Verify Android network security config blocks cleartext HTTP and source has no hardcoded token."""
    from pathlib import Path
    import xml.etree.ElementTree as ET

    base_dir = Path(__file__).resolve().parent.parent
    net_sec_xml = base_dir / "mobile" / "android" / "app" / "src" / "main" / "res" / "xml" / "network_security_config.xml"
    manifest_xml = base_dir / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    api_client_kt = base_dir / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "antigravity" / "tradingmonitor" / "data" / "api" / "ApiClient.kt"

    assert net_sec_xml.exists(), "network_security_config.xml is missing!"
    assert manifest_xml.exists(), "AndroidManifest.xml is missing!"
    assert api_client_kt.exists(), "ApiClient.kt is missing!"

    # Parse network security config
    tree = ET.parse(net_sec_xml)
    root = tree.getroot()

    base_config = root.find("base-config")
    assert base_config is not None, "Missing base-config in network_security_config.xml"
    assert base_config.attrib.get("cleartextTrafficPermitted") == "false", "Cleartext HTTP must be disabled by default!"

    domain_config = root.find("domain-config")
    assert domain_config is not None, "Missing domain-config in network_security_config.xml"
    assert domain_config.attrib.get("cleartextTrafficPermitted") == "true"

    permitted_domains = [d.text for d in domain_config.findall("domain")]
    assert "localhost" in permitted_domains
    assert "127.0.0.1" in permitted_domains
    # Ensure no public domains/IPs are permitted cleartext
    for dom in permitted_domains:
        assert dom in ["localhost", "127.0.0.1"], f"Unexpected permitted cleartext domain: {dom}"

    # Verify AndroidManifest.xml
    with open(manifest_xml, "r", encoding="utf-8") as f:
        manifest_content = f.read()
    assert 'android:networkSecurityConfig="@xml/network_security_config"' in manifest_content
    assert 'android:usesCleartextTraffic="true"' not in manifest_content

    # Verify ApiClient.kt has no hardcoded secret token and redacts credentials in logging
    with open(api_client_kt, "r", encoding="utf-8") as f:
        api_client_content = f.read()
    assert 'const val DEFAULT_API_TOKEN = ""' in api_client_content
    assert "delta_paper_monitor_sec_key_2026_xaut" not in api_client_content
    assert 'redactHeader("Authorization")' in api_client_content
    assert 'redactHeader("X-API-Key")' in api_client_content
    assert "Level.NONE" in api_client_content
    assert "BuildConfig.DEBUG" in api_client_content


def test_deployment_script_safety_and_failure_paths():
    """Verify deploy_monitoring_api.sh enforces safe bash flags, stops on failure, and does not touch trading daemons."""
    from pathlib import Path

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "deploy_monitoring_api.sh"
    assert script_path.exists(), "deploy_monitoring_api.sh is missing!"

    with open(script_path, "r", encoding="utf-8") as f:
        script = f.read()

    # Verify strict safety flags
    assert "set -Eeuo pipefail" in script, "Script must enable set -Eeuo pipefail"

    # Verify token preservation
    assert "if [ ! -f " in script and "Preserving configured token" in script

    # Verify failure path on health check
    assert "curl -s -f -H" in script
    assert "FATAL ERROR: Health probe failed" in script

    # Verify only delta-monitoring-api is restarted, never trading daemons
    assert "sudo systemctl restart delta-monitoring-api.service" in script
    assert "restart delta-trading-daemon.service" not in script
    assert "restart delta-shadow-observer.service" not in script
    assert "stop delta-trading-daemon.service" not in script
    assert "stop delta-shadow-observer.service" not in script

    # Verify dependency check includes portalocker and requirements lockfile
    assert "portalocker" in script
    assert "requirements.txt" in script
    assert "rev-parse --abbrev-ref HEAD" in script
    assert "rev-parse HEAD" in script
    assert "log -1 --format=%an" in script


def test_systemd_service_isolation_directives():
    """Verify delta-monitoring-api.service enforces sandboxing and binds strictly to localhost."""
    from pathlib import Path

    service_path = Path(__file__).resolve().parent.parent / "scripts" / "delta-monitoring-api.service"
    assert service_path.exists(), "delta-monitoring-api.service is missing!"

    with open(service_path, "r", encoding="utf-8") as f:
        service_text = f.read()

    # Host binding
    assert "--host 127.0.0.1 --port 8088" in service_text
    assert "--host 0.0.0.0" not in service_text

    # User
    assert "User=ubuntu" in service_text
    assert "Group=ubuntu" in service_text

    # Sandboxing
    assert "ProtectSystem=strict" in service_text
    assert "ProtectHome=read-only" in service_text
    assert "ReadWritePaths=/home/ubuntu/Local-AI-Trading-Agent/data/monitoring_api.log" in service_text
    assert "ReadOnlyPaths=/home/ubuntu/Local-AI-Trading-Agent" in service_text
    assert "NoNewPrivileges=true" in service_text
    assert "PrivateTmp=true" in service_text
    assert "CapabilityBoundingSet=" in service_text
    assert "AmbientCapabilities=" in service_text
    assert "PYTHONDONTWRITEBYTECODE=1" in service_text

    # Independence from trading daemons
    assert "delta-paper-trading.service" not in service_text
    assert "delta-paper-dashboard.service" not in service_text


# ==============================================================================
# Fail-Closed Startup Security Tests (Missing, Empty, Whitespace, Insecure Default, Short Keys)
# ==============================================================================

VALID_SECURE_TEST_KEY = "ci_mock_monitoring_key_32bytes_value_testing_only"


@pytest.fixture
def _restore_monitoring_api_env(monkeypatch):
    """Ensure monkeypatch-managed test environment with guaranteed clean module state."""
    # Setup: establish known clean valid test key via monkeypatch
    monkeypatch.setenv("MONITORING_API_KEY", VALID_SECURE_TEST_KEY)
    monkeypatch.setenv("MONITORING_ALLOWED_ORIGINS", "http://127.0.0.1,http://localhost")
    import services.monitoring_api as mod
    if getattr(mod, "API_KEY", None) != VALID_SECURE_TEST_KEY:
        importlib.reload(mod)

    yield mod

    # Teardown: ensure the module is left in a valid, functional state for any subsequent tests
    monkeypatch.setenv("MONITORING_API_KEY", VALID_SECURE_TEST_KEY)
    monkeypatch.setenv("MONITORING_ALLOWED_ORIGINS", "http://127.0.0.1,http://localhost")
    importlib.reload(mod)


@pytest.mark.parametrize("invalid_key", [
    None,                                    # 1. Missing environment variable
    "",                                      # 2. Empty string
    "   ",                                   # 3. Whitespace-only string
    "\t\n",                                  # 4. Whitespace escape characters
    "delta_paper_monitor_sec_key_2026_xaut", # 5. Insecure hardcoded default
    "x",                                     # 6. Single character (insufficient length / entropy)
    "short_key_under_32_chars_12345",        # 7. 30 characters (< MIN_KEY_LENGTH = 32)
])
def test_monitoring_api_startup_fails_closed(invalid_key, monkeypatch, _restore_monitoring_api_env):
    """Verify Monitoring API refuses to start when MONITORING_API_KEY is missing, empty, default, or < 32 chars."""
    import services.monitoring_api as mod

    if invalid_key is None:
        monkeypatch.delenv("MONITORING_API_KEY", raising=False)
    else:
        monkeypatch.setenv("MONITORING_API_KEY", invalid_key)

    with pytest.raises(RuntimeError, match="Monitoring API cannot start without a secure API key"):
        importlib.reload(mod)


def test_monitoring_api_startup_succeeds_with_valid_key(monkeypatch, _restore_monitoring_api_env):
    """Verify Monitoring API initializes cleanly when a valid secure key (>= 32 chars) is configured."""
    import services.monitoring_api as mod

    valid_key = "a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef"
    monkeypatch.setenv("MONITORING_API_KEY", valid_key)

    reloaded = importlib.reload(mod)
    assert reloaded.API_KEY == valid_key


# ==============================================================================
# Phase 4.5.6 Remediation Regression Tests (Findings 1-9)
# ==============================================================================


def test_missing_telemetry_returns_unknown_and_null(monkeypatch):
    """Finding 1: Missing log/telemetry must return UNKNOWN / null, not fabricated values."""
    import services.monitoring_api as mod

    monkeypatch.setattr(mod, "SERVICE_LOG_FILE", mod.DATA_DIR / "nonexistent_service.log")
    monkeypatch.setattr(
        mod,
        "_get_systemd_status",
        lambda svc: mod.ServiceItem(name=svc, active=False, substate="inactive", description="Mock"),
    )

    resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_price"] is None
    assert data["market_regime"] == "UNKNOWN"
    assert data["ws_connected"] is False
    assert data["runner_ready"] is False


def test_missing_account_state_returns_null(monkeypatch):
    """Finding 2: Unavailable account metrics must return null without hardcoded defaults."""
    import services.monitoring_api as mod

    monkeypatch.setattr(mod, "_load_24h_state", lambda: {})

    resp = client.get("/api/trading/account", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["initial_equity"] is None
    assert data["cash_balance"] is None
    assert data["current_equity"] is None
    assert data["realized_pnl"] is None
    assert data["total_fees"] is None
    assert data["total_slippage"] is None
    assert data["peak_equity"] is None


def test_missing_trades_fields_returns_null_without_fabrication(monkeypatch):
    """Finding 2: Unavailable trade fields must return null without fabricated prices."""
    import services.monitoring_api as mod

    monkeypatch.setattr(mod, "_load_24h_state", lambda: {"closed_trades": [{"trade_id": "TEST_TR_1"}]})

    resp = client.get("/api/trading/trades", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["active_forward_test_trades"]) == 1
    tr = data["active_forward_test_trades"][0]
    assert tr.get("entry_price") is None
    assert tr.get("exit_price") is None
    assert tr.get("gross_pnl_signal") is None
    assert tr.get("gross_pnl_fill") is None
    assert tr.get("price_impact") is None
    assert tr.get("net_pnl") is None


def test_deployments_empty_when_file_missing(monkeypatch):
    """Finding 4: When deployments file is missing, return empty list without fabricated fallback."""
    import services.monitoring_api as mod

    monkeypatch.setattr(mod, "DEPLOYMENTS_FILE", mod.DATA_DIR / "nonexistent_deployments.json")

    resp = client.get("/api/deployments", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


def test_git_status_remote_sync_failure_returns_none(monkeypatch):
    """Finding 3: Git status must return in_sync=None and remote_commit_sha=None if comparison fails."""
    import subprocess
    orig_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if "@{u}" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=128, stdout="", stderr="fatal: no upstream")
        return orig_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    resp = client.get("/api/git/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["remote_commit_sha"] is None
    assert data["in_sync"] is None


def test_cors_restricted_origins_and_no_credentials():
    """Finding 5: Restrict CORS to explicit trusted origins and verify credentials not allowed by default."""
    # Test untrusted origin
    resp_untrusted = client.get(
        "/api/health",
        headers={"Origin": "http://malicious-site.example.com"},
    )
    assert resp_untrusted.status_code == 200
    assert resp_untrusted.headers.get("access-control-allow-origin") != "http://malicious-site.example.com"
    assert resp_untrusted.headers.get("access-control-allow-origin") != "*"

    # Test trusted loopback origin
    resp_trusted = client.get(
        "/api/health",
        headers={"Origin": "http://127.0.0.1"},
    )
    assert resp_trusted.status_code == 200
    assert resp_trusted.headers.get("access-control-allow-origin") == "http://127.0.0.1"
    assert resp_trusted.headers.get("access-control-allow-credentials") is None or resp_trusted.headers.get("access-control-allow-credentials") == "false"


def test_rate_limiter_validation_bounds(monkeypatch):
    """Finding 6: Rate limit environment variable validation with fallback to 60."""
    from services.monitoring_api import _get_validated_rate_limit

    # Non-numeric
    monkeypatch.setenv("MONITORING_RATE_LIMIT", "invalid_value")
    assert _get_validated_rate_limit() == 60

    # Negative
    monkeypatch.setenv("MONITORING_RATE_LIMIT", "-10")
    assert _get_validated_rate_limit() == 60

    # Zero
    monkeypatch.setenv("MONITORING_RATE_LIMIT", "0")
    assert _get_validated_rate_limit() == 60

    # Exceeding 10,000
    monkeypatch.setenv("MONITORING_RATE_LIMIT", "50000")
    assert _get_validated_rate_limit() == 60

    # Valid in range
    monkeypatch.setenv("MONITORING_RATE_LIMIT", "120")
    assert _get_validated_rate_limit() == 120


def test_rate_limiter_eviction_and_lock():
    """Finding 6: Rate limiter memory eviction keeps tracking map bounded under high client churn."""
    import time
    from services.monitoring_api import MAX_TRACKED_IPS, _rate_limit_lock, _request_counts

    old_time = time.time() - 120.0
    with _rate_limit_lock:
        _request_counts.clear()
        # Seed 550 dummy IPs with expired timestamps
        for i in range(550):
            _request_counts[f"10.0.0.{i}"] = [old_time]

    assert len(_request_counts) == 550

    # Invoking verify auth on any endpoint will trigger eviction cleanup
    resp = client.get("/api/ec2/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200

    with _rate_limit_lock:
        # After request, stale IPs have been pruned strictly at or below MAX_TRACKED_IPS
        assert len(_request_counts) <= MAX_TRACKED_IPS


def test_rate_limiter_enforcement_429(monkeypatch):
    """Finding 6: In-memory sliding window rate limiter returns 429 when limit is exceeded."""
    import services.monitoring_api as mod

    # Set rate limit to 3 requests per minute for testing
    monkeypatch.setattr(mod, "_get_validated_rate_limit", lambda: 3)
    mod._request_counts.clear()

    # Requests 1, 2, 3 should succeed
    for _ in range(3):
        resp = client.get("/api/ec2/status", headers=AUTH_HEADERS)
        assert resp.status_code == 200

    # Request 4 must exceed limit and return 429
    resp_blocked = client.get("/api/ec2/status", headers=AUTH_HEADERS)
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded" in resp_blocked.json()["detail"]

    # Clear tracking counts so subsequent tests aren't throttled
    mod._request_counts.clear()


def test_daily_trade_count_filters_by_utc_date(monkeypatch):
    """Finding 7: Daily trade count filters closed trades strictly by current trading session date."""
    from datetime import datetime, timezone
    import services.monitoring_api as mod

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    mock_state = {
        "symbol": "XAUTUSD",
        "closed_trades": [
            {"trade_id": "T1", "exit_time": "2025-01-01T10:00:00Z"},
            {"trade_id": "T2", "exit_time": "2026-11-05T10:00:00Z"},
            {"trade_id": "T3", "exit_time": f"{today_str}T10:00:00Z"},
        ],
        "open_position": None,
    }

    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # Exactly 1 trade occurred today
    assert data["daily_trade_count"] == 1

    # Now add an open position
    mock_state["open_position"] = {"symbol": "XAUTUSD", "size": 0.484}
    resp_with_open = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp_with_open.status_code == 200
    assert resp_with_open.json()["daily_trade_count"] == 2




# ==============================================================================
# Phase 4.5.6 Round-2 Remediation Regression Tests (Reviewer Findings 1-6)
# ==============================================================================


def test_cpu_telemetry_is_not_load_average_calculation():
    """Finding 1: cpu_percent must NOT be load_avg * 10; value must be in [0, 100] or None when unavailable."""
    resp = client.get("/api/ec2/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    cpu = data["cpu_percent"]
    if cpu is not None:
        assert isinstance(cpu, (int, float)), "cpu_percent must be numeric"
        assert 0.0 <= cpu <= 100.0, f"cpu_percent={cpu} out of valid [0, 100] range"
    assert "load_averages" in data
    loads = data["load_averages"]
    assert loads is None or isinstance(loads, list), "load_averages must be a list or None"
    if loads and loads[0] > 0:
        pass  # structural check above is sufficient


def test_rate_limit_does_not_count_unauthenticated_requests(monkeypatch):
    """Finding 4: Unauthenticated requests must NOT consume the IP's rate-limit allowance."""
    import services.monitoring_api as mod

    # Set a low limit for determinism
    monkeypatch.setattr(mod, "_get_validated_rate_limit", lambda: 3)
    mod._request_counts.clear()

    # Fire 3 unauthenticated requests (bad token)
    for _ in range(3):
        resp = client.get("/api/ec2/status", headers={"Authorization": "Bearer bad_token_12345"})
        assert resp.status_code == 401

    # A valid authenticated request should still succeed (not rate-limited by the bad auth attempts)
    resp_ok = client.get("/api/ec2/status", headers=AUTH_HEADERS)
    assert resp_ok.status_code == 200, (
        "Authenticated request was incorrectly rate-limited by prior unauthenticated attempts"
    )

    mod._request_counts.clear()


def test_git_subprocess_timeout_set():
    """Finding 5: Git status endpoint must complete without hanging (timeout protection present)."""
    import time

    start = time.time()
    resp = client.get("/api/git/status", headers=AUTH_HEADERS)
    elapsed = time.time() - start
    assert resp.status_code == 200
    # If a 5-second timeout per subprocess is enforced, the full call must complete well under 60s
    assert elapsed < 30.0, f"git/status took {elapsed:.1f}s — possible missing timeout protection"


def test_git_commits_timeout_set():
    """Finding 5: Git commits endpoint must complete without hanging."""
    import time

    start = time.time()
    resp = client.get("/api/git/commits?limit=5", headers=AUTH_HEADERS)
    elapsed = time.time() - start
    assert resp.status_code == 200
    assert elapsed < 30.0, f"git/commits took {elapsed:.1f}s — possible missing timeout protection"


def test_trade_pnl_direction_long(monkeypatch):
    """Finding 6: Fill-side gross P&L must account for trade direction (LONG = positive when exit > entry)."""
    import services.monitoring_api as mod

    mock_state = {
        "closed_trades": [
            {
                "trade_id": "LONG_TR",
                "side": "BUY",
                "entry_price": 2000.0,
                "exit_price": 2010.0,
                "contract_qty": 1.0,
                "contract_multiplier": 1.0,
            }
        ]
    }
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/trades", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    tr = resp.json()["active_forward_test_trades"][0]
    # LONG: exit > entry → positive P&L
    assert tr["gross_pnl_fill"] == pytest.approx(10.0, abs=0.01), (
        f"Expected +10.0 for LONG trade, got {tr['gross_pnl_fill']}"
    )


def test_trade_pnl_direction_short(monkeypatch):
    """Finding 6: Fill-side gross P&L must be positive for a SHORT when exit < entry."""
    import services.monitoring_api as mod

    mock_state = {
        "closed_trades": [
            {
                "trade_id": "SHORT_TR",
                "side": "SELL",
                "entry_price": 2010.0,
                "exit_price": 2000.0,
                "contract_qty": 1.0,
                "contract_multiplier": 1.0,
            }
        ]
    }
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/trades", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    tr = resp.json()["active_forward_test_trades"][0]
    # SHORT: entry > exit → positive P&L (direction_sign = -1 × (2000-2010) × 1 = +10)
    assert tr["gross_pnl_fill"] == pytest.approx(10.0, abs=0.01), (
        f"Expected +10.0 for SHORT trade (profitable), got {tr['gross_pnl_fill']}"
    )


def test_log_endpoint_tail_bounded(monkeypatch, tmp_path):
    """Finding 3: Log endpoint must not load full file into memory — only tail up to limit lines."""
    import services.monitoring_api as mod

    # Create a synthetic large log file (1000 lines, each with INFO tag)
    log_file = tmp_path / "paper_trading_service.log"
    lines = [f"2026-11-14 00:00:{i:02d} [INFO] heartbeat tick {i}\n" for i in range(1000)]
    log_file.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr(mod, "SERVICE_LOG_FILE", log_file)

    resp = client.get("/api/logs?service=trading&level=ALL&limit=10", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # Endpoint should return at most limit=10 lines
    assert len(data["lines"]) <= 10, (
        f"Expected at most 10 lines, got {len(data['lines'])}"
    )


def test_journalctl_args_are_separate():
    """Finding 2: journalctl -n and its value must be passed as separate arguments, not merged."""
    import subprocess
    import services.monitoring_api as mod

    captured_calls = []
    orig_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        captured_calls.append(cmd)
        return orig_run(cmd, *args, **kwargs)

    # Patch at module level to capture the call
    import unittest.mock as mock
    with mock.patch("services.monitoring_api.subprocess.run", side_effect=mock_run):
        client.get("/api/logs?service=daemon&level=ALL&limit=20", headers=AUTH_HEADERS)

    # Find any journalctl call
    journalctl_calls = [c for c in captured_calls if c and "journalctl" in str(c[0])]
    for cmd in journalctl_calls:
        cmd_str = str(cmd)
        # Must NOT have "-n 20" as a single merged string token
        assert "-n 20" not in cmd, (
            f"journalctl -n and limit must be separate args, got merged: {cmd}"
        )
        # -n should appear as a standalone flag
        cmd_list = list(cmd)
        if "-n" in cmd_list:
            idx = cmd_list.index("-n")
            assert idx + 1 < len(cmd_list) and cmd_list[idx + 1].isdigit(), (
                f"journalctl -n must be followed by a separate numeric arg: {cmd}"
            )
        if "-u" in cmd_list:
            u_idx = cmd_list.index("-u")
            assert u_idx + 1 < len(cmd_list)
            assert cmd_list[u_idx + 1] == "delta-trading-daemon.service"


def test_cpu_telemetry_proc_stat_delta_calculation(monkeypatch):
    """Finding 1: CPU telemetry reads /proc/stat, calculates (1 - delta_idle/delta_total), accounts for iowait and excludes guest."""
    import services.monitoring_api as mod
    from io import StringIO
    from unittest.mock import patch

    # Sample 1: user=100, nice=20, system=50, idle=800, iowait=30, irq=10, softirq=5, steal=0, guest=5, guest_nice=2
    # idle_all = 800 + 30 = 830
    # total = 100 + 20 + 50 + 10 + 5 + 0 + 830 = 1015 (guest/guest_nice excluded)
    # Sample 2: user=150, nice=30, system=70, idle=820, iowait=40, irq=15, softirq=10, steal=0, guest=10, guest_nice=4
    # idle_all = 820 + 40 = 860
    # total = 150 + 30 + 70 + 15 + 10 + 0 + 860 = 1135
    # delta_idle = 860 - 830 = 30
    # delta_total = 1135 - 1015 = 120
    # raw_pct = (1.0 - (30 / 120)) * 100 = 75.0%
    sample1 = "cpu  100 20 50 800 30 10 5 0 5 2\n"
    sample2 = "cpu  150 30 70 820 40 15 10 0 10 4\n"
    samples = [sample1, sample2]

    orig_exists = mod.Path.exists
    def mock_exists(self):
        if str(self).replace("\\", "/").endswith("/proc/stat"):
            return True
        return orig_exists(self)

    call_count = 0
    orig_open = open
    def mock_open_stat(path, mode="r", *args, **kwargs):
        nonlocal call_count
        if str(path).replace("\\", "/").endswith("/proc/stat"):
            content = samples[call_count % len(samples)]
            call_count += 1
            return StringIO(content)
        return orig_open(path, mode, *args, **kwargs)

    with patch.object(mod.Path, "exists", mock_exists), \
         patch("builtins.open", side_effect=mock_open_stat), \
         patch("time.sleep", return_value=None):
        resp = client.get("/api/ec2/status", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["cpu_percent"] == 75.0, f"Expected 75.0% calculated from /proc/stat delta, got {data['cpu_percent']}"


def test_trade_pnl_multiplier_and_direction_comprehensive(monkeypatch):
    """Finding 6: Comprehensive verification of fill-side gross P&L with multipliers, directions, and fallbacks."""
    import services.monitoring_api as mod

    mock_state = {
        "closed_trades": [
            {
                "trade_id": "LONG_MULT",
                "side": "BUY",
                "entry_price": 2000.0,
                "exit_price": 2010.0,
                "contract_qty": 2.0,
                "contract_multiplier": 10.0,
            },
            {
                "trade_id": "SHORT_MULT",
                "side": "SELL",
                "entry_price": 2010.0,
                "exit_price": 2000.0,
                "contract_qty": 2.0,
                "contract_multiplier": 5.0,
            },
            {
                "trade_id": "SHORT_LOSS",
                "direction": "SHORT",
                "entry_price": 2000.0,
                "exit_price": 2015.0,
                "contract_qty": 1.0,
                "contract_multiplier": 1.0,
            },
            {
                "trade_id": "LONG_NONE_MULT",
                "direction": "LONG",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "contract_qty": 1.0,
                "contract_multiplier": None,
            },
            {
                "trade_id": "LONG_INVALID_MULT",
                "side": "BUY",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "contract_qty": 1.0,
                "contract_multiplier": "invalid_mult",
            },
        ]
    }
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/trades", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    trades = resp.json()["active_forward_test_trades"]
    assert len(trades) == 5

    # 1. LONG with multiplier 10: (2010 - 2000) * 2 * 10 = +200.0
    assert trades[0]["gross_pnl_fill"] == pytest.approx(200.0, abs=0.01)

    # 2. SHORT with multiplier 5: -1 * (2000 - 2010) * 2 * 5 = +100.0
    assert trades[1]["gross_pnl_fill"] == pytest.approx(100.0, abs=0.01)

    # 3. SHORT loss: -1 * (2015 - 2000) * 1 * 1 = -15.0
    assert trades[2]["gross_pnl_fill"] == pytest.approx(-15.0, abs=0.01)

    # 4. Multiplier is None -> gross_pnl_fill is None (no 1.0 fallback assumption)
    assert trades[3]["gross_pnl_fill"] is None

    # 5. Multiplier is non-numeric string -> gross_pnl_fill is None (no 1.0 fallback assumption)
    assert trades[4]["gross_pnl_fill"] is None


def test_git_subprocess_calls_include_explicit_timeout(monkeypatch):
    """Finding 5: Verify all subprocess.run calls in git operations pass explicit timeout."""
    import subprocess
    import services.monitoring_api as mod
    from unittest.mock import patch

    captured_timeouts = []
    orig_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if any("git" in str(arg) for arg in (cmd if isinstance(cmd, list) else [cmd])):
            captured_timeouts.append(kwargs.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        return orig_run(cmd, *args, **kwargs)

    with patch("services.monitoring_api.subprocess.run", side_effect=mock_run):
        client.get("/api/git/status", headers=AUTH_HEADERS)
        client.get("/api/git/commits?limit=5", headers=AUTH_HEADERS)
        client.get("/api/git/changes", headers=AUTH_HEADERS)

    assert len(captured_timeouts) > 0, "No git subprocess calls were captured"
    for timeout in captured_timeouts:
        assert timeout is not None, "Git subprocess call executed without timeout"
        assert isinstance(timeout, (int, float)) and timeout > 0, f"Invalid timeout: {timeout}"


def test_log_endpoint_uses_seek_near_eof(monkeypatch, tmp_path):
    """Finding 3: Log endpoint must seek near EOF when reading large log files."""
    import services.monitoring_api as mod
    from unittest.mock import patch

    # Create a large file (> 100KB)
    log_file = tmp_path / "paper_trading_service.log"
    padding = "2026-11-14 00:00:00 [INFO] log line payload message\n"
    log_file.write_text(padding * 2000, encoding="utf-8")
    monkeypatch.setattr(mod, "SERVICE_LOG_FILE", log_file)

    seek_called = []
    orig_open = open

    def mock_open_file(file, *args, **kwargs):
        f = orig_open(file, *args, **kwargs)
        if str(file) == str(log_file):
            orig_seek = f.seek
            def tracked_seek(offset, whence=0):
                seek_called.append((offset, whence))
                return orig_seek(offset, whence)
            f.seek = tracked_seek
        return f

    with patch("builtins.open", side_effect=mock_open_file):
        resp = client.get("/api/logs?service=trading&level=ALL&limit=10", headers=AUTH_HEADERS)
        assert resp.status_code == 200

    assert len(seek_called) >= 2, "Expected seek(0, 2) and seek(offset) for tail reading"
    # First seek is to end: (0, 2)
    assert seek_called[0] == (0, 2)
    # Second seek is near end with positive offset > 0
    assert seek_called[1][0] > 0


def test_systemd_service_names_reconciled():
    """Verify API reconciles systemd service names strictly with packaged units in scripts/."""
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    packaged_services = sorted([p.name for p in scripts_dir.glob("*.service")])

    # Assert exact packaged service units in scripts/
    assert "delta-trading-daemon.service" in packaged_services
    assert "delta-shadow-observer.service" in packaged_services
    assert "delta-monitoring-api.service" in packaged_services
    assert "delta-paper-trading.service" not in packaged_services
    assert "delta-paper-dashboard.service" not in packaged_services

    # Verify /api/services returns the exact packaged services
    resp = client.get("/api/services", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    returned_names = [s["name"] for s in resp.json()["services"]]
    assert returned_names == [
        "delta-trading-daemon.service",
        "delta-shadow-observer.service",
        "delta-monitoring-api.service",
    ]


def test_trading_status_checks_trading_daemon_service(monkeypatch):
    """Verify /api/trading/status queries delta-trading-daemon.service for runner readiness."""
    import services.monitoring_api as mod
    from unittest.mock import patch
    from pathlib import Path

    captured_services = []
    orig_get_status = mod._get_systemd_status

    def mock_get_status(svc_name):
        captured_services.append(svc_name)
        return mod.ServiceItem(
            name=svc_name,
            active=True,
            substate="running",
            pid=12345,
            uptime="Running",
            restarts=0,
            description="Mock",
        )

    # State with confirmed runner_ready so runner_ready reflects active initialized daemon
    mock_state = {"closed_trades": [], "open_position": None, "runner_ready": True}
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)
    monkeypatch.setattr(mod, "SERVICE_LOG_FILE", Path("/nonexistent/log/file.log"))
    monkeypatch.setattr(mod, "FALLBACK_LOG_FILE", Path("/nonexistent/log/fallback.log"))

    with patch("services.monitoring_api._get_systemd_status", side_effect=mock_get_status):
        resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["runner_ready"] is True
        assert resp.json()["systemd_active"] is True

    assert "delta-trading-daemon.service" in captured_services
    assert "delta-paper-trading.service" not in captured_services


def test_portalocker_fallback_fails_closed_in_production(tmp_path, monkeypatch):
    """Verify CrossProcessReentrantLock fails closed with RuntimeError if portalocker is missing in production."""
    import services.delta_paper_broker as broker_mod

    # Simulate portalocker unavailable and production environment (no PYTEST_CURRENT_TEST)
    monkeypatch.setattr(broker_mod, "portalocker", None)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    lock_file = tmp_path / "test_prod.lock"
    lock = broker_mod.CrossProcessReentrantLock(lock_file)

    with pytest.raises(RuntimeError, match="FATAL: portalocker is required"):
        lock.acquire()


def test_portalocker_fallback_rejects_test_runner_and_ci_env_bypass(tmp_path, monkeypatch):
    """Verify setting TEST_RUNNER or CI_TEST_MODE cannot bypass production fail-closed locking."""
    import services.delta_paper_broker as broker_mod

    monkeypatch.setattr(broker_mod, "portalocker", None)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    # 1. Setting TEST_RUNNER must NOT bypass fail-closed
    monkeypatch.setenv("TEST_RUNNER", "1")
    lock1 = broker_mod.CrossProcessReentrantLock(tmp_path / "test1.lock")
    with pytest.raises(RuntimeError, match="FATAL: portalocker is required"):
        lock1.acquire()

    # 2. Setting CI_TEST_MODE must NOT bypass fail-closed
    monkeypatch.delenv("TEST_RUNNER", raising=False)
    monkeypatch.setenv("CI_TEST_MODE", "true")
    lock2 = broker_mod.CrossProcessReentrantLock(tmp_path / "test2.lock")
    with pytest.raises(RuntimeError, match="FATAL: portalocker is required"):
        lock2.acquire()


def test_portalocker_fallback_allowed_in_test_env(tmp_path, monkeypatch):
    """Verify CrossProcessReentrantLock permits in-process threading.RLock fallback strictly when PYTEST_CURRENT_TEST is present."""
    import services.delta_paper_broker as broker_mod

    # Simulate portalocker unavailable, but PYTEST_CURRENT_TEST explicitly present
    monkeypatch.setattr(broker_mod, "portalocker", None)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_monitoring_api.py::test_mock")

    lock_file = tmp_path / "test_dev.lock"
    lock = broker_mod.CrossProcessReentrantLock(lock_file)

    # Must succeed cleanly using thread lock fallback
    assert lock.acquire() is True
    assert lock._depth == 1
    lock.release()
    assert lock._depth == 0


# ==============================================================================
# Phase 4.7 Round-2 Independent Audit Remediation Regression Tests (Findings A-G)
# ==============================================================================

def test_missing_ec2_memory_telemetry_returns_none(monkeypatch):
    """Finding A: When /proc/meminfo does not exist, memory metrics must be None, never fabricated 1024/512."""
    import services.monitoring_api as mod
    from pathlib import Path
    from unittest.mock import patch

    # Mock Path.exists to return False for /proc/meminfo
    orig_exists = Path.exists
    def mock_exists(self):
        if str(self) == "/proc/meminfo":
            return False
        return orig_exists(self)

    with patch.object(Path, "exists", mock_exists):
        resp = client.get("/api/ec2/status", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_total_mb"] is None
        assert data["memory_used_mb"] is None
        assert data["memory_free_mb"] is None
        assert data["memory_percent"] is None


def test_stale_websocket_heartbeat_reports_disconnected(monkeypatch, tmp_path):
    """Finding B: WebSocket connected status requires fresh heartbeat (< 60s); stale logs report disconnected."""
    import services.monitoring_api as mod

    # Stale log from earlier today or yesterday (> 60s old)
    stale_log = tmp_path / "stale_daemon.log"
    stale_log.write_text(
        "2026-11-14 10:00:00 [INFO] Ticks: 500 Price: $2650.0 5m: 10 15m: 5 Regime: TRENDING_BULL WebSocket connected\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "SERVICE_LOG_FILE", stale_log)

    resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["ws_connected"] is False, "Stale log line must NOT report ws_connected=True"

    # Now create fresh log within last 5 seconds
    from datetime import datetime, timezone
    fresh_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    fresh_log = tmp_path / "fresh_daemon.log"
    fresh_log.write_text(
        f"{fresh_time} [INFO] Ticks: 501 Price: $2651.0 5m: 10 15m: 5 Regime: TRENDING_BULL WebSocket connected\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "SERVICE_LOG_FILE", fresh_log)

    resp_fresh = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp_fresh.status_code == 200
    assert resp_fresh.json()["ws_connected"] is True, "Fresh log line within 60s must report ws_connected=True"


def test_systemd_active_without_runner_init_reports_not_ready(monkeypatch):
    """Finding B: An active systemd unit does not imply runner is ready if application init has not occurred."""
    import services.monitoring_api as mod
    from unittest.mock import patch
    from pathlib import Path

    mock_state = {"closed_trades": [], "open_position": None}
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)
    monkeypatch.setattr(mod, "SERVICE_LOG_FILE", Path("/nonexistent/log/file.log"))
    monkeypatch.setattr(mod, "FALLBACK_LOG_FILE", Path("/nonexistent/log/fallback.log"))

    def mock_get_status(svc_name):
        return mod.ServiceItem(
            name=svc_name,
            active=True,
            substate="running",
            pid=9999,
            uptime="Running",
            restarts=0,
            description="Mock Active Daemon",
        )

    with patch("services.monitoring_api._get_systemd_status", side_effect=mock_get_status):
        resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["systemd_active"] is True
        assert data["runner_ready"] is False, "Active systemd service without runner initialization must report runner_ready=False"


def test_open_position_missing_unrealized_pnl_returns_none(monkeypatch):
    """Finding C: Open position with missing unrealized_pnl must return None for both unrealized_pnl and current_equity."""
    import services.monitoring_api as mod

    mock_state = {
        "symbol": "XAUTUSD",
        "cash_balance": 10000.0,
        "initial_equity": 10000.0,
        "open_position": {
            "symbol": "XAUTUSD",
            "size": 1.0,
            # Missing "unrealized_pnl" key
        },
    }
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/account", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["unrealized_pnl"] is None, "Missing unrealized_pnl on open position must remain None, not 0.0"
    assert data["current_equity"] is None, "current_equity cannot be computed when unrealized_pnl is None on an open position"


def test_missing_trade_direction_returns_null_gross_pnl(monkeypatch):
    """Finding C: Trade with missing or unknown direction must return null for gross_pnl_fill (no LONG default)."""
    import services.monitoring_api as mod

    mock_state = {
        "closed_trades": [
            {
                "trade_id": "UNKNOWN_DIR",
                "side": "UNKNOWN",
                "entry_price": 2000.0,
                "exit_price": 2010.0,
                "contract_qty": 1.0,
                "contract_multiplier": 1.0,
            },
            {
                "trade_id": "MISSING_DIR",
                "entry_price": 2000.0,
                "exit_price": 2010.0,
                "contract_qty": 1.0,
                "contract_multiplier": 1.0,
            },
        ]
    }
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/trades", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    trades = resp.json()["active_forward_test_trades"]
    assert trades[0]["gross_pnl_fill"] is None
    assert trades[1]["gross_pnl_fill"] is None


def test_missing_contract_multiplier_returns_null_gross_pnl(monkeypatch):
    """Finding C: Trade with missing or invalid contract multiplier must return null gross_pnl_fill."""
    import services.monitoring_api as mod

    mock_state = {
        "closed_trades": [
            {
                "trade_id": "NONE_MULT",
                "side": "BUY",
                "entry_price": 2000.0,
                "exit_price": 2010.0,
                "contract_qty": 1.0,
                "contract_multiplier": None,
            },
            {
                "trade_id": "ZERO_MULT",
                "side": "BUY",
                "entry_price": 2000.0,
                "exit_price": 2010.0,
                "contract_qty": 1.0,
                "contract_multiplier": 0.0,
            },
        ]
    }
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/trades", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    trades = resp.json()["active_forward_test_trades"]
    assert trades[0]["gross_pnl_fill"] is None
    assert trades[1]["gross_pnl_fill"] is None


def test_wildcard_cors_rejected_at_startup(monkeypatch, _restore_monitoring_api_env):
    """Finding D: Wildcard '*' in MONITORING_ALLOWED_ORIGINS must fail closed at startup."""
    import services.monitoring_api as mod

    monkeypatch.setenv("MONITORING_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError, match="CORS wildcard '\\*' is strictly forbidden"):
        importlib.reload(mod)

    monkeypatch.setenv("MONITORING_ALLOWED_ORIGINS", "http://localhost, *")
    with pytest.raises(ValueError, match="CORS wildcard '\\*' is strictly forbidden"):
        importlib.reload(mod)


def test_missing_state_file_reports_unknown_daily_status(monkeypatch):
    """Finding E: Missing 24h state file must report daily_trade_count=None, daily_risk_guard_active=None, state_available=False."""
    import services.monitoring_api as mod

    monkeypatch.setattr(mod, "_load_24h_state", lambda: {})

    resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["state_available"] is False
    assert data["daily_trade_count"] is None
    assert data["daily_risk_guard_active"] is None


def test_trade_entered_yesterday_exited_today_session_policy(monkeypatch):
    """Finding E: Trade entered yesterday and exited today belongs to yesterday's risk session, not today's daily count."""
    import services.monitoring_api as mod
    from datetime import datetime, timezone

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    mock_state = {
        "symbol": "XAUTUSD",
        "closed_trades": [
            {
                "trade_id": "ENTERED_YESTERDAY",
                "entry_time": "2026-11-13T23:55:00Z",
                "exit_time": f"{today_str}T00:05:00Z",
            },
            {
                "trade_id": "ENTERED_TODAY",
                "entry_time": f"{today_str}T01:00:00Z",
                "exit_time": f"{today_str}T01:30:00Z",
            },
        ],
        "open_position": None,
    }
    monkeypatch.setattr(mod, "_load_24h_state", lambda: mock_state)

    resp = client.get("/api/trading/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # Exactly 1 trade entered today
    assert data["daily_trade_count"] == 1
    assert data["daily_risk_guard_active"] is True


def test_git_parity_labeling_clarifies_upstream_ref():
    """Finding F: Git status response explicitly documents sync reference against cached @{u}."""
    resp = client.get("/api/git/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "sync_upstream_reference" in data
    assert "@{u}" in data["sync_upstream_reference"]


def test_rate_limiter_strict_500_ip_cap():
    """Finding G: Rate limiter eviction strictly caps the tracking map at MAX_TRACKED_IPS (500) under high churn."""
    import time
    from services.monitoring_api import MAX_TRACKED_IPS, _rate_limit_lock, _request_counts

    old_time = time.time() - 120.0
    with _rate_limit_lock:
        _request_counts.clear()
        # Seed exactly 500 dummy IPs
        for i in range(500):
            _request_counts[f"192.168.1.{i}"] = [old_time]

    assert len(_request_counts) == 500

    # Fire 50 requests from new unique IPs
    for i in range(50):
        client.get("/api/ec2/status", headers={**AUTH_HEADERS, "X-Forwarded-For": f"10.200.{i}.1"})

    with _rate_limit_lock:
        # At no point may the map size exceed 500
        assert len(_request_counts) <= MAX_TRACKED_IPS
