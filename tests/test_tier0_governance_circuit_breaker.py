"""Adversarial Regression Test Suite for Tier 0 Governance Incident Circuit Breaker.

Tests:
1. Breach at 0 trades and 0 sessions (Zero-Masking Override of WAITING_FOR_SAMPLE)
2. Live-order attempt while Tier 3 is inconclusive (Zero-Masking Override of INCONCLUSIVE_INSUFFICIENT_SAMPLE)
3. Protected artifact checksum mutation detection and immediate containment
4. PR merge or origin/main branch divergence detection and fail-closed containment
5. Incident persistence and durability across runner restart/reinstantiation
6. Restart prevention behavior after a recorded breach (fail-closed startup lock)
7. Failure to write incident record fails closed (disk full / permission error handling)
8. Dual-sample-gate enforcement: >= 100 trades AND >= 100 UTC sessions
9. Remote operational dispatch (SSH / EC2) prevention
10. Clean progression to Tier 5 (VALIDATION_PASSED_PENDING_GOVERNANCE_REVIEW)
"""
import hashlib
import json
import os
from pathlib import Path
import pytest

from services.governance_supervisor import (
    GovernanceSupervisor,
    GovernanceContainmentError,
    GovernanceIncident,
    ValidationState,
)
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from services.delta_paper_bot import DeltaPaperBot
from config.orb_settings import ORBSettings
from services.phase4_7_analytics import (
    calculate_phase4_7_metrics,
    evaluate_statistical_acceptance_gate,
)


@pytest.fixture
def temp_gov_env(tmp_path):
    """Create isolated directory structure for governance testing."""
    ledger_file = tmp_path / "governance_incidents.json"
    defect_file = tmp_path / "paper_forward_24h_state.json"
    
    # Write valid defect ledger
    valid_content = b'{"defect_ledger_test": true}'
    defect_file.write_bytes(valid_content)
    expected_hash = hashlib.sha256(valid_content).hexdigest()

    supervisor = GovernanceSupervisor(
        ledger_path=ledger_file,
        defect_ledger_path=defect_file,
        expected_defect_sha256=expected_hash,
        expected_main_sha="2f348e432ebdd95a429dda3b0635df74b51a506f",
    )
    return {
        "tmp_path": tmp_path,
        "ledger_file": ledger_file,
        "defect_file": defect_file,
        "expected_hash": expected_hash,
        "supervisor": supervisor,
    }


# =============================================================================
# TEST 1: BREACH AT 0 TRADES AND 0 SESSIONS (TIER 0 OVERRIDES WAITING_FOR_SAMPLE)
# =============================================================================

def test_01_breach_at_zero_trades_overrides_waiting_for_sample(temp_gov_env):
    """When trades=0 and sessions=0, a governance breach forces VALIDATION_FAILED (GOVERNANCE_BREACH)."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]
    
    # Baseline: clean governance at 0 trades -> WAITING_FOR_SAMPLE
    empty_metrics = {"total_trades": 0, "unique_sessions_count": 0}
    res_clean = sup.evaluate_five_tier_state(empty_metrics)
    assert res_clean["tier_reached"] == 1
    assert res_clean["state"] == ValidationState.WAITING_FOR_SAMPLE
    assert res_clean["governance_clean"] is True

    # Inject breach (live order attempt)
    with pytest.raises(GovernanceContainmentError, match="Live exchange order attempt detected"):
        sup.check_live_order_safety(broker_mode="LIVE", live_enabled=True)

    # Post-breach: must evaluate to VALIDATION_FAILED (GOVERNANCE_BREACH), NOT WAITING_FOR_SAMPLE
    res_breach = sup.evaluate_five_tier_state(empty_metrics)
    assert res_breach["tier_reached"] == 0
    assert res_breach["state"] == ValidationState.VALIDATION_FAILED_GOVERNANCE_BREACH
    assert res_breach["gate_passed"] is False
    assert res_breach["governance_clean"] is False
    assert "TIER 0 GOVERNANCE BREACH DETECTED" in res_breach["summary_rationale"]
    assert res_breach["active_breaches_count"] >= 1


# =============================================================================
# TEST 2: LIVE-ORDER ATTEMPT WHILE TIER 3 IS INCONCLUSIVE (OVERRIDE OF TIER 3)
# =============================================================================

def test_02_live_order_while_tier3_inconclusive_overrides_state(temp_gov_env):
    """When Tier 3 is inconclusive (sample < 20), a live-order attempt forces GOVERNANCE_BREACH."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]

    # Passing portfolio metrics (100 trades, 100 sessions, WR=60%, PF=1.8, Exp=+0.5R, MDD=2%, Sharpe=1.5)
    passing_metrics = {
        "total_trades": 100,
        "unique_sessions_count": 100,
        "win_rate_pct": 60.0,
        "profit_factor": 1.80,
        "expectancy_r": 0.50,
        "max_drawdown_pct": 2.0,
        "annualized_sharpe": 1.50,
    }
    # Strategy 3 has only 12 trades (< 20)
    strategy_metrics = {
        "ORB_EMA_VOLUME": {"trades": 25},
        "TREND_FOLLOWING": {"trades": 28},
        "ATR_BREAKOUT": {"trades": 12},  # Undersampled!
        "BB_RSI_REVERSION": {"trades": 20},
        "LIQUIDITY_SWEEP": {"trades": 15},  # Undersampled!
    }

    # Clean state -> INCONCLUSIVE_INSUFFICIENT_SAMPLE
    res_inconclusive = sup.evaluate_five_tier_state(passing_metrics, strategy_metrics=strategy_metrics)
    assert res_inconclusive["tier_reached"] == 3
    assert res_inconclusive["state"] == ValidationState.INCONCLUSIVE_INSUFFICIENT_SAMPLE

    # Live trading attempt occurs
    with pytest.raises(GovernanceContainmentError):
        sup.check_live_order_safety(broker_mode="PAPER", live_enabled=True, live_acknowledged="YES")

    # Post-breach: Tier 0 must override Tier 3; state cannot remain INCONCLUSIVE
    res_post_breach = sup.evaluate_five_tier_state(passing_metrics, strategy_metrics=strategy_metrics)
    assert res_post_breach["tier_reached"] == 0
    assert res_post_breach["state"] == ValidationState.VALIDATION_FAILED_GOVERNANCE_BREACH
    assert res_post_breach["gate_passed"] is False


# =============================================================================
# TEST 3: PROTECTED ARTIFACT CHECKSUM MUTATION DETECTION
# =============================================================================

def test_03_artifact_checksum_mutation_detection_and_containment(temp_gov_env):
    """Tampering with a protected artifact triggers immediate ARTIFACT_CHECKSUM_MUTATION breach."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]
    defect_file = temp_gov_env["defect_file"]

    # Baseline verification passes
    assert sup.check_protected_artifact() is True

    # Mutate defect ledger on disk
    defect_file.write_bytes(b'{"defect_ledger_tampered": true}')

    # Verification must fail closed and record breach
    with pytest.raises(GovernanceContainmentError, match="hash mutation"):
        sup.check_protected_artifact()

    assert sup.has_active_breach() is True
    incidents = sup.get_incidents()
    assert any(inc["incident_type"] == "ARTIFACT_CHECKSUM_MUTATION" for inc in incidents)


# =============================================================================
# TEST 4: PR MERGE AND ORIGIN/MAIN DIVERGENCE DETECTION
# =============================================================================

def test_04_git_governance_divergence_detection(temp_gov_env):
    """PR #3 merge or main commit hash change triggers GIT_GOVERNANCE_BREACH."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]

    # 1. Main branch hash diverged
    with pytest.raises(GovernanceContainmentError, match="origin/main diverged"):
        sup.check_git_invariants(current_main_sha="deadbeef1234567890abcdef1234567890abcdef")

    assert sup.has_active_breach() is True
    incidents = sup.get_incidents()
    assert any(inc["incident_type"] == "GIT_GOVERNANCE_BREACH" for inc in incidents)

    # 2. PR #3 merged
    with pytest.raises(GovernanceContainmentError, match="PR #3 merge detected"):
        sup.check_git_invariants(pr_merged=True)


# =============================================================================
# TEST 5: INCIDENT PERSISTENCE ACROSS RE-INSTANTIATION
# =============================================================================

def test_05_incident_persistence_across_reinstantiation(temp_gov_env):
    """Recorded incidents persist durably in JSON ledger and are recovered on supervisor restart."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]
    ledger_file = temp_gov_env["ledger_file"]

    # Record two distinct incidents
    sup.record_incident("LIVE_ORDER_ATTEMPT", {"details": "Order to live endpoint intercepted"})
    sup.record_incident("REMOTE_DISPATCH_BREACH", {"details": "SSH command attempted"})

    assert ledger_file.exists()

    # Create fresh supervisor instance pointing to same ledger file
    fresh_sup = GovernanceSupervisor(
        ledger_path=ledger_file,
        defect_ledger_path=temp_gov_env["defect_file"],
        expected_defect_sha256=temp_gov_env["expected_hash"],
    )

    assert fresh_sup.has_active_breach() is True
    recovered_incidents = fresh_sup.get_incidents()
    assert len(recovered_incidents) == 2
    types = [inc["incident_type"] for inc in recovered_incidents]
    assert "LIVE_ORDER_ATTEMPT" in types
    assert "REMOTE_DISPATCH_BREACH" in types


# =============================================================================
# TEST 6: RESTART BEHAVIOR AFTER RECORDED BREACH
# =============================================================================

def test_06_startup_refused_after_recorded_breach(temp_gov_env):
    """Supervisor startup safety check refuses runner execution when active breaches exist."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]
    ledger_file = temp_gov_env["ledger_file"]

    # Initially clean: startup succeeds
    assert sup.enforce_startup_governance_safety() is True

    # Record a breach
    sup.record_incident("LIVE_ORDER_ATTEMPT", {"details": "Attempted live API call"})

    # Startup now fails closed
    with pytest.raises(GovernanceContainmentError, match="Runner startup refused"):
        sup.enforce_startup_governance_safety()

    # Re-instantiated runner also refuses startup
    restarted_sup = GovernanceSupervisor(
        ledger_path=ledger_file,
        defect_ledger_path=temp_gov_env["defect_file"],
        expected_defect_sha256=temp_gov_env["expected_hash"],
    )
    with pytest.raises(GovernanceContainmentError, match="Runner startup refused"):
        restarted_sup.enforce_startup_governance_safety()


# =============================================================================
# TEST 7: FAILURE TO WRITE INCIDENT RECORD FAILS CLOSED
# =============================================================================

def test_07_failure_to_write_incident_fails_closed(temp_gov_env, monkeypatch):
    """If disk error prevents saving incident ledger, supervisor fails closed with containment error."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]

    # Simulate filesystem write failure (e.g. disk full, permission denied)
    def mock_write_text(*args, **kwargs):
        raise OSError("Mock disk I/O error: disk full")

    monkeypatch.setattr(Path, "write_text", mock_write_text)

    # Must raise GovernanceContainmentError, never silently drop the incident
    with pytest.raises(GovernanceContainmentError, match="Governance incident ledger write failure"):
        sup.record_incident("LIVE_ORDER_ATTEMPT", {"details": "Test I/O error containment"})


# =============================================================================
# TEST 8: DUAL-SAMPLE-GATE ENFORCEMENT (>= 100 TRADES AND >= 100 SESSIONS)
# =============================================================================

def test_08_dual_sample_gate_enforcement():
    """Verify that both >= 100 trades AND >= 100 distinct sessions are required."""
    # 1. 100 trades across only 15 sessions -> FAILS with GATE_INSUFFICIENT_SESSIONS
    trades_few_sessions = []
    for i in range(100):
        trades_few_sessions.append({
            "net_pnl": 20.0,
            "gross_pnl": 21.0,
            "r_multiple": 1.5,
            "exit_time": f"2026-01-{(i % 15) + 1:02d}T12:00:00Z",
        })
    metrics_few_sessions = calculate_phase4_7_metrics(trades_few_sessions)
    gate_few_sessions = evaluate_statistical_acceptance_gate(metrics_few_sessions, min_trades=100, min_sessions=100)
    assert gate_few_sessions["gate_passed"] is False
    assert gate_few_sessions["status"] == "INSUFFICIENT_EVIDENCE"
    assert gate_few_sessions["gate_code"] == "GATE_INSUFFICIENT_SESSIONS"
    assert gate_few_sessions["criteria_breakdown"]["sample_size_check"]["passed"] is True
    assert gate_few_sessions["criteria_breakdown"]["session_count_check"]["passed"] is False

    # 2. 50 trades across 50 sessions -> FAILS with GATE_INSUFFICIENT_SAMPLE_SIZE
    trades_few_trades = []
    for i in range(50):
        trades_few_trades.append({
            "net_pnl": 20.0,
            "gross_pnl": 21.0,
            "r_multiple": 1.5,
            "exit_time": f"2026-{(i // 25) + 1:02d}-{(i % 25) + 1:02d}T12:00:00Z",
        })
    metrics_few_trades = calculate_phase4_7_metrics(trades_few_trades)
    gate_few_trades = evaluate_statistical_acceptance_gate(metrics_few_trades, min_trades=100, min_sessions=100)
    assert gate_few_trades["gate_passed"] is False
    assert gate_few_trades["status"] == "INSUFFICIENT_EVIDENCE"
    assert gate_few_trades["gate_code"] == "GATE_INSUFFICIENT_SAMPLE_SIZE"
    assert gate_few_trades["criteria_breakdown"]["sample_size_check"]["passed"] is False
    assert gate_few_trades["criteria_breakdown"]["session_count_check"]["passed"] is False

    # 3. 100 trades across 100 distinct sessions with passing KPIs -> PASSES
    passing_trades = []
    for i in range(100):
        is_win = (i % 10) < 6  # 60% win rate
        pnl = 30.0 if is_win else -15.0
        r_mult = 2.0 if is_win else -1.0
        m = (i // 25) + 1
        d = (i % 25) + 1
        passing_trades.append({
            "net_pnl": pnl,
            "gross_pnl": pnl + 1.0,
            "r_multiple": r_mult,
            "exit_time": f"2026-{m:02d}-{d:02d}T12:00:00Z",
        })
    metrics_pass = calculate_phase4_7_metrics(passing_trades, initial_equity=10000.0)
    gate_pass = evaluate_statistical_acceptance_gate(metrics_pass, min_trades=100, min_sessions=100)
    assert gate_pass["gate_passed"] is True
    assert gate_pass["status"] == "ACCEPTED"
    assert gate_pass["gate_code"] == "GATE_PASSED"
    assert gate_pass["criteria_breakdown"]["sample_size_check"]["passed"] is True
    assert gate_pass["criteria_breakdown"]["session_count_check"]["passed"] is True


# =============================================================================
# TEST 9: REMOTE DISPATCH INTERCEPTION
# =============================================================================

def test_09_remote_dispatch_interception(temp_gov_env):
    """Remote SSH or EC2 commands trigger containment and REMOTE_DISPATCH_BREACH incident."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]

    with pytest.raises(GovernanceContainmentError, match="Remote operational command execution forbidden"):
        sup.check_remote_dispatch_safety("ssh -i key.pem ubuntu@172.31.4.192")

    incidents = sup.get_incidents()
    assert any(inc["incident_type"] == "REMOTE_DISPATCH_BREACH" for inc in incidents)


# =============================================================================
# TEST 10: CLEAN PROGRESSION TO TIER 5 (GOVERNANCE REVIEW)
# =============================================================================

def test_10_clean_progression_to_tier_5(temp_gov_env):
    """When all 5 tiers pass with clean governance, returns VALIDATION_PASSED_PENDING_GOVERNANCE_REVIEW."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]

    passing_metrics = {
        "total_trades": 100,
        "unique_sessions_count": 100,
        "win_rate_pct": 58.0,
        "profit_factor": 1.65,
        "expectancy_r": 0.42,
        "max_drawdown_pct": 2.10,
        "annualized_sharpe": 1.45,
    }
    strategy_metrics = {
        "ORB_EMA_VOLUME": {"trades": 22},
        "TREND_FOLLOWING": {"trades": 24},
        "ATR_BREAKOUT": {"trades": 20},
        "BB_RSI_REVERSION": {"trades": 21},
        "LIQUIDITY_SWEEP": {"trades": 20},
    }

    res = sup.evaluate_five_tier_state(
        metrics=passing_metrics,
        strategy_metrics=strategy_metrics,
        bootstrap_ci_lower_r=0.08,
        holm_bonferroni_passed=True,
        min_trades=100,
        min_sessions=100,
        min_strategy_trades=20,
    )

    assert res["tier_reached"] == 5
    assert res["state"] == ValidationState.VALIDATION_PASSED_PENDING_GOVERNANCE_REVIEW
    assert res["gate_passed"] is True
    assert res["governance_clean"] is True


# =============================================================================
# TEST 11: REAL PAPER BROKER DISPATCH INTEGRATION BLOCKS ON BREACH
# =============================================================================

def test_11_real_paper_broker_dispatch_blocks_on_breach(temp_gov_env):
    """DeltaPaperBroker order-dispatch path integrates with GovernanceSupervisor and fails closed."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]
    state_file = temp_gov_env["tmp_path"] / "paper_broker_test_state.json"

    broker = DeltaPaperBroker(
        state_file=state_file,
        idempotency_db_path=temp_gov_env["tmp_path"] / "test_idemp.db",
        gov_supervisor=sup,
    )

    # 1. Calling execute_live_order directly on broker must record incident in supervisor and raise PaperSafetyViolation
    with pytest.raises(PaperSafetyViolation, match="Tier 0 containment engaged"):
        broker.execute_live_order(symbol="XAUTUSD", lots=1, side="BUY")

    assert sup.has_active_breach() is True
    incidents = sup.get_incidents()
    assert any(inc["incident_type"] == "LIVE_ORDER_ATTEMPT" for inc in incidents)

    # 2. Open simulated trade on broker must now fail closed due to active governance containment
    success, reason, order_dict = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=1,
        strategy_name="ORB_EMA_VOLUME",
    )
    assert success is False
    assert "GOVERNANCE_CONTAINMENT" in reason
    assert order_dict is None


# =============================================================================
# TEST 12: REAL PAPER BOT INIT LIVE MODE TRIGGERS GOVERNANCE BREACH
# =============================================================================

def test_12_real_paper_bot_init_live_mode_triggers_breach(temp_gov_env):
    """DeltaPaperBot initialization with live mode records incident in supervisor and halts."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]

    live_cfg = ORBSettings(mode="LIVE", live_trading_enabled=True)

    with pytest.raises(PaperSafetyViolation, match="requires mode='PAPER'"):
        DeltaPaperBot(cfg=live_cfg, gov_supervisor=sup)

    assert sup.has_active_breach() is True
    incidents = sup.get_incidents()
    assert any(inc["incident_type"] == "LIVE_ORDER_ATTEMPT" for inc in incidents)

