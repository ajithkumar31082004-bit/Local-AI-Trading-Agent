"""Root pytest configuration and global safety isolation fixtures."""
from pathlib import Path
import pytest
from services.governance_supervisor import GovernanceSupervisor


@pytest.fixture(autouse=True)
def isolate_test_governance_ledger(tmp_path, monkeypatch):
    """Automatically isolate GovernanceSupervisor incident ledger per test function.

    Prevents tests that intentionally exercise safety violation paths (e.g. testing live order rejection)
    from persisting an active breach into the repository data/ directory and tripping the global
    fail-closed circuit breaker for downstream tests.
    """
    test_ledger = tmp_path / "gov_incidents_test.json"
    monkeypatch.setattr(GovernanceSupervisor, "DEFAULT_LEDGER_PATH", test_ledger)
