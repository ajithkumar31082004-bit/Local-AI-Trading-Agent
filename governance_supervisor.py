"""Tier 0 Governance Incident Supervisor & Non-Suppressible Circuit Breaker.

Provides orthogonal, continuous governance monitoring across all validation phases:
- Live Exchange Order Detection & Interception
- Protected Artifact Cryptographic Invariance Monitoring
- Git Repository Governance (PR #3 Unmerged, Frozen main)
- Remote Command / EC2 Operational Dispatch Prevention
- Immutable, Durable Incident Persistence (data/governance_incidents.json)
- Immediate Fail-Closed Operational Containment
- Restart Prevention upon Recorded Breach
- Zero-Masking Five-Tier State Precedence Resolution
"""
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


class GovernanceContainmentError(RuntimeError):
    """Raised when an immutable governance invariant is breached and operational containment is engaged."""
    pass


@dataclass
class GovernanceIncident:
    incident_id: str
    incident_type: str  # LIVE_ORDER_ATTEMPT, ARTIFACT_CHECKSUM_MUTATION, GIT_GOVERNANCE_BREACH, REMOTE_DISPATCH_BREACH
    severity: str       # CRITICAL, FATAL
    timestamp: str      # UTC ISO-8601
    details: Dict[str, Any]
    contained: bool = True
    active: bool = True


class ValidationState:
    WAITING_FOR_SAMPLE = "WAITING_FOR_SAMPLE"
    AUDIT_ELIGIBLE = "AUDIT_ELIGIBLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATION_FAILED_GOVERNANCE_BREACH = "VALIDATION_FAILED (GOVERNANCE_BREACH)"
    INCONCLUSIVE_INSUFFICIENT_SAMPLE = "INCONCLUSIVE_INSUFFICIENT_SAMPLE"
    VALIDATION_PASSED_PENDING_GOVERNANCE_REVIEW = "VALIDATION_PASSED_PENDING_GOVERNANCE_REVIEW"


class GovernanceSupervisor:
    """Orthogonal Continuous Governance Incident Supervisor & Circuit Breaker."""

    DEFAULT_LEDGER_PATH = Path("data/governance_incidents.json")
    DEFAULT_DEFECT_LEDGER_PATH = Path("data/paper_forward_24h_state.json")
    EXPECTED_DEFECT_LEDGER_SHA256 = "dad10844b0e77ed19db058eec1e541aa858eb8e2d3ea1ada66d83a69390ced25"
    EXPECTED_FROZEN_MAIN_SHA = "2f348e432ebdd95a429dda3b0635df74b51a506f"
    EXPECTED_PR_BRANCH = "feature/phase4-1-2-ci-build-dependency-fix"

    def __init__(
        self,
        ledger_path: Optional[Union[str, Path]] = None,
        defect_ledger_path: Optional[Union[str, Path]] = None,
        expected_defect_sha256: str = EXPECTED_DEFECT_LEDGER_SHA256,
        expected_main_sha: str = EXPECTED_FROZEN_MAIN_SHA,
    ):
        self.ledger_path = Path(ledger_path) if ledger_path is not None else self.DEFAULT_LEDGER_PATH
        self.defect_ledger_path = Path(defect_ledger_path) if defect_ledger_path is not None else self.DEFAULT_DEFECT_LEDGER_PATH
        self.expected_defect_sha256 = expected_defect_sha256.lower()
        self.expected_main_sha = expected_main_sha.lower()
        self._lock = threading.RLock()
        self._contained: bool = False
        self._in_memory_incidents: List[GovernanceIncident] = []
        self._load_ledger()

    def _load_ledger(self) -> None:
        """Load persisted incidents from disk."""
        with self._lock:
            if not self.ledger_path.exists():
                return
            try:
                data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
                incidents_data = data.get("incidents", [])
                self._in_memory_incidents = [
                    GovernanceIncident(**inc) for inc in incidents_data
                ]
                if any(inc.active for inc in self._in_memory_incidents):
                    self._contained = True
            except Exception as e:
                logger.error(f"Failed to parse governance ledger at {self.ledger_path}: {e}")
                # Fail closed: unreadable ledger must be treated as potential breach
                self._contained = True

    def _save_ledger(self) -> None:
        """Atomically persist incidents to disk with fail-closed error handling."""
        with self._lock:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": "1.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_incidents": len(self._in_memory_incidents),
                "active_breaches": sum(1 for inc in self._in_memory_incidents if inc.active),
                "incidents": [asdict(inc) for inc in self._in_memory_incidents],
            }
            content = json.dumps(payload, indent=2)
            
            # Atomic file write using temporary file in same directory
            temp_file = self.ledger_path.parent / f".tmp_{self.ledger_path.name}_{os.getpid()}_{len(self._in_memory_incidents)}"
            try:
                temp_file.write_text(content, encoding="utf-8")
                shutil.move(str(temp_file), str(self.ledger_path))
            except Exception as e:
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
                logger.critical(f"FATAL: Failed to write governance incident ledger: {e}")
                raise GovernanceContainmentError(
                    f"FATAL: Governance incident ledger write failure: {e}. Failing closed to maintain audit integrity."
                ) from e

    def has_active_breach(self) -> bool:
        """Check if any active governance incident exists."""
        with self._lock:
            return self._contained or any(inc.active for inc in self._in_memory_incidents)

    def get_incidents(self) -> List[Dict[str, Any]]:
        """Return all recorded incidents."""
        with self._lock:
            return [asdict(inc) for inc in self._in_memory_incidents]

    def record_incident(
        self,
        incident_type: str,
        details: Dict[str, Any],
        severity: str = "CRITICAL",
    ) -> GovernanceIncident:
        """Record an immutable governance incident and engage fail-closed containment."""
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            incident_id = f"GOV_INC_{int(datetime.now(timezone.utc).timestamp())}_{len(self._in_memory_incidents) + 1}"
            incident = GovernanceIncident(
                incident_id=incident_id,
                incident_type=incident_type,
                severity=severity,
                timestamp=now_iso,
                details=details,
                contained=True,
                active=True,
            )
            self._in_memory_incidents.append(incident)
            self._contained = True
            
            # Persist immediately; if persistence fails, _save_ledger raises GovernanceContainmentError
            self._save_ledger()
            
            logger.critical(
                f"GOVERNANCE BREACH RECORDED [{incident_id}]: type={incident_type}, severity={severity}. "
                f"Immediate operational containment engaged."
            )
            return incident

    def check_live_order_safety(
        self,
        broker_mode: str = "PAPER",
        live_enabled: bool = False,
        live_acknowledged: Optional[str] = None,
        destination_url: Optional[str] = None,
    ) -> bool:
        """Intercept and prevent live exchange order attempts."""
        mode_upper = str(broker_mode).upper()
        is_live_mode = mode_upper == "LIVE"
        is_live_ack = str(live_acknowledged).upper() == "YES"
        
        is_live_url = False
        if destination_url:
            u = destination_url.lower()
            if "api.delta.exchange" in u and "cdn" not in u and "testnet" not in u:
                is_live_url = True

        if is_live_mode or live_enabled or is_live_ack or is_live_url:
            details = {
                "broker_mode": broker_mode,
                "live_enabled": live_enabled,
                "live_acknowledged": live_acknowledged,
                "destination_url": destination_url,
                "violation_reason": "Live trading execution or configuration detected in paper forward validation environment.",
            }
            self.record_incident("LIVE_ORDER_ATTEMPT", details=details, severity="FATAL")
            raise GovernanceContainmentError(
                "CRITICAL GOVERNANCE BREACH: Live exchange order attempt detected. "
                "Operational containment engaged. Paper trading runner terminated."
            )
        return True

    def check_protected_artifact(
        self,
        artifact_path: Optional[Union[str, Path]] = None,
        expected_sha256: Optional[str] = None,
    ) -> bool:
        """Verify bit-for-bit cryptographic invariance of protected artifacts."""
        target_path = Path(artifact_path) if artifact_path is not None else self.defect_ledger_path
        expected_hash = (expected_sha256 or self.expected_defect_sha256).lower()

        if not target_path.exists():
            details = {
                "artifact_path": str(target_path),
                "expected_sha256": expected_hash,
                "violation_reason": "Protected artifact missing from filesystem.",
            }
            self.record_incident("ARTIFACT_CHECKSUM_MUTATION", details=details, severity="CRITICAL")
            raise GovernanceContainmentError(
                f"CRITICAL GOVERNANCE BREACH: Protected artifact missing: {target_path}."
            )

        hasher = hashlib.sha256()
        with open(target_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        actual_hash = hasher.hexdigest().lower()

        if actual_hash != expected_hash:
            details = {
                "artifact_path": str(target_path),
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
                "violation_reason": "Protected artifact SHA-256 hash mutation detected.",
            }
            self.record_incident("ARTIFACT_CHECKSUM_MUTATION", details=details, severity="FATAL")
            raise GovernanceContainmentError(
                f"CRITICAL GOVERNANCE BREACH: Protected artifact {target_path.name} hash mutation! "
                f"Expected {expected_hash}, got {actual_hash}."
            )
        return True

    def check_git_invariants(
        self,
        current_main_sha: Optional[str] = None,
        pr_merged: bool = False,
        expected_main_sha: Optional[str] = None,
    ) -> bool:
        """Verify Git repository freeze invariants (PR #3 open, main commit invariant)."""
        target_expected_sha = (expected_main_sha or self.expected_main_sha).lower()
        
        if pr_merged:
            details = {
                "violation_reason": "PR #3 has been merged into main prior to Phase 4.8 authorization.",
            }
            self.record_incident("GIT_GOVERNANCE_BREACH", details=details, severity="FATAL")
            raise GovernanceContainmentError(
                "CRITICAL GOVERNANCE BREACH: PR #3 merge detected. Merge is strictly forbidden during Phase 4.7."
            )

        if current_main_sha is not None:
            actual_sha = current_main_sha.strip().lower()
            if actual_sha != target_expected_sha:
                details = {
                    "expected_main_sha": target_expected_sha,
                    "actual_main_sha": actual_sha,
                    "violation_reason": "origin/main branch commit hash diverged from frozen milestone commit.",
                }
                self.record_incident("GIT_GOVERNANCE_BREACH", details=details, severity="FATAL")
                raise GovernanceContainmentError(
                    f"CRITICAL GOVERNANCE BREACH: origin/main diverged! Expected {target_expected_sha}, got {actual_sha}."
                )
        return True

    def check_remote_dispatch_safety(self, command_str: Optional[str] = None) -> bool:
        """Verify that no remote EC2 or SSH operational commands are executed."""
        if command_str:
            cmd = command_str.lower()
            prohibited_tokens = ["ssh ", "aws ec2", "gcloud compute", "paramiko"]
            for token in prohibited_tokens:
                if token in cmd:
                    details = {
                        "command": command_str,
                        "token": token,
                        "violation_reason": f"Prohibited remote command execution detected ({token}).",
                    }
                    self.record_incident("REMOTE_DISPATCH_BREACH", details=details, severity="CRITICAL")
                    raise GovernanceContainmentError(
                        f"CRITICAL GOVERNANCE BREACH: Remote operational command execution forbidden ({token})."
                    )
        return True

    def enforce_startup_governance_safety(self) -> bool:
        """Enforce fail-closed startup: refuse initialization if any recorded breach exists."""
        with self._lock:
            if self.has_active_breach():
                active_types = [inc.incident_type for inc in self._in_memory_incidents if inc.active]
                raise GovernanceContainmentError(
                    f"CRITICAL: Runner startup refused. Unresolved governance breaches in ledger: {active_types}. "
                    "Fail-closed containment prevents execution after a recorded violation."
                )
            return True

    def evaluate_five_tier_state(
        self,
        metrics: Dict[str, Any],
        strategy_metrics: Optional[Dict[str, Dict[str, Any]]] = None,
        bootstrap_ci_lower_r: Optional[float] = None,
        holm_bonferroni_passed: Optional[bool] = None,
        min_trades: int = 100,
        min_sessions: int = 100,
        min_strategy_trades: int = 20,
    ) -> Dict[str, Any]:
        """Evaluate Phase 4.7 forward validation outcome according to the five-tier precedence hierarchy.
        
        Hierarchy Order:
        Tier 0 (Continuous Governance Supervisor — Absolute Dominance)
               │
               ▼ (Only if Tier 0 is CLEAN)
        Tier 1 (Sample Eligibility)
               │
               ▼ (Only if Tier 1 Passes)
        Tier 2 (Portfolio KPIs & Dual Expectancy)
               │
               ▼ (Only if Tier 2 Passes)
        Tier 3 (Strategy Sufficiency Filter)
               │
               ▼ (Only if Tier 3 Passes)
        Tier 4 (Multiple Testing FWER Control)
               │
               ▼ (Only if Tier 4 Passes)
        Tier 5 (Governance Review Milestone)
        """
        # =====================================================================
        # TIER 0: CONTINUOUS GOVERNANCE SUPERVISION (ZERO-MASKING INVARIANT)
        # =====================================================================
        if self.has_active_breach():
            incidents = self.get_incidents()
            active_breaches = [inc for inc in incidents if inc.get("active", True)]
            return {
                "tier_reached": 0,
                "state": ValidationState.VALIDATION_FAILED_GOVERNANCE_BREACH,
                "gate_passed": False,
                "governance_clean": False,
                "active_breaches_count": len(active_breaches),
                "summary_rationale": (
                    f"TIER 0 GOVERNANCE BREACH DETECTED: {len(active_breaches)} active governance breach(es) recorded. "
                    "Operational containment engaged. Governance violations override all statistical states "
                    "and can never be masked by WAITING_FOR_SAMPLE or INCONCLUSIVE_INSUFFICIENT_SAMPLE."
                ),
                "incidents": active_breaches,
            }

        # =====================================================================
        # TIER 1: SAMPLE ELIGIBILITY CHECK (TRADES AND SESSIONS)
        # =====================================================================
        total_trades = metrics.get("total_trades", 0)
        unique_sessions = metrics.get("unique_sessions_count", 0)
        trades_pass = total_trades >= min_trades
        sessions_pass = unique_sessions >= min_sessions

        if not trades_pass or not sessions_pass:
            reasons = []
            if not trades_pass:
                reasons.append(f"{total_trades} closed trades recorded (minimum {min_trades} required)")
            if not sessions_pass:
                reasons.append(f"{unique_sessions} distinct sessions recorded (minimum {min_sessions} required)")
            return {
                "tier_reached": 1,
                "state": ValidationState.WAITING_FOR_SAMPLE,
                "gate_passed": False,
                "governance_clean": True,
                "sample_size": total_trades,
                "required_sample_size": min_trades,
                "sessions_count": unique_sessions,
                "required_sessions": min_sessions,
                "summary_rationale": (
                    f"WAITING_FOR_SAMPLE: Sample thresholds not satisfied ({'; '.join(reasons)}). "
                    "Passive collection continues; milestone audit submission remains blocked."
                ),
            }

        # =====================================================================
        # TIER 2: PORTFOLIO KPIS AND DUAL EXPECTANCY GATES
        # =====================================================================
        wr = float(metrics.get("win_rate_pct", 0.0))
        pf = float(metrics.get("profit_factor", 0.0))
        exp_r = float(metrics.get("expectancy_r", 0.0))
        mdd = float(metrics.get("max_drawdown_pct", 0.0))
        sharpe = float(metrics.get("annualized_sharpe", 0.0))

        wr_pass = wr >= 50.0
        pf_pass = pf >= 1.30 or math.isinf(pf)
        exp_pass = exp_r > 0.20
        mdd_pass = mdd <= 3.50
        sharpe_pass = sharpe >= 1.20
        bootstrap_pass = (bootstrap_ci_lower_r is None) or (bootstrap_ci_lower_r > 0.0)

        portfolio_kpis_pass = all([wr_pass, pf_pass, exp_pass, mdd_pass, sharpe_pass, bootstrap_pass])

        if not portfolio_kpis_pass:
            failed_kpis = []
            if not wr_pass:
                failed_kpis.append(f"Win Rate {wr:.2f}% < 50.0%")
            if not pf_pass:
                failed_kpis.append(f"Profit Factor {pf:.2f} < 1.30")
            if not exp_pass:
                failed_kpis.append(f"Expectancy {exp_r:+.2f}R <= +0.20R")
            if not mdd_pass:
                failed_kpis.append(f"Max Drawdown {mdd:.2f}% > 3.50%")
            if not sharpe_pass:
                failed_kpis.append(f"Sharpe {sharpe:.2f} < 1.20")
            if not bootstrap_pass:
                failed_kpis.append(f"Bootstrap CI Lower Bound {bootstrap_ci_lower_r:+.4f}R <= 0.00R")
            return {
                "tier_reached": 2,
                "state": ValidationState.VALIDATION_FAILED,
                "gate_passed": False,
                "governance_clean": True,
                "failed_kpis": failed_kpis,
                "summary_rationale": (
                    f"VALIDATION_FAILED: Portfolio quantitative acceptance gates failed: {'; '.join(failed_kpis)}. "
                    "Precedence dominates strategy sufficiency."
                ),
            }

        # =====================================================================
        # TIER 3: STRATEGY-LEVEL SAMPLE SUFFICIENCY FILTER
        # =====================================================================
        if strategy_metrics:
            undersampled = []
            for s_name, s_data in strategy_metrics.items():
                s_trades = s_data.get("trades", 0)
                if s_trades < min_strategy_trades:
                    undersampled.append(f"{s_name} ({s_trades} trades < {min_strategy_trades})")
            if undersampled:
                return {
                    "tier_reached": 3,
                    "state": ValidationState.INCONCLUSIVE_INSUFFICIENT_SAMPLE,
                    "gate_passed": False,
                    "governance_clean": True,
                    "undersampled_strategies": undersampled,
                    "summary_rationale": (
                        f"INCONCLUSIVE_INSUFFICIENT_SAMPLE: Portfolio KPIs pass, but individual strategies lack required "
                        f"sample depth: {'; '.join(undersampled)}. Strategies remain uncertified for live deployment."
                    ),
                }

        # =====================================================================
        # TIER 4: MULTIPLE TESTING CONTROL (STEP-DOWN HOLM-BONFERRONI)
        # =====================================================================
        if holm_bonferroni_passed is False:
            return {
                "tier_reached": 4,
                "state": ValidationState.VALIDATION_FAILED,
                "gate_passed": False,
                "governance_clean": True,
                "summary_rationale": (
                    "VALIDATION_FAILED: Step-down Holm-Bonferroni hypothesis test failed across strategy family. "
                    "Family-wise error rate protection rejects statistical alpha claim."
                ),
            }

        # =====================================================================
        # TIER 5: GOVERNANCE REVIEW MILESTONE REACHED
        # =====================================================================
        return {
            "tier_reached": 5,
            "state": ValidationState.VALIDATION_PASSED_PENDING_GOVERNANCE_REVIEW,
            "gate_passed": True,
            "governance_clean": True,
            "summary_rationale": (
                "VALIDATION_PASSED_PENDING_GOVERNANCE_REVIEW: All five portfolio KPIs, dual expectancy gates, "
                "strategy sample depths, Holm-Bonferroni hypothesis tests, and governance invariants pass without breach. "
                "System qualifies for formal Phase 4.8 independent human governance review."
            ),
        }
