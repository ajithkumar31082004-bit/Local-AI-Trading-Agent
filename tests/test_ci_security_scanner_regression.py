"""Phase 4.1.1 — CI Security Scanner Regression Test Suite.

VERIFIES:
1. Synthetic redaction test fixtures properly exercise redaction without triggering static scanner.
2. The CI scanner logic reliably detects deliberately introduced fake credentials in temporary files.
3. All monitored credential types (DELTA_API_KEY, DELTA_API_SECRET, AWS_SECRET_ACCESS_KEY) are flagged.
4. Benign non-secret configuration variables (TRADING_MODE, etc.) are never flagged.
5. The entire repository is currently 100% clean of credential patterns under the CI scanner.
"""

import os
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The exact regex used in .github/workflows/deploy_and_track.yml
CI_SECRET_PATTERN = re.compile(
    r'(DELTA_API_KEY|DELTA_API_SECRET|AWS_SECRET_ACCESS_KEY)\s*=\s*["\']?[a-zA-Z0-9_\-]{8,}'
)


def run_scanner_on_directory(scan_root: Path) -> list:
    """Execute the exact GitHub Actions secret-scanning logic on a directory."""
    findings = []
    for root, dirs, files in os.walk(scan_root):
        if any(x in root for x in [".git", ".venv", "node_modules"]):
            continue
        for f in files:
            if f.endswith((".py", ".json", ".yaml", ".yml", ".md", ".sh")):
                p = os.path.join(root, f)
                with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                    for idx, line in enumerate(fp, 1):
                        if CI_SECRET_PATTERN.search(line):
                            findings.append((p, idx, line.strip()))
    return findings


# =============================================================================
# 1. SYNTHETIC REDACTION FIXTURE VERIFICATION
# =============================================================================

def test_01_synthetic_redaction_fixtures_functionality():
    """Verify that redaction utility works properly with synthetic test fixtures."""
    from services.monitoring_api import redact_sensitive_info, sanitize_dict

    # Construct dynamically to verify runtime regex while avoiding static match
    key_prefix = "DELTA_" + "API_KEY"
    raw_text = f"{key_prefix}=dlt_mock_test_token_12345 and Bearer my_mock_bearer_token_12345"
    sanitized = redact_sensitive_info(raw_text)

    assert "dlt_mock_test_token_12345" not in sanitized
    assert "my_mock_bearer_token_12345" not in sanitized
    assert "***REDACTED***" in sanitized
    assert f"{key_prefix}=***REDACTED***" in sanitized

    # Test dictionary sanitization
    sample_dict = {
        "api_key": "mock_api_key_value",
        "secret": "mock_secret_value",
        "nested": {"token": "mock_token_value"},
        "symbol": "XAUTUSD",
        "lots": 1,
    }
    cleaned = sanitize_dict(sample_dict)
    assert cleaned["api_key"] == "***REDACTED***"
    assert cleaned["secret"] == "***REDACTED***"
    assert cleaned["nested"]["token"] == "***REDACTED***"
    assert cleaned["symbol"] == "XAUTUSD"
    assert cleaned["lots"] == 1


# =============================================================================
# 2. DELIBERATE FAKE CREDENTIAL DETECTION (NEGATIVE TESTING)
# =============================================================================

def test_02_scanner_detects_deliberate_fake_credential_in_temp_file(tmp_path):
    """Verify that the scanner detects fake credential patterns in temporary files."""
    test_file = tmp_path / "test_fake_creds.py"
    # Construct dynamically so this test file itself does not contain the static pattern
    prefix = "DELTA_" + "API_KEY"
    fake_val = "fake_val_12345678"
    test_file.write_text(f'{prefix}="{fake_val}"\n', encoding="utf-8")

    findings = run_scanner_on_directory(tmp_path)
    assert len(findings) == 1
    file_path, line_no, matched_line = findings[0]
    assert line_no == 1
    assert prefix in matched_line
    assert fake_val in matched_line


def test_03_scanner_detects_all_monitored_credential_types(tmp_path):
    """Verify scanner catches DELTA_API_KEY, DELTA_API_SECRET, and AWS_SECRET_ACCESS_KEY."""
    monitored = ["DELTA_API_" + "KEY", "DELTA_API_" + "SECRET", "AWS_SECRET_ACCESS_" + "KEY"]

    for idx, var_name in enumerate(monitored, 1):
        target_dir = tmp_path / f"test_{idx}"
        target_dir.mkdir()
        target_file = target_dir / "config.env"
        target_file.write_text(f"{var_name}=sample_fake_secret_12345\n", encoding="utf-8")

        # Rename to .sh or .py so scanner inspects it
        scanned_file = target_dir / f"config_{idx}.sh"
        target_file.rename(scanned_file)

        findings = run_scanner_on_directory(target_dir)
        assert len(findings) == 1, f"Scanner failed to detect {var_name}"


# =============================================================================
# 3. BENIGN NON-SECRET PATTERNS NEVER FLAGGED
# =============================================================================

def test_04_scanner_ignores_benign_configuration(tmp_path):
    """Verify benign environment variables do not cause false positives."""
    benign_file = tmp_path / "settings.env.py"
    benign_file.write_text(
        "TRADING_MODE='PAPER'\n"
        "LIVE_TRADING_ENABLED=false\n"
        "APPROVED_MAX_PAPER_LOTS=1\n"
        "DELTA_SYMBOL='XAUTUSD'\n"
        "DELTA_PRODUCT_ID=131253\n"
        "DELTA_WS_URL='wss://socket.india.delta.exchange'\n"
        "MONITORING_RATE_LIMIT=60\n",
        encoding="utf-8"
    )

    findings = run_scanner_on_directory(tmp_path)
    assert len(findings) == 0, f"False positive detected on benign configuration: {findings}"


# =============================================================================
# 4. REPOSITORY FULL CLEANLINESS VERIFICATION
# =============================================================================

def test_05_repository_is_completely_clean():
    """Verify zero secrets or credential patterns exist across the entire repository."""
    findings = run_scanner_on_directory(REPO_ROOT)
    assert len(findings) == 0, f"Real or unredacted credentials detected in repository: {findings}"
