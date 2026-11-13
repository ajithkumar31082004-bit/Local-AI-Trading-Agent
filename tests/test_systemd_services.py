"""Phase 4.1 — Offline Validation Suite for systemd Services.

VERIFIES:
1. Unit files parse correctly into valid [Unit], [Service], and [Install] sections.
2. Referenced entrypoint scripts exist in the repository.
3. Systemd execution configuration (User, Group, WorkingDirectory, EnvironmentFile).
4. PAPER-only startup safety guard in ExecStartPre prevents any LIVE execution.
5. Shadow observer strictly prohibits order submission and enforces isolation.
6. Restart limits are strictly bounded (Restart=on-failure, StartLimitBurst, StartLimitIntervalSec).
7. Zero secrets, API keys, or private keys embedded in unit files.
8. Least-privilege sandboxing directives (ProtectSystem, NoNewPrivileges, PrivateTmp, Capabilities).
9. Shutdown signals (KillSignal, TimeoutStopSec) support graceful termination.
"""

import os
import re
import sys
import subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
TRADING_DAEMON_SERVICE = SCRIPTS_DIR / "delta-trading-daemon.service"
SHADOW_OBSERVER_SERVICE = SCRIPTS_DIR / "delta-shadow-observer.service"
MONITORING_API_SERVICE = SCRIPTS_DIR / "delta-monitoring-api.service"


def _parse_systemd_unit(path: Path) -> dict:
    """Simple parser for systemd INI unit files supporting multi-line backslash continuations."""
    assert path.exists(), f"Unit file not found: {path}"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    sections = {}
    current_section = None
    accumulated_line = ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        if line.endswith("\\"):
            accumulated_line += line[:-1].strip() + " "
            continue
        else:
            accumulated_line += line
            line = accumulated_line.strip()
            accumulated_line = ""

        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            if current_section not in sections:
                sections[current_section] = {}
        elif current_section and "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key in sections[current_section]:
                existing = sections[current_section][key]
                if isinstance(existing, list):
                    existing.append(val)
                else:
                    sections[current_section][key] = [existing, val]
            else:
                sections[current_section][key] = val

    return sections


# =============================================================================
# 1. SYNTAX & SECTION PARSING TESTS
# =============================================================================

def test_01_unit_files_exist_and_parse():
    """Verify both new service unit files exist and parse with standard systemd sections."""
    for service_path in [TRADING_DAEMON_SERVICE, SHADOW_OBSERVER_SERVICE, MONITORING_API_SERVICE]:
        assert service_path.exists(), f"Service file missing: {service_path}"
        parsed = _parse_systemd_unit(service_path)
        assert "Unit" in parsed, f"Missing [Unit] in {service_path.name}"
        assert "Service" in parsed, f"Missing [Service] in {service_path.name}"
        assert "Install" in parsed, f"Missing [Install] in {service_path.name}"
        assert parsed["Install"].get("WantedBy") == "multi-user.target"


# =============================================================================
# 2. ENTRY POINT INTEGRITY TESTS
# =============================================================================

def test_02_entry_point_scripts_exist():
    """Verify that Python scripts referenced in ExecStart exist in repository."""
    # Trading Daemon
    td_parsed = _parse_systemd_unit(TRADING_DAEMON_SERVICE)
    exec_start_td = td_parsed["Service"]["ExecStart"]
    assert "cli.py paper-live" in exec_start_td
    assert (REPO_ROOT / "cli.py").is_file()

    # Shadow Observer
    so_parsed = _parse_systemd_unit(SHADOW_OBSERVER_SERVICE)
    exec_start_so = so_parsed["Service"]["ExecStart"]
    assert "scripts/run_shadow_observer.py" in exec_start_so
    assert (REPO_ROOT / "scripts" / "run_shadow_observer.py").is_file()

    # Monitoring API
    api_parsed = _parse_systemd_unit(MONITORING_API_SERVICE)
    exec_start_api = api_parsed["Service"]["ExecStart"]
    assert "services.monitoring_api:app" in exec_start_api
    assert (REPO_ROOT / "services" / "monitoring_api.py").is_file()


# =============================================================================
# 3. ENVIRONMENT & DEPLOYMENT PATH CONFIGURATION
# =============================================================================

def test_03_deployment_paths_and_service_account():
    """Verify User, Group, WorkingDirectory, and EnvironmentFile directives."""
    for service_path in [TRADING_DAEMON_SERVICE, SHADOW_OBSERVER_SERVICE]:
        parsed = _parse_systemd_unit(service_path)
        svc = parsed["Service"]
        assert svc.get("User") == "ubuntu", f"Invalid User in {service_path.name}"
        assert svc.get("Group") == "ubuntu", f"Invalid Group in {service_path.name}"
        assert svc.get("WorkingDirectory") == "/home/ubuntu/Local-AI-Trading-Agent"
        assert svc.get("EnvironmentFile") == "-/home/ubuntu/Local-AI-Trading-Agent/.env"
        assert "Environment" in svc


# =============================================================================
# 4. PAPER-ONLY SAFETY GUARD ENFORCEMENT
# =============================================================================

def test_04_trading_daemon_exec_start_pre_paper_guard():
    """Verify that ExecStartPre on trading daemon asserts PAPER mode and blocks LIVE."""
    td_parsed = _parse_systemd_unit(TRADING_DAEMON_SERVICE)
    exec_pre = td_parsed["Service"]["ExecStartPre"]

    assert "settings.mode == 'PAPER'" in exec_pre
    assert "settings.trading_mode == 'PAPER'" in exec_pre
    assert "settings.live_trading_enabled is False" in exec_pre
    assert "LIVE_TRADING_ENABLED" in exec_pre

    # Test execution: Normal PAPER mode should succeed
    env_paper = os.environ.copy()
    env_paper["TRADING_MODE"] = "PAPER"
    env_paper["LIVE_TRADING_ENABLED"] = "false"
    res_paper = subprocess.run(
        [sys.executable, "-c",
         "from config.orb_settings import settings; "
         "assert settings.mode == 'PAPER'; "
         "assert settings.trading_mode == 'PAPER'; "
         "assert settings.live_trading_enabled is False; "
         "print('OK')"],
        cwd=str(REPO_ROOT),
        env=env_paper,
        capture_output=True,
        text=True,
    )
    assert res_paper.returncode == 0
    assert "OK" in res_paper.stdout

    # Test execution: Simulated LIVE mode must fail
    env_live = os.environ.copy()
    env_live["TRADING_MODE"] = "LIVE"
    res_live = subprocess.run(
        [sys.executable, "-c",
         "from config.orb_settings import settings; "
         "assert settings.mode == 'PAPER'; "
         "print('FAIL_IF_REACHED')"],
        cwd=str(REPO_ROOT),
        env=env_live,
        capture_output=True,
        text=True,
    )
    assert res_live.returncode != 0
    assert ("ConfigurationError" in res_live.stderr or "AssertionError" in res_live.stderr or "FATAL" in res_live.stderr)


# =============================================================================
# 5. SHADOW OBSERVER ORDER PROHIBITION & ISOLATION
# =============================================================================

def test_05_shadow_observer_order_prohibition():
    """Verify that Shadow Observer has zero order-submission capability and validates isolation."""
    so_parsed = _parse_systemd_unit(SHADOW_OBSERVER_SERVICE)
    exec_pre = so_parsed["Service"]["ExecStartPre"]

    assert "verify_observer_isolation" in exec_pre
    assert "not hasattr(runner, 'place_order')" in exec_pre
    assert "not hasattr(runner, 'submit_order')" in exec_pre

    # Run the exact check in Python
    from services.shadow_observer.dry_run_runner import ShadowDryRunRunner
    from services.shadow_observer.observer_safety_guard import verify_observer_isolation

    verify_observer_isolation()
    runner = ShadowDryRunRunner()
    assert not hasattr(runner, "place_order")
    assert not hasattr(runner, "submit_order")
    assert not hasattr(runner, "execute_order")
    assert not hasattr(runner, "cancel_order")


# =============================================================================
# 6. BOUNDED RESTARTS & PERMANENT LOCKOUT BEHAVIOR
# =============================================================================

def test_06_bounded_restarts_and_lockout():
    """Verify that Restart=on-failure with bounded delay and burst limits preventing crash loops."""
    for service_path in [TRADING_DAEMON_SERVICE, SHADOW_OBSERVER_SERVICE]:
        parsed = _parse_systemd_unit(service_path)
        unit = parsed["Unit"]
        svc = parsed["Service"]

        # Unit-level rate limiting
        burst = int(unit.get("StartLimitBurst", "0"))
        interval = int(unit.get("StartLimitIntervalSec", "0"))
        assert 1 <= burst <= 5, f"StartLimitBurst must be between 1 and 5 (got {burst})"
        assert interval >= 60, f"StartLimitIntervalSec must be >= 60s (got {interval})"

        # Service-level restart behavior
        assert svc.get("Restart") == "on-failure", "Must use Restart=on-failure (never Restart=always)"
        restart_sec = int(svc.get("RestartSec", "0"))
        assert restart_sec >= 5, f"RestartSec must be >= 5s (got {restart_sec})"

        # Clean shutdown configuration
        assert "KillSignal" in svc
        timeout_stop = int(svc.get("TimeoutStopSec", "0"))
        assert 10 <= timeout_stop <= 60


# =============================================================================
# 7. ZERO EMBEDDED SECRETS AUDIT
# =============================================================================

def test_07_no_embedded_secrets_in_service_files():
    """Verify zero private keys, API secrets, or passwords are embedded in unit files."""
    forbidden_patterns = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"DELTA_API_KEY\s*=\s*[a-zA-Z0-9_\-]{8,}"),
        re.compile(r"DELTA_API_SECRET\s*=\s*[a-zA-Z0-9_\-]{8,}"),
        re.compile(r"AWS_SECRET_ACCESS_KEY\s*=\s*[a-zA-Z0-9_\-]{8,}"),
        re.compile(r"(password|passwd|token)\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    ]

    for service_path in [TRADING_DAEMON_SERVICE, SHADOW_OBSERVER_SERVICE]:
        text = service_path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            match = pattern.search(text)
            assert match is None, f"Leaked credential pattern '{pattern.pattern}' detected in {service_path.name}: {match.group(0)}"


# =============================================================================
# 8. SANDBOXING & PRIVILEGE RESTRICTIONS
# =============================================================================

def test_08_sandboxing_and_least_privilege_directives():
    """Verify Linux security sandboxing directives are configured."""
    for service_path in [TRADING_DAEMON_SERVICE, SHADOW_OBSERVER_SERVICE]:
        parsed = _parse_systemd_unit(service_path)
        svc = parsed["Service"]

        assert svc.get("ProtectSystem") == "strict"
        assert svc.get("ProtectHome") == "read-only"
        assert svc.get("NoNewPrivileges") == "true"
        assert svc.get("PrivateTmp") == "true"
        assert svc.get("ProtectKernelTunables") == "true"
        assert svc.get("ProtectControlGroups") == "true"
        assert svc.get("RestrictRealtime") == "true"
        assert svc.get("RestrictSUIDSGID") == "true"
        assert "CapabilityBoundingSet" in svc
        assert "AmbientCapabilities" in svc
        assert "ReadOnlyPaths" in svc
        assert "ReadWritePaths" in svc
