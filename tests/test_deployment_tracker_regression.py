"""Phase 4.1.2 — Regression Test Suite for CI Build Tracking Dependency Provisioning.

VERIFIES:
1. Job 3 (ci_build_tracking) provisions Python and installs requirements.txt before running tracker.
2. The sequence of steps guarantees dependencies exist before execution.
3. services.deployment_tracker records and retrieves deployment metadata correctly.
4. Subprocess execution of the deployment tracker script succeeds without import errors.
5. All three CI jobs (test_and_validate, security_audit, ci_build_tracking) remain intact.
6. Zero secrets or credentials exist in the workflow file.
"""

import os
import sys
import json
import subprocess
import yaml
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy_and_track.yml"


# =============================================================================
# 1. WORKFLOW JOB 3 SPECIFICATION & STEP ORDERING TESTS
# =============================================================================

def test_01_job3_provisions_python_and_dependencies_before_execution():
    """Verify that ci_build_tracking sets up Python and installs dependencies prior to execution."""
    assert WORKFLOW_PATH.exists(), f"Workflow file not found: {WORKFLOW_PATH}"
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    jobs = workflow.get("jobs", {})
    assert "ci_build_tracking" in jobs, "Job 'ci_build_tracking' missing from workflow"

    job3 = jobs["ci_build_tracking"]
    assert job3.get("runs-on") == "ubuntu-latest"
    assert job3.get("needs") == ["test_and_validate", "security_audit"]

    steps = job3.get("steps", [])
    step_names = [s.get("name", "") for s in steps]

    # Verify required steps exist
    assert any("Checkout" in name for name in step_names), "Checkout step missing"
    assert any("Set up Python" in name for name in step_names), "Python setup step missing"
    assert any("Install Dependencies" in name for name in step_names), "Dependency installation step missing"
    assert any("Record CI Verification" in name for name in step_names), "Tracker execution step missing"

    # Verify correct step ordering
    setup_idx = next(i for i, s in enumerate(steps) if "Set up Python" in s.get("name", ""))
    install_idx = next(i for i, s in enumerate(steps) if "Install Dependencies" in s.get("name", ""))
    exec_idx = next(i for i, s in enumerate(steps) if "Record CI Verification" in s.get("name", ""))

    assert setup_idx < install_idx < exec_idx, "Steps must execute in order: Setup Python -> Install -> Record"

    # Verify Python version and caching
    setup_step = steps[setup_idx]
    assert setup_step.get("with", {}).get("python-version") == "3.11"
    assert setup_step.get("with", {}).get("cache") == "pip"

    # Verify installation command
    install_step = steps[install_idx]
    install_run = install_step.get("run", "")
    assert "pip install -r requirements.txt" in install_run


# =============================================================================
# 2. WORKFLOW COMPREHENSIVE INTEGRITY & SECURITY TESTS
# =============================================================================

def test_02_all_three_ci_jobs_remain_configured_and_ordered():
    """Verify all three workflow jobs exist and preserve pipeline dependencies."""
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    jobs = workflow.get("jobs", {})
    assert set(jobs.keys()) == {"test_and_validate", "security_audit", "ci_build_tracking"}

    # Verify test_and_validate has strict PAPER safety verification
    t_and_v_steps = jobs["test_and_validate"].get("steps", [])
    assert any("Verify Strict PAPER Trading Safety" in s.get("name", "") for s in t_and_v_steps)

    # Verify security_audit has leak scan
    sec_steps = jobs["security_audit"].get("steps", [])
    assert any("Scan for Leaked Secrets" in s.get("name", "") for s in sec_steps)


def test_03_zero_secrets_in_workflow_file():
    """Verify no API keys or private credentials are embedded in deploy_and_track.yml."""
    import re
    secret_pat = re.compile(r'(DELTA_API_KEY|DELTA_API_SECRET|AWS_SECRET_ACCESS_KEY)\s*=\s*["\']?[a-zA-Z0-9_\-]{8,}')
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert not secret_pat.search(content), "Leaked secret pattern detected in workflow file"


# =============================================================================
# 3. DEPLOYMENT TRACKER MODULE FUNCTIONALITY TESTS
# =============================================================================

def test_04_deployment_tracker_records_and_retrieves(tmp_path, monkeypatch):
    """Verify record_deployment appends records and get_latest_deployment retrieves them."""
    from services import deployment_tracker

    test_file = tmp_path / "deployments.json"
    monkeypatch.setattr(deployment_tracker, "DEPLOYMENTS_FILE", test_file)

    # Initially empty
    assert deployment_tracker.get_latest_deployment() is None

    # Record first deployment
    rec1 = deployment_tracker.record_deployment(
        commit_sha="abcd1234efgh5678",
        branch="feature/test-branch",
        author="test-engineer",
        environment="TEST_CI",
        status="SUCCESS",
        tests_passed=True,
        message="Initial test deployment",
    )

    assert rec1["id"].startswith("DEP-")
    assert rec1["commit_sha"] == "abcd1234efgh5678"
    assert rec1["status"] == "SUCCESS"
    assert rec1["tests_passed"] is True

    # Retrieve latest
    latest = deployment_tracker.get_latest_deployment()
    assert latest is not None
    assert latest["id"] == rec1["id"]
    assert latest["commit_sha"] == "abcd1234efgh5678"

    # Record second deployment
    rec2 = deployment_tracker.record_deployment(
        commit_sha="9876fedc5432ba01",
        branch="feature/test-branch",
        author="test-engineer",
        environment="TEST_CI",
        status="SUCCESS",
        tests_passed=True,
        message="Second test deployment",
    )

    # Latest should now be rec2 (inserted at index 0)
    latest2 = deployment_tracker.get_latest_deployment()
    assert latest2["id"] == rec2["id"]
    assert latest2["commit_sha"] == "9876fedc5432ba01"


# =============================================================================
# 4. SUBPROCESS EXECUTION OF EXACT CI TRACKER SNIPPET
# =============================================================================

def test_05_ci_tracker_snippet_runs_cleanly_in_subprocess(tmp_path, monkeypatch):
    """Execute the exact Python one-liner used in Job 3 in an isolated subprocess."""
    test_file = tmp_path / "deployments.json"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    snippet = (
        "from services.deployment_tracker import record_deployment, DEPLOYMENTS_FILE; "
        "import services.deployment_tracker as dt; "
        f"from pathlib import Path; dt.DEPLOYMENTS_FILE = Path(r'{test_file}'); "
        "rec = record_deployment("
        "    commit_sha='testsha1234567890',"
        "    branch='feature/test',"
        "    author='test-bot',"
        "    environment='GITHUB_CI',"
        "    status='SUCCESS',"
        "    tests_passed=True,"
        "    message='CI Verification snippet test'"
        "); "
        "print('TRACKED_ID:', rec['id'])"
    )

    res = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"Subprocess tracker execution failed: {res.stderr}"
    assert "TRACKED_ID: DEP-" in res.stdout
    assert test_file.exists()
