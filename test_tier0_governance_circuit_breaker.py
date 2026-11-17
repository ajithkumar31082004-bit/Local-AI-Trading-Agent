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
def temp_gov_env(tmp_path, monkeypatch):
    """Create isolated directory structure for governance testing."""
    ledger_file = tmp_path / "governance_incidents.json"
    defect_file = tmp_path / "paper_forward_24h_state.json"
    
    # Write valid defect ledger
    valid_content = b'{"defect_ledger_test": true}'
    defect_file.write_bytes(valid_content)
    expected_hash = hashlib.sha256(valid_content).hexdigest()

    # Isolate default class paths so default instantiations use test sandbox
    monkeypatch.setattr(GovernanceSupervisor, "DEFAULT_LEDGER_PATH", ledger_file)
    monkeypatch.setattr(GovernanceSupervisor, "DEFAULT_DEFECT_LEDGER_PATH", defect_file)
    monkeypatch.setattr(GovernanceSupervisor, "EXPECTED_DEFECT_LEDGER_SHA256", expected_hash)

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


# =============================================================================
# TEST 13: DEFAULT PAPER BROKER CONSTRUCTION MANDATES GOVERNANCE SUPERVISOR
# =============================================================================

def test_13_default_paper_broker_construction_mandates_governance_supervisor(temp_gov_env):
    """DeltaPaperBroker created without gov_supervisor automatically constructs and activates one."""
    state_file = temp_gov_env["tmp_path"] / "default_broker_state.json"
    broker = DeltaPaperBroker(state_file=state_file)

    assert broker.gov_supervisor is not None
    assert isinstance(broker.gov_supervisor, GovernanceSupervisor)
    assert broker.gov_supervisor.has_active_breach() is False

    # Submitting live order triggers supervisor containment
    with pytest.raises(PaperSafetyViolation):
        broker.execute_live_order(symbol="XAUTUSD", lots=1, side="BUY")
    assert broker.gov_supervisor.has_active_breach() is True


# =============================================================================
# TEST 14: DEFAULT PAPER BOT CONSTRUCTION MANDATES GOVERNANCE SUPERVISOR
# =============================================================================

def test_14_default_paper_bot_construction_mandates_governance_supervisor(temp_gov_env):
    """DeltaPaperBot created without gov_supervisor automatically constructs one and cascades to broker."""
    bot = DeltaPaperBot()

    assert bot.gov_supervisor is not None
    assert isinstance(bot.gov_supervisor, GovernanceSupervisor)
    assert bot.broker.gov_supervisor is not None
    # Crucial instance consistency: bot and broker must reference the exact same supervisor object
    assert bot.broker.gov_supervisor is bot.gov_supervisor


# =============================================================================
# TEST 15: INJECTED BROKER RECEIVES BOT SUPERVISOR FOR UNIFIED CONTAINMENT
# =============================================================================

def test_15_injected_broker_receives_bot_supervisor(temp_gov_env):
    """Injected broker without supervisor or with different supervisor is synchronized to bot supervisor."""
    sup = temp_gov_env["supervisor"]
    state_file = temp_gov_env["tmp_path"] / "injected_broker_state.json"
    injected_broker = DeltaPaperBroker(state_file=state_file, gov_supervisor=None)

    bot = DeltaPaperBot(broker=injected_broker, gov_supervisor=sup)
    assert bot.gov_supervisor is sup
    assert bot.broker.gov_supervisor is sup
    assert bot.broker.gov_supervisor is bot.gov_supervisor


# =============================================================================
# TEST 16: BROKER AND BOT STARTUP REFUSAL WHEN ACTIVE INCIDENT IN LEDGER
# =============================================================================

def test_16_broker_and_bot_startup_refusal_when_active_incident_in_ledger(temp_gov_env):
    """Broker and Bot constructors and start method refuse initialization if active breach exists."""
    sup: GovernanceSupervisor = temp_gov_env["supervisor"]
    state_file = temp_gov_env["tmp_path"] / "startup_refusal_state.json"

    # Record fatal breach
    sup.record_incident("MANUAL_AUDIT_BREACH", {"reason": "Test active breach"}, severity="FATAL")
    assert sup.has_active_breach() is True

    # 1. DeltaPaperBroker refuses construction
    with pytest.raises(GovernanceContainmentError, match="Runner startup refused"):
        DeltaPaperBroker(state_file=state_file, gov_supervisor=sup)

    # 2. DeltaPaperBot refuses construction
    with pytest.raises(GovernanceContainmentError, match="Runner startup refused"):
        DeltaPaperBot(gov_supervisor=sup)

    # 3. Clean bot startup also refuses if breach occurs before start()
    clean_sup = GovernanceSupervisor(ledger_path=temp_gov_env["tmp_path"] / "clean_ledger.json")
    clean_bot = DeltaPaperBot(gov_supervisor=clean_sup)
    clean_sup.record_incident("POST_INIT_BREACH", {"reason": "Breach after init"}, severity="FATAL")
    with pytest.raises(GovernanceContainmentError, match="Runner startup refused"):
        clean_bot.start()


# =============================================================================
# TEST 17: FAIL CLOSED IF INCIDENT LEDGER IS CORRUPTED AT STARTUP
# =============================================================================

def test_17_fail_closed_if_ledger_corrupted_at_startup(temp_gov_env):
    """Unreadable or corrupted incident ledger forces fail-closed containment and blocks startup."""
    corrupt_ledger = temp_gov_env["tmp_path"] / "corrupt_ledger.json"
    corrupt_ledger.write_text("{ MALFORMED_JSON ::: invalid syntax }", encoding="utf-8")

    corrupt_sup = GovernanceSupervisor(ledger_path=corrupt_ledger)
    assert corrupt_sup.has_active_breach() is True

    # Broker and Bot must refuse startup
    with pytest.raises(GovernanceContainmentError, match="Runner startup refused"):
        DeltaPaperBroker(state_file=temp_gov_env["tmp_path"] / "corrupt_broker_state.json", gov_supervisor=corrupt_sup)

    with pytest.raises(GovernanceContainmentError, match="Runner startup refused"):
        DeltaPaperBot(gov_supervisor=corrupt_sup)


# =============================================================================
# TEST 18: MISSING SUPERVISOR MODULE FAILS CLOSED
# =============================================================================

def test_18_missing_supervisor_module_fails_closed(temp_gov_env, monkeypatch):
    """When GovernanceSupervisor module fails to import, both broker and bot raise PaperSafetyViolation."""
    import services.delta_paper_broker as broker_mod
    import services.delta_paper_bot as bot_mod

    monkeypatch.setattr(broker_mod, "_SUPERVISOR_IMPORT_EXCEPTION", ImportError("Mock module missing"))
    monkeypatch.setattr(bot_mod, "_BOT_SUPERVISOR_IMPORT_EXCEPTION", ImportError("Mock module missing"))

    state_file = temp_gov_env["tmp_path"] / "import_fail_state.json"
    with pytest.raises(PaperSafetyViolation, match="GovernanceSupervisor module failed to import"):
        DeltaPaperBroker(state_file=state_file)

    with pytest.raises(PaperSafetyViolation, match="GovernanceSupervisor module failed to import"):
        DeltaPaperBot()


# =============================================================================
# TEST 19: MISSING SUPERVISOR DEPENDENCY FAILS CLOSED
# =============================================================================

def test_19_missing_supervisor_dependency_fails_closed(temp_gov_env, monkeypatch):
    """When GovernanceSupervisor is None due to broken dependency, broker and bot fail closed."""
    import services.delta_paper_broker as broker_mod
    import services.delta_paper_bot as bot_mod

    monkeypatch.setattr(broker_mod, "GovernanceSupervisor", None)
    monkeypatch.setattr(bot_mod, "GovernanceSupervisor", None)

    state_file = temp_gov_env["tmp_path"] / "dep_fail_state.json"
    with pytest.raises(PaperSafetyViolation, match="GovernanceSupervisor module failed to import"):
        DeltaPaperBroker(state_file=state_file)

    with pytest.raises(PaperSafetyViolation, match="GovernanceSupervisor module failed to import"):
        DeltaPaperBot()


# =============================================================================
# TEST 20: SUPERVISOR CONSTRUCTOR FAILURE FAILS CLOSED
# =============================================================================

def test_20_supervisor_constructor_failure_fails_closed(temp_gov_env, monkeypatch):
    """When GovernanceSupervisor constructor raises an unexpected error, broker and bot fail closed."""
    def broken_init(self, *args, **kwargs):
        raise OSError("Simulated disk I/O permission denied during supervisor init")

    monkeypatch.setattr(GovernanceSupervisor, "__init__", broken_init)

    state_file = temp_gov_env["tmp_path"] / "ctor_fail_state.json"
    with pytest.raises(PaperSafetyViolation, match="Failed to construct GovernanceSupervisor"):
        DeltaPaperBroker(state_file=state_file)

    with pytest.raises(PaperSafetyViolation, match="Failed to construct GovernanceSupervisor"):
        DeltaPaperBot()


# =============================================================================
# TEST 21: MISSING ENFORCE STARTUP METHOD FAILS CLOSED
# =============================================================================

def test_21_missing_enforce_startup_method_fails_closed(temp_gov_env):
    """Injected supervisor missing enforce_startup_governance_safety causes immediate failure."""
    class MockIncompleteSupervisor:
        def has_active_breach(self):
            return False

    incomplete_sup = MockIncompleteSupervisor()
    state_file = temp_gov_env["tmp_path"] / "incomplete_sup_state.json"

    with pytest.raises(PaperSafetyViolation, match="lacks mandatory 'enforce_startup_governance_safety'"):
        DeltaPaperBroker(state_file=state_file, gov_supervisor=incomplete_sup)

    with pytest.raises(PaperSafetyViolation, match="lacks mandatory 'enforce_startup_governance_safety'"):
        DeltaPaperBot(gov_supervisor=incomplete_sup)


# =============================================================================
# TEST 22: INJECTED BROKER WITH NO SUPERVISOR SUPPORT FAILS CLOSED
# =============================================================================

def test_22_injected_broker_with_no_supervisor_support_fails_closed(temp_gov_env):
    """Injecting a broker object that lacks 'gov_supervisor' attribute immediately fails closed."""
    class UnmonitoredBroker:
        pass

    unsafe_broker = UnmonitoredBroker()

    with pytest.raises(PaperSafetyViolation, match="does not support governance monitoring"):
        DeltaPaperBot(broker=unsafe_broker)


# =============================================================================
# TEST 23: SUPERVISOR POST-SYNCHRONIZATION IDENTITY MISMATCH REJECTION (V8)
# =============================================================================

def test_23_supervisor_identity_mismatch_fails_closed(temp_gov_env):
    """When an injected broker rejects or mutates supervisor assignment, bot fails closed."""
    class DefectiveBroker:
        def __init__(self):
            self._sup = GovernanceSupervisor()

        @property
        def gov_supervisor(self):
            # Returns internal supervisor instance regardless of assignment attempts
            return self._sup

        @gov_supervisor.setter
        def gov_supervisor(self, val):
            # Defect: silently ignore or reject synchronization assignment
            pass

    defective_broker = DefectiveBroker()
    bot_sup = GovernanceSupervisor()

    with pytest.raises(PaperSafetyViolation, match="Post-synchronization supervisor identity verification failed"):
        DeltaPaperBot(broker=defective_broker, gov_supervisor=bot_sup)


# =============================================================================
# TEST 24: NON-CALLABLE ENFORCE STARTUP METHOD FAILS CLOSED (V8)
# =============================================================================

def test_24_non_callable_enforce_startup_method_fails_closed(temp_gov_env):
    """Supervisor having non-callable enforce_startup_governance_safety fails closed."""
    class NonCallableSupervisor:
        enforce_startup_governance_safety = "NOT_A_CALLABLE_STRING"
        def has_active_breach(self):
            return False

    bad_sup = NonCallableSupervisor()
    state_file = temp_gov_env["tmp_path"] / "non_callable_sup_state.json"

    with pytest.raises(PaperSafetyViolation, match="method or method is not callable"):
        DeltaPaperBroker(state_file=state_file, gov_supervisor=bad_sup)

    with pytest.raises(PaperSafetyViolation, match="method or method is not callable"):
        DeltaPaperBot(gov_supervisor=bad_sup)


# =============================================================================
# TEST 25: THROWING ENFORCE STARTUP METHOD EXCEPTION PROPAGATION (V8)
# =============================================================================

def test_25_throwing_enforce_startup_exception_propagates(temp_gov_env):
    """Exceptions raised by enforce_startup_governance_safety propagate without being swallowed."""
    class ThrowingSupervisor:
        def enforce_startup_governance_safety(self):
            raise GovernanceContainmentError("Simulated governance breach during startup verification")

    throwing_sup = ThrowingSupervisor()
    state_file = temp_gov_env["tmp_path"] / "throwing_sup_state.json"

    with pytest.raises(GovernanceContainmentError, match="Simulated governance breach during startup verification"):
        DeltaPaperBroker(state_file=state_file, gov_supervisor=throwing_sup)

    with pytest.raises(GovernanceContainmentError, match="Simulated governance breach during startup verification"):
        DeltaPaperBot(gov_supervisor=throwing_sup)

    # Also verify custom runtime exceptions propagate
    class CustomErrorSupervisor:
        def enforce_startup_governance_safety(self):
            raise RuntimeError("Custom governance infrastructure failure")

    custom_sup = CustomErrorSupervisor()
    state_file2 = temp_gov_env["tmp_path"] / "throwing_custom_state.json"

    with pytest.raises(RuntimeError, match="Custom governance infrastructure failure"):
        DeltaPaperBroker(state_file=state_file2, gov_supervisor=custom_sup)

    with pytest.raises(RuntimeError, match="Custom governance infrastructure failure"):
        DeltaPaperBot(gov_supervisor=custom_sup)


# =============================================================================
# TEST 26: CANONICAL IMPORT FAILURE WITHOUT FALLBACK (V8)
# =============================================================================

def test_26_canonical_import_failure_without_fallback(temp_gov_env, monkeypatch):
    """Broker and bot fail closed when canonical services.governance_supervisor fails to import."""
    import services.delta_paper_broker as broker_mod
    import services.delta_paper_bot as bot_mod

    monkeypatch.setattr(broker_mod, "GovernanceSupervisor", None)
    monkeypatch.setattr(broker_mod, "_SUPERVISOR_IMPORT_EXCEPTION", ImportError("No module named 'services.governance_supervisor'"))

    state_file = temp_gov_env["tmp_path"] / "canonical_import_fail_state.json"
    with pytest.raises(PaperSafetyViolation, match="GovernanceSupervisor module failed to import"):
        broker_mod.DeltaPaperBroker(state_file=state_file)

    monkeypatch.setattr(bot_mod, "GovernanceSupervisor", None)
    monkeypatch.setattr(bot_mod, "_BOT_SUPERVISOR_IMPORT_EXCEPTION", ImportError("No module named 'services.governance_supervisor'"))

    with pytest.raises(PaperSafetyViolation, match="GovernanceSupervisor module failed to import"):
        bot_mod.DeltaPaperBot()


# =============================================================================
# TEST 27: STARTUP ATOMICITY AND ROLLBACK ON FEED START FAILURE (V9)
# =============================================================================

def test_27_startup_rollback_on_feed_start_failure(temp_gov_env):
    """When feed.start() raises, callbacks are unregistered, feed is stopped, and bot remains not running."""
    class FailingFeed:
        def __init__(self):
            self._candle_callbacks = []
            self._tick_callbacks = []
            self.stop_called = False

        def register_candle_callback(self, cb):
            self._candle_callbacks.append(cb)

        def unregister_candle_callback(self, cb):
            if cb in self._candle_callbacks:
                self._candle_callbacks.remove(cb)

        def register_tick_callback(self, cb):
            self._tick_callbacks.append(cb)

        def unregister_tick_callback(self, cb):
            if cb in self._tick_callbacks:
                self._tick_callbacks.remove(cb)

        def start(self, bootstrap_lookback=None):
            raise ConnectionError("Simulated WebSocket connection reset during feed start")

        def stop(self):
            self.stop_called = True

        def get_completed_5m_candles(self):
            import pandas as pd
            return pd.DataFrame()

    failing_feed = FailingFeed()
    broker = DeltaPaperBroker(state_file=temp_gov_env["tmp_path"] / "rollback_feed_state.json")
    bot = DeltaPaperBot(feed=failing_feed, broker=broker)

    with pytest.raises(ConnectionError, match="Simulated WebSocket connection reset during feed start"):
        bot.start()

    # Invariants after rollback (Fail-closed lifecycle guarantee)
    assert bot._is_running is False
    assert failing_feed.stop_called is True
    assert len(failing_feed._candle_callbacks) == 0
    assert len(failing_feed._tick_callbacks) == 0


# =============================================================================
# TEST 28: STARTUP ATOMICITY AND ROLLBACK ON CANDLE EVALUATION FAILURE (V8)
# =============================================================================

def test_28_startup_rollback_on_candle_evaluation_failure(temp_gov_env, monkeypatch):
    """When bootstrap candle evaluation fails during startup, feed is stopped, callbacks removed, and bot is not running."""
    import pandas as pd

    class HealthyMockFeed:
        def __init__(self):
            self._candle_callbacks = []
            self._tick_callbacks = []
            self.running = False
            self.stop_called = False

        def register_candle_callback(self, cb):
            self._candle_callbacks.append(cb)

        def unregister_candle_callback(self, cb):
            if cb in self._candle_callbacks:
                self._candle_callbacks.remove(cb)

        def register_tick_callback(self, cb):
            self._tick_callbacks.append(cb)

        def unregister_tick_callback(self, cb):
            if cb in self._tick_callbacks:
                self._tick_callbacks.remove(cb)

        def start(self, bootstrap_lookback=None):
            self.running = True

        def stop(self):
            self.running = False
            self.stop_called = True

        def get_completed_5m_candles(self):
            # Returns non-empty dataframe to trigger _evaluate_strategy
            return pd.DataFrame({"Close": [100.0, 101.0], "High": [102.0, 103.0], "Low": [99.0, 100.0], "Volume": [10, 20]})

    healthy_feed = HealthyMockFeed()
    broker = DeltaPaperBroker(state_file=temp_gov_env["tmp_path"] / "eval_fail_broker_state.json")
    bot = DeltaPaperBot(feed=healthy_feed, broker=broker)

    def broken_eval(df):
        raise ValueError("Simulated corrupt candle schema during initial batch evaluation")

    monkeypatch.setattr(bot, "_evaluate_strategy", broken_eval)

    with pytest.raises(ValueError, match="Simulated corrupt candle schema"):
        bot.start()

    # Invariants after rollback
    assert bot._is_running is False
    assert healthy_feed.stop_called is True
    assert healthy_feed.running is False
    assert len(healthy_feed._candle_callbacks) == 0
    assert len(healthy_feed._tick_callbacks) == 0


# =============================================================================
# TEST 29: PARTIAL CALLBACK REGISTRATION FAILURE ROLLBACK (V9)
# =============================================================================

def test_29_partial_callback_registration_failure_rollback(temp_gov_env):
    """When candle callback registration succeeds but tick registration raises, candle callback is rolled back."""
    class PartialCallbackFeed:
        def __init__(self):
            self._candle_callbacks = []
            self._tick_callbacks = []
            self.start_called = False
            self.stop_called = False

        def register_candle_callback(self, cb):
            self._candle_callbacks.append(cb)

        def unregister_candle_callback(self, cb):
            if cb in self._candle_callbacks:
                self._candle_callbacks.remove(cb)

        def register_tick_callback(self, cb):
            raise RuntimeError("Simulated failure during tick callback handler attachment")

        def unregister_tick_callback(self, cb):
            if cb in self._tick_callbacks:
                self._tick_callbacks.remove(cb)

        def start(self, bootstrap_lookback=None):
            self.start_called = True

        def stop(self):
            self.stop_called = True

    feed = PartialCallbackFeed()
    broker = DeltaPaperBroker(state_file=temp_gov_env["tmp_path"] / "partial_cb_state.json")
    bot = DeltaPaperBot(feed=feed, broker=broker)

    with pytest.raises(RuntimeError, match="Simulated failure during tick callback handler attachment"):
        bot.start()

    # Invariants: candle callback cleanly removed, feed never started, bot not running
    assert bot._is_running is False
    assert len(feed._candle_callbacks) == 0
    assert feed.start_called is False


# =============================================================================
# TEST 30: PARTIALLY INITIALIZED FEED STARTUP FAILURE ROLLBACK (V9)
# =============================================================================

def test_30_partially_initialized_feed_startup_rollback(temp_gov_env):
    """When feed.start() starts internal workers/state before raising, feed.stop() is guaranteed invoked."""
    class PartiallyStartingFeed:
        def __init__(self):
            self._candle_callbacks = []
            self._tick_callbacks = []
            self.internal_worker_running = False
            self.stop_called = False

        def register_candle_callback(self, cb):
            self._candle_callbacks.append(cb)

        def unregister_candle_callback(self, cb):
            if cb in self._candle_callbacks:
                self._candle_callbacks.remove(cb)

        def register_tick_callback(self, cb):
            self._tick_callbacks.append(cb)

        def unregister_tick_callback(self, cb):
            if cb in self._tick_callbacks:
                self._tick_callbacks.remove(cb)

        def start(self, bootstrap_lookback=None):
            # Partially initialize internal worker before failing
            self.internal_worker_running = True
            raise ConnectionResetError("Simulated socket drop during handshake negotiation")

        def stop(self):
            self.stop_called = True
            self.internal_worker_running = False

    feed = PartiallyStartingFeed()
    broker = DeltaPaperBroker(state_file=temp_gov_env["tmp_path"] / "partial_init_feed_state.json")
    bot = DeltaPaperBot(feed=feed, broker=broker)

    with pytest.raises(ConnectionResetError, match="Simulated socket drop during handshake negotiation"):
        bot.start()

    # Invariants: stop called, worker shutdown, callbacks released, bot not running
    assert bot._is_running is False
    assert feed.stop_called is True
    assert feed.internal_worker_running is False
    assert len(feed._candle_callbacks) == 0
    assert len(feed._tick_callbacks) == 0


# =============================================================================
# TEST 31: CLEANUP FAILURE RESILIENCE AND ORIGINAL EXCEPTION PRESERVATION (V9)
# =============================================================================

def test_31_cleanup_failure_resilience_and_exception_preservation(temp_gov_env):
    """When secondary cleanup fails during rollback, original startup exception is strictly preserved."""
    class DefectiveTeardownFeed:
        def __init__(self):
            self._candle_callbacks = []
            self._tick_callbacks = []

        def register_candle_callback(self, cb):
            self._candle_callbacks.append(cb)

        def unregister_candle_callback(self, cb):
            raise OSError("Simulated failure unregistering candle callback during cleanup")

        def register_tick_callback(self, cb):
            self._tick_callbacks.append(cb)

        def unregister_tick_callback(self, cb):
            raise OSError("Simulated failure unregistering tick callback during cleanup")

        def start(self, bootstrap_lookback=None):
            raise TimeoutError("Primary startup failure: Delta Exchange public feed unreachable")

        def stop(self):
            raise RuntimeError("Simulated crash in feed.stop() during emergency rollback")

    feed = DefectiveTeardownFeed()
    broker = DeltaPaperBroker(state_file=temp_gov_env["tmp_path"] / "defective_teardown_state.json")
    bot = DeltaPaperBot(feed=feed, broker=broker)

    # Must preserve primary startup exception (TimeoutError), NOT secondary stop or unregister errors
    with pytest.raises(TimeoutError, match="Primary startup failure: Delta Exchange public feed unreachable"):
        bot.start()

    # Bot must not be running
    assert bot._is_running is False

    # Secondary cleanup errors must be logged in bot audit trail
    cleanup_logs = [log for log in bot._bot_log if "secondary cleanup errors" in log.get("message", "")]
    assert len(cleanup_logs) >= 1

