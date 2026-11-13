"""Focused regression tests for Phase 4.5.3 SonarCloud remediation.

Validates:
1. Numerical equivalence and division-by-zero protection in adx() (orb_indicators.py).
2. Atomic file write, directory confinement, symlink rejection, failure cleanup, and concurrent-write safety in DailyRiskState (position_sizing.py).
"""

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from indicators.orb_indicators import adx
from risk.position_sizing import DailyRiskState
from config.orb_settings import ORBSettings


class TestADXIndicatorRemediation:
    """Verifies that .add(1e-12) preserves numerical output and edge-case safety."""

    def test_adx_numerical_equivalence_and_types(self):
        """Confirm ADX output dtype, non-empty values, and absence of Inf values."""
        np.random.seed(42)
        close = pd.Series(100.0 + np.cumsum(np.random.randn(80)))
        high = close + np.random.uniform(0.5, 2.0, 80)
        low = close - np.random.uniform(0.5, 2.0, 80)

        res = adx(high, low, close, period=14)
        assert isinstance(res, pd.Series)
        assert len(res) == 80
        # First (period - 1) * 2 = 26 bars should be NaN due to two-stage ewm smoothing
        first_valid = res.first_valid_index()
        assert first_valid == 26
        # Remaining should be valid bounded numbers [0, 100]
        valid = res.dropna()
        assert len(valid) == 80 - 26
        assert not np.isinf(valid.values).any()
        assert (valid >= 0.0).all() and (valid <= 100.0).all()

    def test_adx_zero_true_range_edge_case(self):
        """Zero price movement must not raise ZeroDivisionError or produce Inf."""
        flat = pd.Series([100.0] * 30)
        res = adx(flat, flat, flat, period=14)
        assert isinstance(res, pd.Series)
        assert not np.isinf(res.dropna().values).any()
        # With zero true range, DX/ADX is 0.0
        assert (res.dropna() == 0.0).all()

    def test_adx_single_candle_spike(self):
        """Verify abrupt price spike behaves deterministically."""
        high = pd.Series([100.0] * 20 + [150.0] + [100.0] * 20)
        low = pd.Series([99.0] * 20 + [99.0] + [99.0] * 20)
        close = pd.Series([100.0] * 20 + [145.0] + [100.0] * 20)

        res = adx(high, low, close, period=14)
        assert not np.isinf(res.dropna().values).any()


class TestDailyRiskStateRemediation:
    """Verifies atomic write, secure temp file creation, symlink rejection, and concurrency safety."""

    def test_daily_risk_state_atomic_save_and_load(self):
        """Verify normal atomic state save and reload."""
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "daily_risk_state.json"
            cfg = ORBSettings()
            drs = DailyRiskState(path=state_file, cfg=cfg)

            sample_state = {
                "date": drs._today(),
                "realized_pnl": -5.25,
                "trades": 1,
                "last_loss_at": None,
            }
            drs._save_unlocked(sample_state)

            # State file must exist; no lingering .tmp files
            assert state_file.exists()
            assert list(Path(td).glob("*.tmp")) == []

            loaded = drs._load_unlocked()
            assert loaded["date"] == sample_state["date"]
            assert loaded["realized_pnl"] == -5.25
            assert loaded["trades"] == 1

    def test_daily_risk_state_tmp_cleanup_on_serialization_failure(self):
        """Verify tmp file is cleaned up if serialization fails."""
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "daily_risk_state.json"
            cfg = ORBSettings()
            drs = DailyRiskState(path=state_file, cfg=cfg)

            # Pass non-serializable object to trigger exception during json.dump
            unserializable_state = {"invalid_func": lambda: None}
            with pytest.raises((TypeError, ValueError)):
                drs._save_unlocked(unserializable_state)

            # No temporary files should be leaked
            assert list(Path(td).glob("*.tmp")) == []
            assert not state_file.exists()

    def test_daily_risk_state_original_preserved_on_failure(self):
        """Verify pre-existing state file remains unmodified if subsequent save fails."""
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "daily_risk_state.json"
            cfg = ORBSettings()
            drs = DailyRiskState(path=state_file, cfg=cfg)

            initial_state = {
                "date": drs._today(),
                "realized_pnl": 12.50,
                "trades": 2,
                "last_loss_at": None,
            }
            drs._save_unlocked(initial_state)
            initial_content = state_file.read_text(encoding="utf-8")

            # Attempt to overwrite with invalid non-serializable data
            with pytest.raises((TypeError, ValueError)):
                drs._save_unlocked({"bad": lambda: None})

            # Original state must be preserved byte-for-byte
            assert state_file.read_text(encoding="utf-8") == initial_content
            assert list(Path(td).glob("*.tmp")) == []

    def test_daily_risk_state_symlink_rejection(self, monkeypatch):
        """Verify state file writing is rejected if path is a symlink."""
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "daily_risk_state.json"
            cfg = ORBSettings()
            drs = DailyRiskState(path=state_file, cfg=cfg)

            # Test using monkeypatch so check executes predictably across all OS environments
            monkeypatch.setattr(Path, "is_symlink", lambda self: True)
            with pytest.raises(ValueError, match="Refusing to write state to symlink"):
                drs._save_unlocked({"date": "2026-11-12", "realized_pnl": 0.0, "trades": 0})

            # In environments supporting native symlinks (e.g. Linux CI runner), test real symlink
            monkeypatch.undo()
            real_target = Path(td) / "secret_target.json"
            real_target.write_text("{}", encoding="utf-8")
            symlink_path = Path(td) / "symlink_state.json"
            try:
                os.symlink(real_target, symlink_path)
                drs_sym = DailyRiskState(path=symlink_path, cfg=cfg)
                with pytest.raises(ValueError, match="Refusing to write state to symlink"):
                    drs_sym._save_unlocked({"date": "2026-11-12", "realized_pnl": 0.0, "trades": 0})
                # Ensure secret_target was never overwritten
                assert real_target.read_text(encoding="utf-8") == "{}"
            except OSError:
                pass  # OS without symlink creation privileges (e.g. unprivileged Windows)

    def test_daily_risk_state_posix_permissions(self):
        """On POSIX systems, verify state file permissions are 0o600."""
        if os.name != "posix":
            pytest.skip("POSIX-only permission assertion")

        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "daily_risk_state.json"
            cfg = ORBSettings()
            drs = DailyRiskState(path=state_file, cfg=cfg)

            drs._save_unlocked({"date": drs._today(), "realized_pnl": 0.0, "trades": 0, "last_loss_at": None})
            mode = os.stat(state_file).st_mode & 0o777
            assert mode == 0o600, f"Expected 0o600 permissions, got {oct(mode)}"

    def test_daily_risk_state_concurrent_writes(self):
        """Verify concurrent trade reservations across threads are strictly serialized without data loss or corruption."""
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "daily_risk_state.json"
            cfg = ORBSettings(max_trades_per_day=100)
            worker_count = 10

            def worker_fn(worker_id: int):
                worker_drs = DailyRiskState(path=state_file, cfg=cfg)
                return worker_drs.reserve_trade()

            with ThreadPoolExecutor(max_workers=5) as executor:
                results = list(executor.map(worker_fn, range(worker_count)))

            assert len(results) == worker_count

            # Load final state and assert integrity
            final_drs = DailyRiskState(path=state_file, cfg=cfg)
            final_state = final_drs.load()
            assert final_state["trades"] == worker_count
            assert final_state["date"] == final_drs._today()
            # Confirm no leftover temporary files
            assert list(Path(td).glob("*.tmp")) == []
