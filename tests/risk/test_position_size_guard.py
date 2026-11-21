"""Adversarial Unit Tests for Central Hard Position-Sizing Safety Guard (Phase 3.23).

Tests every boundary condition, malformed type, adversarial input, strategy caller,
and final paper-order boundary rejection.
STRICT PAPER ONLY. MOCKED EXECUTION ONLY. NEVER SENDS REAL ORDERS.
"""

import math
import pytest
from unittest.mock import MagicMock, patch

from risk.position_size_guard import (
    validate_paper_order_lots,
    evaluate_paper_order_lots,
    validate_contract_quantity,
    PositionSizeViolation,
    PositionGuardDecision,
    APPROVED_MAX_PAPER_LOTS,
    APPROVED_MIN_PAPER_LOTS,
    STANDARD_CONTRACT_SIZE_XAUT,
)
from services.delta_paper_broker import DeltaPaperBroker
from services.delta_paper_bot import DeltaPaperBot
from config.orb_settings import ORBSettings
from strategy.orb_ema_volume import Signal
from risk.position_sizing import RiskDecision


# ==============================================================================
# 1. Exhaustive 16-Case Input Table Matrix
# ==============================================================================

@pytest.mark.parametrize(
    "input_quantity,expected_pass,expected_code",
    [
        (1, True, None),
        (2, False, "OVERSIZED_QUANTITY"),
        (10, False, "OVERSIZED_QUANTITY"),
        (100, False, "OVERSIZED_QUANTITY"),
        (484, False, "OVERSIZED_QUANTITY"),  # Historical incident quantity
        (495, False, "OVERSIZED_QUANTITY"),  # Margin calculator ceiling
        (0, False, "NON_POSITIVE_LOTS"),
        (-1, False, "NON_POSITIVE_LOTS"),
        (1.5, False, "FLOAT_QUANTITY_REJECTED"),
        (0.5, False, "FLOAT_QUANTITY_REJECTED"),
        (float("nan"), False, "NAN_QUANTITY_REJECTED"),
        (float("inf"), False, "INF_QUANTITY_REJECTED"),
        (None, False, "MISSING_QUANTITY"),
        (True, False, "BOOLEAN_TYPE_REJECTED"),
        ("1", False, "INVALID_TYPE"),
        ("", False, "INVALID_TYPE"),
    ],
)
def test_16_case_input_matrix(input_quantity, expected_pass, expected_code):
    """Test every row of the mandatory 16-case adversarial input table."""
    decision: PositionGuardDecision = evaluate_paper_order_lots(input_quantity)

    if expected_pass:
        assert decision.approved is True
        assert decision.lots == 1
        assert decision.violation_code is None
        assert validate_paper_order_lots(input_quantity) == 1
    else:
        assert decision.approved is False
        assert decision.lots == 0
        assert decision.violation_code == expected_code
        assert decision.rejection_reason is not None

        with pytest.raises(PositionSizeViolation) as exc_info:
            validate_paper_order_lots(input_quantity)
        assert exc_info.value.violation_code == expected_code


# ==============================================================================
# 2. Strategy Integration & Central Guard Consistency (Scenarios 1 & 2)
# ==============================================================================

STRATEGY_NAMES = [
    "ORB_EMA_VOLUME",
    "TREND_FOLLOWING",
    "VWAP_EMA",
    "BOLLINGER_BANDS_RSI",
    "ATR_COMPRESSION",
    "LIQUIDITY_SWEEP",
    "EMA_CROSSOVER",
    "MACD_MOMENTUM",
    "SUPERTREND",
    "MEAN_REVERSION",
]


@pytest.mark.parametrize("strat_name", STRATEGY_NAMES)
def test_every_strategy_enforces_central_guard_on_submission(tmp_path, strat_name):
    """Scenario 1 & 2: Every strategy must use the same central guard; 1 lot approved, >1 rejected."""
    state_file = tmp_path / f"state_{strat_name}.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # 1. Approved 1-lot attempt passes
    ok_1lot, msg_1lot, pos_1lot = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4100.0,
        stop_loss=4085.0,
        take_profit=4130.0,
        lots=1,
        strategy_name=strat_name,
    )
    assert ok_1lot is True
    assert pos_1lot is not None
    assert pos_1lot["lots"] == 1
    assert pos_1lot["contract_qty"] == 0.001
    broker.close_simulated_trade(exit_price=4130.0, exit_reason="TP")

    # 2. Oversized attempt (e.g. 2 lots) by the same strategy is REJECTED
    ok_over, msg_over, pos_over = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4100.0,
        stop_loss=4085.0,
        take_profit=4130.0,
        lots=2,
        strategy_name=strat_name,
    )
    assert ok_over is False
    assert "Position Size Guard Rejection" in msg_over
    assert pos_over is None
    assert broker.open_position is None


# ==============================================================================
# 3. Sizing Engine Returning 484 Lots (Scenario 3 - Historical Anomaly)
# ==============================================================================

def test_sizing_engine_returning_484_lots_is_rejected_at_final_boundary(tmp_path):
    """Scenario 3: If a sizing engine returns the historical 484 lots, broker rejects it."""
    state_file = tmp_path / "incident_replay.json"
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    broker = DeltaPaperBroker(cfg=cfg, state_file=str(state_file), initial_equity=10000.0)

    # Historical parameters from PAPER_20261105_101000_LONG
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4127.90,
        stop_loss=4113.95,
        take_profit=4155.80,
        lots=484,  # The fatal sizing error
        strategy_name="VWAP_EMA",
    )

    assert ok is False
    assert "Position Size Guard Rejection" in msg
    assert pos is None
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["violation_code"] == "OVERSIZED_QUANTITY"
    assert broker.last_order["requested_lots"] == 484
    assert broker.open_position is None


# ==============================================================================
# 4. Margin Calculator Returning 495 Lots (Scenario 4)
# ==============================================================================

def test_margin_calculator_returning_495_lots_is_rejected(tmp_path):
    """Scenario 4: If a margin formula attempts 495 lots, it must be rejected."""
    state_file = tmp_path / "margin_test.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=495,
        strategy_name="ORB",
    )
    assert ok is False
    assert "Position Size Guard Rejection" in msg
    assert "Requested 495 lots exceeds" in msg
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["violation_code"] == "OVERSIZED_QUANTITY"
    assert broker.open_position is None


# ==============================================================================
# 5. Malicious or Malformed Strategy Quantity (Scenario 5)
# ==============================================================================

@pytest.mark.parametrize("malformed", [
    "'; DROP TABLE trades; --",
    {"lots": 1},
    [1],
    (1,),
    object(),
    -999,
    1.0000001,
])
def test_malicious_or_malformed_quantity_rejected(tmp_path, malformed):
    """Scenario 5: Malformed, injected, or non-scalar quantity types are rejected."""
    broker = DeltaPaperBroker(state_file=str(tmp_path / "malformed.json"), initial_equity=10000.0)

    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD",
        direction="LONG",
        entry_price=4000.0,
        stop_loss=3980.0,
        take_profit=4040.0,
        lots=malformed,
        strategy_name="INJECTION_TEST",
    )
    assert ok is False
    assert pos is None
    assert broker.open_position is None


# ==============================================================================
# 6. Contract Quantity Conversion Validation (Scenario 6)
# ==============================================================================

def test_contract_quantity_validation():
    """Scenario 6: Physical contract quantity must match 0.001 XAUT exactly."""
    # 1. Valid 1 lot = 0.001 XAUT
    assert validate_contract_quantity(0.001, STANDARD_CONTRACT_SIZE_XAUT) == 0.001

    # 2. Oversized 484 lots = 0.484 XAUT -> REJECTED
    with pytest.raises(PositionSizeViolation) as exc_484:
        validate_contract_quantity(0.484, STANDARD_CONTRACT_SIZE_XAUT)
    assert exc_484.value.violation_code == "OVERSIZED_CONTRACT_CONVERSION"

    # 3. Oversized 2 lots = 0.002 XAUT -> REJECTED
    with pytest.raises(PositionSizeViolation) as exc_2:
        validate_contract_quantity(0.002, STANDARD_CONTRACT_SIZE_XAUT)
    assert exc_2.value.violation_code == "OVERSIZED_CONTRACT_CONVERSION"

    # 4. Zero contracts -> REJECTED
    with pytest.raises(PositionSizeViolation) as exc_0:
        validate_contract_quantity(0.0, STANDARD_CONTRACT_SIZE_XAUT)
    assert exc_0.value.violation_code == "OVERSIZED_CONTRACT_CONVERSION"


# ==============================================================================
# 7. Repeated Order Attempts After Rejected Oversized Order (Scenario 7)
# ==============================================================================

def test_repeated_rejected_orders_do_not_corrupt_subsequent_valid_order(tmp_path):
    """Scenario 7: Consecutive rejected oversized orders leave state clean for a valid 1-lot order."""
    broker = DeltaPaperBroker(state_file=str(tmp_path / "repeated.json"), initial_equity=10000.0)

    # Attempt 1: 484 lots -> REJECTED
    ok1, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4100.0, stop_loss=4080.0, take_profit=4140.0, lots=484, strategy_name="TEST"
    )
    assert ok1 is False

    # Attempt 2: 10 lots -> REJECTED
    ok2, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4100.0, stop_loss=4080.0, take_profit=4140.0, lots=10, strategy_name="TEST"
    )
    assert ok2 is False

    # Attempt 3: -5 lots -> REJECTED
    ok3, _, _ = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4100.0, stop_loss=4080.0, take_profit=4140.0, lots=-5, strategy_name="TEST"
    )
    assert ok3 is False

    # Attempt 4: Exactly 1 lot -> APPROVED
    ok4, msg4, pos4 = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4100.0, stop_loss=4080.0, take_profit=4140.0, lots=1, strategy_name="TEST"
    )
    assert ok4 is True
    assert pos4["lots"] == 1
    assert broker.open_position is not None
    assert broker.open_position["lots"] == 1


# ==============================================================================
# 8. State Invariance on Rejected Orders (Scenarios 8 & 9)
# ==============================================================================

def test_rejected_orders_preserve_cash_equity_and_zero_fills(tmp_path):
    """Scenarios 8 & 9: Rejected orders do not change cash, equity, or create fills/trades."""
    state_file = tmp_path / "invariance_state.json"
    broker = DeltaPaperBroker(state_file=str(state_file), initial_equity=10000.0)

    initial_cash = broker.cash_balance
    initial_fees = broker.total_fees
    initial_trades_count = len(broker.closed_trades)

    # Attempt oversized order
    ok, msg, pos = broker.open_simulated_trade(
        symbol="XAUTUSD", direction="LONG", entry_price=4100.0, stop_loss=4080.0, take_profit=4140.0, lots=484, strategy_name="TEST"
    )

    assert ok is False
    assert pos is None
    assert broker.cash_balance == initial_cash
    assert broker.total_fees == initial_fees
    assert len(broker.closed_trades) == initial_trades_count
    assert broker.open_position is None


# ==============================================================================
# 9. Auditable Security Event Production (Scenario 10)
# ==============================================================================

def test_rejected_oversized_order_logs_auditable_security_event(tmp_path, caplog):
    """Scenario 10: Sizing rejection produces an auditable security event in logs and state."""
    broker = DeltaPaperBroker(state_file=str(tmp_path / "audit_log.json"), initial_equity=10000.0)

    with caplog.at_level("ERROR"):
        ok, msg, _ = broker.open_simulated_trade(
            symbol="XAUTUSD", direction="LONG", entry_price=4100.0, stop_loss=4080.0, take_profit=4140.0, lots=484, strategy_name="AUDIT_TEST"
        )

    assert ok is False
    # Verify security tag in error logs
    assert any("[SECURITY_SIZING_VIOLATION]" in record.message for record in caplog.records)
    assert any("[FINAL_BOUNDARY_REJECTION]" in record.message for record in caplog.records)
    assert broker.last_order["status"] == "REJECTED"
    assert broker.last_order["reason"] == "SECURITY_SIZING_VIOLATION"


# ==============================================================================
# 10. Bot-Level Pre-Flight Guard Interception
# ==============================================================================

def test_bot_preflight_blocks_sizing_engine_before_broker(tmp_path):
    """Verify DeltaPaperBot blocks oversized sizing before even invoking the broker."""
    from datetime import time
    from zoneinfo import ZoneInfo
    import pandas as pd

    broker_mock = MagicMock()
    broker_mock.open_position = None
    broker_mock.get_account_summary.return_value = {"equity": 10000.0}

    feed_mock = MagicMock()
    feed_mock.get_readiness.return_value.is_ready = True

    risk_state_mock = MagicMock()
    risk_state_mock.can_trade.return_value = (True, "Allowed", {})

    bot = DeltaPaperBot.__new__(DeltaPaperBot)
    bot.broker = broker_mock
    bot.feed = feed_mock
    bot.risk_state = risk_state_mock
    bot.cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    bot.tz = ZoneInfo("Asia/Kolkata")
    bot.earliest_entry_t = time(0, 0)
    bot.last_entry_t = time(23, 59)
    bot._strategy_type = "VWAP"
    bot._last_rejection_reason = ""

    sig = Signal(
        direction="LONG",
        entry=4127.90,
        sl=4113.95,
        tp=4155.80,
        r_multiple=2.0,
        reason="Bullish test setup",
        checklist={},
        timestamp="2026-11-06T12:00:00Z",
        quality_score=0.85,
    )

    # Mock a malicious sizing engine that returns 484 lots
    with patch("services.delta_paper_bot.evaluate_vwap_ema", return_value=sig), \
         patch("services.delta_paper_bot.size_position") as mock_size:
        mock_size.return_value = RiskDecision(
            approved=True,
            lots=484,  # Malicious/buggy sizing
            max_risk_cash=50.0,
            estimated_loss_at_sl=6.35,
            estimated_fees=1.19,
            estimated_spread_slippage=0.79,
            estimated_margin=40.0,
            reason="Mocked oversized sizing",
        )

        df_mock = pd.DataFrame({"Close": [4127.0] * 50})
        bot._evaluate_strategy(df_mock)

        # Broker open_simulated_trade must NEVER have been called!
        broker_mock.open_simulated_trade.assert_not_called()
        assert "Position size guard rejected" in bot._last_rejection_reason or "Central position size guard" in bot._last_rejection_reason
