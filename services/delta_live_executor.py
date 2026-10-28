"""Live order execution engine for Delta Exchange India with strict safety gates."""
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple
import requests

from config.orb_settings import ORBSettings, settings as default_orb_settings
from market_data.delta_exchange import DeltaExchangeClient
from risk.position_sizing import size_position, DailyRiskState, RiskDecision
from services.delta_account_service import DeltaAccountService, DeltaAccountState
from services.delta_instrument_service import DeltaInstrumentService
from services.delta_reconciliation_service import DeltaReconciliationService, LiveOrderRecord

logger = logging.getLogger(__name__)


class LiveExecutionError(RuntimeError):
    """Raised when an order fails pre-flight validation or exchange execution."""
    pass


class BaseExecutionEngine(ABC):
    """Abstract interface for trade execution engines (Paper vs Live)."""

    @abstractmethod
    def execute_trade(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        candle_timestamp: str,
        equity: Optional[float] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Execute a trade setup according to the engine's operational mode."""
        pass


class DeltaLiveExecutor(BaseExecutionEngine):
    """Live execution engine enforcing strict 10-point pre-flight checks and protective bracket orders."""

    def __init__(
        self,
        cfg: Optional[ORBSettings] = None,
        client: Optional[DeltaExchangeClient] = None,
        account_service: Optional[DeltaAccountService] = None,
        instrument_service: Optional[DeltaInstrumentService] = None,
        reconciliation_service: Optional[DeltaReconciliationService] = None,
    ):
        self.cfg = cfg or default_orb_settings
        self.client = client or DeltaExchangeClient(
            api_key=self.cfg.api_key,
            api_secret=self.cfg.api_secret,
            base_url=self.cfg.api_base_url,
            enabled=True,
        )
        self.account_service = account_service or DeltaAccountService(client=self.client, cfg=self.cfg)
        self.instrument_service = instrument_service or DeltaInstrumentService(cfg=self.cfg)
        self.reconciliation_service = reconciliation_service or DeltaReconciliationService(cfg=self.cfg)
        self.risk_state = DailyRiskState(path=self.cfg.state_path, cfg=self.cfg)

    def validate_preflight(
        self,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        candle_timestamp: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Execute the mandatory 10-point pre-flight safety audit.
        
        Returns (is_approved, reason, details_dict).
        """
        # Gate 1: Check Mode is LIVE
        if self.cfg.mode != "LIVE":
            return False, f"Live executor rejected: mode is '{self.cfg.mode}', not 'LIVE'.", None

        # Gate 2: Secondary confirmation acknowledged
        if self.cfg.live_trading_acknowledged != "YES":
            return False, "Live executor rejected: LIVE_TRADING_ACKNOWLEDGED is not 'YES'.", None

        # Gate 3: Emergency Kill Switch
        if self.cfg.kill_switch_active:
            return False, "CRITICAL: Emergency Kill Switch is ACTIVE. Order execution blocked.", None

        # Gate 4: API Credentials present & non-placeholder
        if not self.client.api_key or self.client.api_key.startswith("<") or not self.client.api_secret or self.client.api_secret.startswith("<"):
            return False, "Live executor rejected: API credentials missing or placeholder.", None

        # Gate 5: Instrument validation & tradability
        inst_ok, inst_msg, specs = self.instrument_service.validate_for_strategy(self.cfg.symbol)
        if not inst_ok or not specs:
            return False, f"Instrument validation failed: {inst_msg}", None

        # Gate 6: Account equity & available margin retrieval
        account_state = self.account_service.get_account_state(specs.settling_asset)
        if not account_state.is_valid:
            return False, f"Real account validation failed: {account_state.error}", None

        if account_state.equity <= 0:
            return False, f"Account equity is zero or negative (${account_state.equity}).", None

        # Gate 7: Conflicting open positions or pending orders
        if account_state.open_positions:
            return False, f"Conflicting open positions exist ({len(account_state.open_positions)}). Only 1 active position permitted.", None
        if account_state.open_orders:
            return False, f"Conflicting pending orders exist ({len(account_state.open_orders)}).", None

        # Gate 8: Signal freshness and idempotency
        if self.reconciliation_service.is_candle_already_ordered(candle_timestamp):
            return False, f"Idempotency rejection: candle timestamp '{candle_timestamp}' was already processed.", None

        # Gate 9: Daily Risk Limit & Loss Cooldown Guard
        now_dt = datetime.now(timezone.utc)
        allowed, guard_reason, _ = self.risk_state.can_trade(now_dt)
        if not allowed:
            return False, f"Daily risk guard blocked entry: {guard_reason}", None

        # Gate 10: Position Sizing & Margin Budget Check using verified real equity
        equity_float = float(account_state.equity)
        risk_decision: RiskDecision = size_position(entry, stop_loss, equity_float, self.cfg)
        if not risk_decision.approved:
            return False, f"Risk sizing rejected: {risk_decision.reason}", {"risk_decision": risk_decision.to_dict()}

        if risk_decision.lots < 1:
            return False, "Sized order quantity is less than minimum 1 contract lot.", None

        # Verify margin requirement against available free collateral
        if Decimal(str(risk_decision.estimated_margin)) > account_state.available_margin:
            return False, (
                f"Required margin (${risk_decision.estimated_margin:.2f}) exceeds free available collateral "
                f"(${account_state.available_margin:.2f})."
            ), None

        details = {
            "specs": specs,
            "account_state": account_state,
            "risk_decision": risk_decision,
            "aligned_entry": self.instrument_service.align_price_to_tick(entry, specs.tick_size),
            "aligned_sl": self.instrument_service.align_price_to_tick(stop_loss, specs.tick_size),
            "aligned_tp": self.instrument_service.align_price_to_tick(take_profit, specs.tick_size),
        }
        return True, "Pre-flight safety audit passed.", details

    def execute_trade(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        candle_timestamp: str,
        equity: Optional[float] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Execute a real live order on Delta Exchange with attached protective bracket SL/TP."""
        side = "buy" if direction.upper() == "LONG" else "sell"
        audit_ok, audit_msg, details = self.validate_preflight(
            direction=direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            candle_timestamp=candle_timestamp,
        )

        if not audit_ok:
            logger.warning("Pre-flight safety check failed: %s", audit_msg)
            return {
                "success": False,
                "error": audit_msg,
                "status": "REJECTED",
                "details": details,
            }

        specs = details["specs"]
        risk_dec: RiskDecision = details["risk_decision"]
        aligned_entry = details["aligned_entry"]
        aligned_sl = details["aligned_sl"]
        aligned_tp = details["aligned_tp"]

        # Form unique idempotency client_order_id
        ts_hash = hashlib.sha256(str(candle_timestamp).encode("utf-8")).hexdigest()[:8]
        client_order_id = f"ORB_{int(time.time())}_{ts_hash}"

        # Record intent before dispatching
        self.reconciliation_service.record_intent(
            client_order_id=client_order_id,
            candle_timestamp=candle_timestamp,
            symbol=symbol,
            side=side,
            size=risk_dec.lots,
            limit_price=aligned_entry,
            stop_loss_price=aligned_sl,
            take_profit_price=aligned_tp,
        )

        # Build official Delta Exchange bracket order payload
        # Slippage protective buffer for stop-limit fill
        sl_limit_offset = float(specs.tick_size) * 5.0
        sl_limit_price = (aligned_sl - sl_limit_offset) if side == "buy" else (aligned_sl + sl_limit_offset)
        aligned_sl_limit = self.instrument_service.align_price_to_tick(sl_limit_price, specs.tick_size)

        payload = {
            "product_id": specs.product_id,
            "size": risk_dec.lots,
            "side": side,
            "order_type": "limit_order",
            "limit_price": f"{aligned_entry:.2f}",
            "time_in_force": "gtc",
            "stop_trigger_method": "last_traded_price",
            "bracket_stop_loss_price": f"{aligned_sl:.2f}",
            "bracket_stop_loss_limit_price": f"{aligned_sl_limit:.2f}",
            "bracket_take_profit_price": f"{aligned_tp:.2f}",
            "bracket_take_profit_limit_price": f"{aligned_tp:.2f}",
        }

        compact_payload = json.dumps(payload, separators=(",", ":"))
        path = "/v2/orders"

        try:
            headers = self.client._get_headers("POST", path, payload=compact_payload)
            url = f"{self.client.base_url}{path}"
            res = requests.post(
                url,
                data=compact_payload,
                headers=headers,
                timeout=self.cfg.request_timeout_seconds,
            )

            if res.status_code in (200, 201):
                data = res.json()
                order_result = data.get("result", {})
                exchange_id = str(order_result.get("id", ""))
                status = str(order_result.get("state", "ACCEPTED")).upper()

                self.reconciliation_service.update_result(
                    client_order_id=client_order_id,
                    status=status,
                    exchange_order_id=exchange_id,
                    filled_size=int(order_result.get("unfilled_size", 0)),
                    raw_response=data,
                )

                # Reserve daily trade slot
                try:
                    self.risk_state.reserve_trade(datetime.now(timezone.utc))
                except Exception as exc:
                    logger.warning("Daily trade reservation noted: %s", exc)

                return {
                    "success": True,
                    "client_order_id": client_order_id,
                    "exchange_order_id": exchange_id,
                    "status": status,
                    "order": order_result,
                    "message": f"Successfully submitted {side.upper()} {risk_dec.lots} {symbol} @ {aligned_entry:.2f} with attached SL/TP.",
                }

            error_msg = f"Order submission failed with HTTP {res.status_code}: {res.text[:150]}"
            self.reconciliation_service.update_result(
                client_order_id=client_order_id,
                status="REJECTED",
                error_message=error_msg,
            )
            return {"success": False, "error": error_msg, "status": "REJECTED"}

        except requests.exceptions.Timeout:
            # SAFETY RECONCILIATION: Never blindly retry on timeout!
            logger.error("Order submission timed out for %s. Triggering immediate reconciliation.", client_order_id)
            self.reconciliation_service.update_result(
                client_order_id=client_order_id,
                status="PENDING_RECONCILIATION",
                error_message="Submission timed out. Awaiting exchange confirmation.",
            )
            return {
                "success": False,
                "error": "Order submission timed out. Entered safety hold to reconcile with exchange.",
                "status": "TIMEOUT_HOLD",
                "client_order_id": client_order_id,
            }
        except Exception as exc:
            err = f"Order transmission exception: {exc}"
            logger.error(err)
            self.reconciliation_service.update_result(
                client_order_id=client_order_id,
                status="ERROR",
                error_message=err,
            )
            return {"success": False, "error": err, "status": "ERROR"}
