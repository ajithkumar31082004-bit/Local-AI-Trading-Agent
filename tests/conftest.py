"""Root pytest configuration and global safety isolation fixtures."""
from pathlib import Path
import os
import pytest

# Ensure global test environment defaults to safe PAPER mode and isolated risk settings
os.environ["TRADING_MODE"] = "PAPER"
os.environ["ORB_MODE"] = "PAPER"
os.environ["LIVE_TRADING_ENABLED"] = "false"
os.environ["ORB_LIVE_TRADING_ENABLED"] = "false"
os.environ["ORB_MAX_RISK_PCT"] = "0.5"
os.environ["ORB_MAX_TRADES_PER_DAY"] = "1"
os.environ["MAX_TRADES_PER_DAY"] = "1"

import config.orb_settings
config.orb_settings.settings = config.orb_settings.ORBSettings(
    mode="PAPER",
    risk_pct=0.5,
    max_trades_per_day=1,
    live_trading_enabled=False,
)

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
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ORB_MODE", "PAPER")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("ORB_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("ORB_MAX_RISK_PCT", "0.5")
    monkeypatch.setenv("ORB_MAX_TRADES_PER_DAY", "1")
    monkeypatch.setenv("MAX_TRADES_PER_DAY", "1")

    # Isolate dry_run_runner from local uncommitted .env file
    dummy_env_dir = tmp_path / "dummy_root"
    dummy_env_dir.mkdir(parents=True, exist_ok=True)
    safe_env = dummy_env_dir / ".env"
    safe_env.write_text("TRADING_MODE=PAPER\nLIVE_TRADING_ENABLED=false\n", encoding="utf-8")
    try:
        import services.shadow_observer.dry_run_runner as drr
        monkeypatch.setattr(drr, "ROOT_DIR", dummy_env_dir)
    except Exception:
        pass
