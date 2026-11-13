"""
Regression tests for CI/CD settings attribute alignment and production dependencies (GAP-03 and GAP-04).
Ensures settings.mode and settings.trading_mode remain unified and production dependencies are resolvable.
"""
import os
import subprocess
import sys
import yaml
import pytest
from config.orb_settings import ORBSettings, settings, validate_settings


def test_orb_settings_has_mode_and_trading_mode_attributes():
    """Verify that ORBSettings supports both .mode and .trading_mode seamlessly."""
    cfg = ORBSettings()
    assert hasattr(cfg, "mode"), "ORBSettings must have 'mode' attribute"
    assert hasattr(cfg, "trading_mode"), "ORBSettings must have 'trading_mode' attribute for backward compatibility"
    assert cfg.mode == "PAPER", f"Expected mode == 'PAPER', got {cfg.mode}"
    assert cfg.trading_mode == "PAPER", f"Expected trading_mode == 'PAPER', got {cfg.trading_mode}"
    assert cfg.trading_mode == cfg.mode, "trading_mode property must mirror mode exactly"


def test_orb_settings_parameterized_mode():
    """Verify that mode and trading_mode both reflect custom mode settings."""
    cfg = ORBSettings(mode="ALERT_ONLY")
    assert cfg.mode == "ALERT_ONLY"
    assert cfg.trading_mode == "ALERT_ONLY"
    validate_settings(cfg)


def test_workflow_yaml_syntax_and_safety_step():
    """Validate that deploy_and_track.yml is valid YAML and the safety step succeeds."""
    workflow_path = os.path.join(".github", "workflows", "deploy_and_track.yml")
    assert os.path.isfile(workflow_path), f"Workflow file missing: {workflow_path}"

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    assert "jobs" in workflow, "Workflow missing 'jobs' section"
    assert "test_and_validate" in workflow["jobs"], "Workflow missing 'test_and_validate' job"

    steps = workflow["jobs"]["test_and_validate"]["steps"]
    safety_step = next((s for s in steps if "Verify Strict PAPER Trading Safety" in s.get("name", "")), None)
    assert safety_step is not None, "Safety verification step missing in workflow"

    script = safety_step["run"]
    assert "settings.mode == 'PAPER'" in script, "Safety step must assert settings.mode == 'PAPER'"
    assert "settings.trading_mode == 'PAPER'" in script, "Safety step must assert settings.trading_mode == 'PAPER'"

    # Extract and execute the exact Python inline snippet from the workflow
    python_code = script.split('python -c "', 1)[1].rsplit('"', 1)[0]
    result = subprocess.run([sys.executable, "-c", python_code], capture_output=True, text=True)
    assert result.returncode == 0, f"Workflow safety Python snippet failed: {result.stderr}"
    assert "Safety verification PASSED" in result.stdout


def test_requirements_contains_production_dependencies():
    """Verify that requirements.txt explicitly lists all required production packages."""
    with open("requirements.txt", "r", encoding="utf-8") as f:
        content = f.read()

    required_pkgs = ["websockets", "fastapi", "uvicorn", "httpx"]
    for pkg in required_pkgs:
        assert pkg in content, f"Production package '{pkg}' missing from requirements.txt"


def test_production_dependencies_importable():
    """Verify that websockets, fastapi, uvicorn, and httpx can be imported cleanly and satisfy version constraints."""
    import websockets
    import fastapi
    import uvicorn
    import httpx

    assert websockets.__version__ is not None
    assert fastapi.__version__ is not None
    assert uvicorn.__version__ is not None
    assert httpx.__version__ is not None
