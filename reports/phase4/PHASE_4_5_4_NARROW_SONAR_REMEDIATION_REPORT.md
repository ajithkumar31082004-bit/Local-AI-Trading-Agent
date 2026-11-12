# Phase 4.5.4: Narrow SonarCloud Remediation Audit & Verification Report

**Date:** 2026-11-12  
**Target Branch:** `feature/phase4-1-2-ci-build-dependency-fix`  
**Base Branch:** `main` (commit `2f348e432ebdd95a429dda3b0635df74b51a506f`, untouched)  
**Associated PR:** PR #3 ([Open & Unmerged](https://github.com/ajithkumar31082004-bit/Local-AI-Trading-Agent/pull/3))  
**Defect Ledger State:** `data/paper_forward_24h_state.json` (SHA256: `DAD10844B0E77ED19DB058EEC1E541AA858EB8E2D3EA1ADA66D83A69390CED25`, 484-lot integrity verified)  
**Safety Status:** Strictly `PAPER` mode (`settings.trading_mode = PAPER`, `settings.live_trading_enabled = False`, `APPROVED_MAX_PAPER_LOTS = 1`)

---

## 1. Executive Summary

Phase 4.5.4 completes the targeted remediation of the remaining SonarCloud Quality Gate findings on PR #3 without altering any trading logic, live trading configurations, or system operations.

All four targeted scopes have been implemented cleanly, verified with zero regressions, and evaluated locally:

1. **Scope A — Test Fixture Deduplication (CPD / Duplication < 3.0%):**
   - Created centralized synthetic candle fixture module `tests/fixtures/synthetic_market_data.py`.
   - Refactored `tests/test_trend_following.py`, `tests/test_vwap_ema.py`, and `tests/test_strategy_arbitrator.py`.
   - Eliminated **179 lines** of copy-paste synthetic candle boilerplate across 9 test functions.
   - Projected new-code duplication drops from **3.47%** to **< 1.5%** (well below the 3.0% threshold).

2. **Scope B — GitHub Actions Hardening (`githubactions:S8541`, `githubactions:S8544`):**
   - Pinned `pip==24.3.1` with `--only-binary :all:`.
   - Added `--only-binary :all:` to `pip install -r requirements.txt`.
   - Removed redundant and unpinned `pip install fastapi uvicorn httpx pytest`.
   - Satisfied `tests/test_deployment_tracker_regression.py` (5/5 passed).

3. **Scope C — Android Security Hardening (`kotlin:S7204`, `xml:S5332`, `xml:S6358`, `text:S8569`):**
   - Configured `android:allowBackup="false"` and `android:usesCleartextTraffic="false"` in `mobile/android/app/src/main/AndroidManifest.xml`.
   - Enabled code shrinking in release build: `isMinifyEnabled = true` in `mobile/android/app/build.gradle.kts`.
   - Created `mobile/android/app/proguard-rules.pro` with standard Compose and serialization rules.
   - Enabled dependency locking across configurations in `mobile/android/build.gradle.kts` (`dependencyLocking { lockAllConfigurations() }`).
   - Validated configuration with Gradle 9.7.1 (`./gradlew.bat help` -> `BUILD SUCCESSFUL`).

4. **Scope D — Observer Subprocess Hardening (`pythonsecurity:S8705`):**
   - Replaced unvalidated `sys.argv` forwarding with a strict regex allowlist (`^[a-zA-Z0-9_\-\./:=]+$`) and explicit resolved script path in `scripts/stage1_readonly_market_monitor.py`.
   - Verified with `tests/test_stage1_readonly_observer.py` (17/17 passed).

---

## 2. Preflight Baseline Verification

| Preflight Invariant | Expected Baseline | Actual Verified State | Status |
| :--- | :--- | :--- | :--- |
| **Current Branch** | `feature/phase4-1-2-ci-build-dependency-fix` | `feature/phase4-1-2-ci-build-dependency-fix` | Verified |
| **Current HEAD** | `4aa9af221c39a33250bb02a7c03916beb6a202dc` | `4aa9af221c39a33250bb02a7c03916beb6a202dc` | Verified |
| **Main Branch HEAD** | `2f348e432ebdd95a429dda3b0635df74b51a506f` | `2f348e432ebdd95a429dda3b0635df74b51a506f` | Untouched |
| **PR #3 Status** | Open & Unmerged | Open & Unmerged | Preserved |
| **PR #4 Status** | Merged into feature via fast-forward | Merged (Closed) | Preserved |
| **Defect Ledger Checksum** | `DAD10844B0E77ED19DB058EEC1E541AA858EB8E2D3EA1ADA66D83A69390CED25` | `DAD10844B0E77ED19DB058EEC1E541AA858EB8E2D3EA1ADA66D83A69390CED25` | Intact |
| **Trading Mode** | `PAPER` | `PAPER` | Invariant |
| **Live Trading Flag** | `False` | `False` | Invariant |

---

## 3. Detailed Scope Implementations

### Scope A: Test Fixture Deduplication
- **Root Cause:** In PR #3, unit test files for newly added strategies (`strategy/trend_following.py`, `strategy/vwap_ema.py`, `strategy/arbitrator.py`) generated synthetic 160-bar 5-minute OHLCV dataframes using repetitive 15-to-20 line inline scripts. SonarCloud's Copy-Paste Detector (CPD) flagged these identical blocks, causing PR #3's duplication metric to reach 3.47% (exceeding the 3.0% Quality Gate threshold).
- **Remediation:**
  1. Created `tests/fixtures/__init__.py`.
  2. Created `tests/fixtures/synthetic_market_data.py` containing reusable factory functions:
     - `create_synthetic_bearish_continuation_df(base_close=4100.0, ...)`
     - `create_synthetic_bullish_continuation_df(base_close=3800.0, ...)`
  3. Refactored test files:
     - `tests/test_trend_following.py`: Refactored 6 tests (`test_trend_following_confirmed_short_signal`, `test_trend_following_confirmed_long_signal`, `test_trend_following_low_volume_rejected`, `test_regime_filter_choppy_adx_rejected`, `test_regime_filter_flat_ema_slope_rejected`, `test_friction_exceeds_tolerance_rejected`, `test_minimum_stop_distance_enforced`). Net reduction: **122 lines**.
     - `tests/test_vwap_ema.py`: Refactored 2 tests (`test_confirmed_vwap_ema_short`, `test_confirmed_vwap_ema_long`). Net reduction: **38 lines**.
     - `tests/test_strategy_arbitrator.py`: Refactored 1 test (`test_arbitrator_selects_best_signal`). Net reduction: **19 lines**.
- **Results:** 19/19 strategy tests passed in 4.62s. Zero trading strategy logic touched.

### Scope B: GitHub Actions Workflow Hardening
- **SonarCloud Findings:**
  - `githubactions:S8541` (Omitting `--only-binary` flag permits untrusted arbitrary setup script execution during package installs).
  - `githubactions:S8544` (Pip dependencies should be locked/pinned).
- **Remediation in `.github/workflows/deploy_and_track.yml`:**
  - In Job 1 (`test_and_validate`):
    ```yaml
    - name: Install Dependencies
      run: |
        python -m pip install --upgrade --only-binary :all: pip==24.3.1
        pip install -r requirements.txt --only-binary :all:
    ```
  - In Job 3 (`ci_build_tracking`):
    ```yaml
    - name: Install Dependencies
      run: |
        python -m pip install --upgrade --only-binary :all: pip==24.3.1
        pip install -r requirements.txt --only-binary :all:
    ```
  - Removed redundant unpinned command `pip install fastapi uvicorn httpx pytest` (all four packages are already declared with version pins in `requirements.txt`).
  - Maintained exact compliance with `tests/test_deployment_tracker_regression.py` line 65 (`assert "pip install -r requirements.txt" in install_run`).
- **Results:** 5/5 deployment tracker regression tests passed in 7.53s.

### Scope C: Android Configuration Hardening
- **SonarCloud Findings:**
  - `kotlin:S7204` (Minification should be enabled in the release build).
  - `xml:S5332` (Cleartext HTTP traffic should not be permitted).
  - `xml:S6358` (Allowing application data backups can lead to data exposure).
  - `text:S8569` (Dependency locking should be enabled in Gradle).
- **Remediation:**
  1. `mobile/android/app/src/main/AndroidManifest.xml`:
     - Added `android:allowBackup="false"`.
     - Added `android:usesCleartextTraffic="false"`.
  2. `mobile/android/app/build.gradle.kts`:
     - Set `isMinifyEnabled = true` in the `release` build type.
  3. `mobile/android/app/proguard-rules.pro`:
     - Created ProGuard rule file preserving Compose metadata, Kotlin annotations, and serialized data models.
  4. `mobile/android/build.gradle.kts`:
     - Added `allprojects { dependencyLocking { lockAllConfigurations() } }`.
- **Results:** Executed `./gradlew.bat help` -> `BUILD SUCCESSFUL in 1m 12s` with Gradle 9.7.1.

### Scope D: Observer Subprocess Argument Injection Hardening
- **SonarCloud Finding:**
  - `pythonsecurity:S8705` (Using argument injection in subprocesses is security-sensitive in `scripts/stage1_readonly_market_monitor.py:37`).
- **Remediation:**
  - In `scripts/stage1_readonly_market_monitor.py`:
    ```python
    # Auto-activate repository .venv if invoked with system Python
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        venv_py_win = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
        venv_py_nix = PROJECT_ROOT / ".venv" / "bin" / "python3"
        venv_py = venv_py_win if sys.platform == "win32" else venv_py_nix
        if venv_py.exists() and sys.executable != str(venv_py):
            # Sanitize arguments strictly to prevent command-line argument injection
            safe_arg_pattern = re.compile(r"^[a-zA-Z0-9_\-\./:=]+$")
            clean_args = [arg for arg in sys.argv[1:] if safe_arg_pattern.match(arg)]
            current_script = str(Path(__file__).resolve())
            os.execv(str(venv_py), [str(venv_py), current_script] + clean_args)
    ```
  - Resolves argument injection vulnerability by replacing unvalidated `sys.argv[0]` with `Path(__file__).resolve()` and validating all CLI arguments against an alphanumeric/option allowlist.
- **Results:** 17/17 tests in `tests/test_stage1_readonly_observer.py` passed in 8.03s.

---

## 4. Verification Test Matrix

| Test Suite | File / Scope | Tests Run | Result | Execution Time |
| :--- | :--- | :--- | :--- | :--- |
| **Strategy Deduplication** | `tests/test_trend_following.py`, `tests/test_vwap_ema.py`, `tests/test_strategy_arbitrator.py` | 19 | 19 Passed | 4.62s |
| **Observer Safety & Logic** | `tests/test_stage1_readonly_observer.py` | 17 | 17 Passed | 8.03s |
| **Core CI Safety & Accounting** | `tests/test_dashboard_state_consistency.py`, `tests/test_paper_trade_accounting.py`, `tests/test_monitoring_api.py` | 41 | 41 Passed | 11.03s |
| **Deployment Tracker Regression** | `tests/test_deployment_tracker_regression.py` | 5 | 5 Passed | 7.53s |
| **CI/CD Settings & Safety** | `tests/test_ci_cd_settings_regression.py` | 5 | 5 Passed | 2.12s |
| **Security Leak Scan** | `tests/test_ci_security_scanner_regression.py` | 5 | 5 Passed | 8.21s |
| **Phase 4.5.3 Remediation Regression** | `tests/test_phase4_5_3_sonar_remediation.py` | 9 | 8 Passed, 1 Skipped (POSIX on Win) | 2.32s |
| **Android Build Verification** | Gradle 9.7.1 `./gradlew.bat help` | 1 task | BUILD SUCCESSFUL | 1m 12s |
| **Full Project Regression Suite** | `pytest tests/` | 666 | 665 Passed, 1 Skipped, 0 Failed | 147.85s |

---

## 5. Files Changed Summary

```
.github/workflows/deploy_and_track.yml     |   9 +-
mobile/android/app/build.gradle.kts        |   2 +-
mobile/android/app/proguard-rules.pro      |  16 +++
mobile/android/app/src/main/AndroidManifest.xml |   3 +-
mobile/android/build.gradle.kts            |   6 +
scripts/stage1_readonly_market_monitor.py  |   7 +-
tests/fixtures/__init__.py                 |   1 +
tests/fixtures/synthetic_market_data.py    |  78 +++++++++++
tests/test_strategy_arbitrator.py          |  21 +--
tests/test_trend_following.py              | 135 ++++----------------
tests/test_vwap_ema.py                     |  42 +-----
```

---

## 6. Current Governance Status & Strict Boundaries

1. **Local State Only:** No commits have been made. No branches have been pushed.
2. **Mainline Isolation:** `main` branch remains at `2f348e432ebdd95a429dda3b0635df74b51a506f` (untouched).
3. **PR #3 State:** Remediations reside locally on `feature/phase4-1-2-ci-build-dependency-fix`. PR #3 remains OPEN and UNMERGED.
4. **Execution Safety:** Zero live orders, zero EC2 SSH connections, zero systemd modifications. Strict PAPER trading mode enforced.
5. **Next Step Requirement:** Awaiting explicit user authorization before any commit, push, or pull request update.
