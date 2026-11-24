"""Live order execution engine for Delta Exchange India with strict safety gates."""
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple, List
import requests

from config.orb_settings import ORBSettings, settings as default_orb_settings
from market_data.delta_exchange import DeltaExchangeClient
from risk.position_sizing import size_position, DailyRiskState, RiskDecision
from services.delta_account_service import DeltaAccountService, DeltaAccountState
from services.delta_instrument_service import DeltaInstrumentService
from services.delta_reconciliation_service import DeltaReconciliationService, LiveOrderRecord
from journal.orb_journal import send_telegram_alert

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
        self._ratcheted_positions: set = set()
        self._position_initial_sl: Dict[str, float] = {}
        self._position_stage: Dict[str, int] = {}
        self._position_start_time: Dict[str, datetime] = {}

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
        # Gate 0: Strict Direction Validation and Price Geometry
        clean_dir = str(direction).strip().upper() if direction else ""
        if clean_dir not in ("LONG", "SHORT"):
            return False, f"Live executor rejected: invalid trade direction '{direction}'. Must be LONG or SHORT.", None

        if clean_dir == "LONG" and not (stop_loss < entry < take_profit):
            return False, f"Invalid LONG price geometry: SL (${stop_loss}) must be below Entry (${entry}) and TP (${take_profit}) above Entry.", None

        if clean_dir == "SHORT" and not (take_profit < entry < stop_loss):
            return False, f"Invalid SHORT price geometry: SL (${stop_loss}) must be above Entry (${entry}) and TP (${take_profit}) below Entry.", None

        # Gate 1: Check Mode is LIVE
        if self.cfg.mode != "LIVE":
            return False, f"Live executor rejected: mode is '{self.cfg.mode}', not 'LIVE'.", None

        # Gate 2: Secondary confirmation acknowledged
        if self.cfg.live_trading_acknowledged != "YES":
            return False, "Live executor rejected: LIVE_TRADING_ACKNOWLEDGED is not 'YES'.", None

        # Gate 3: Emergency Kill Switch
        if self.cfg.kill_switch_active:
            return False, "CRITICAL: Emergency Kill Switch is ACTIVE. Order execution blocked.", None

        # Gate 3.1: Direction Restriction Check
        allowed_dir = getattr(self.cfg, "allowed_trade_directions", "BOTH").upper()
        if allowed_dir in {"SHORT_ONLY", "SHORT"} and clean_dir == "LONG":
            return False, "Live executor rejected: LONG trades are disabled by configuration (ALLOWED_TRADE_DIRECTIONS=SHORT_ONLY).", None
        elif allowed_dir in {"LONG_ONLY", "LONG"} and clean_dir == "SHORT":
            return False, "Live executor rejected: SHORT trades are disabled by configuration (ALLOWED_TRADE_DIRECTIONS=LONG_ONLY).", None

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
        clean_dir = str(direction).strip().upper() if direction else ""
        if clean_dir not in ("LONG", "SHORT"):
            logger.error("Live executor rejected: invalid trade direction '%s'. Must be LONG or SHORT.", direction)
            return {
                "success": False,
                "error": f"Invalid trade direction '{direction}'. Must be LONG or SHORT.",
                "status": "REJECTED",
                "details": None,
            }
        side = "buy" if clean_dir == "LONG" else "sell"
        audit_ok, audit_msg, details = self.validate_preflight(
            direction=clean_dir,
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
        # Omit bracket_stop_loss_limit_price so Stop-Loss triggers as a Stop-Market order,
        # guaranteeing 100% immediate fill without resting limit slippage traps.
        payload = {
            "product_id": specs.product_id,
            "size": risk_dec.lots,
            "side": side,
            "order_type": "limit_order",
            "limit_price": f"{aligned_entry:.2f}",
            "time_in_force": "gtc",
            "stop_trigger_method": "last_traded_price",
            "bracket_stop_loss_price": f"{aligned_sl:.2f}",
            "bracket_take_profit_price": f"{aligned_tp:.2f}",
            "bracket_take_profit_limit_price": f"{aligned_tp:.2f}",
            "client_order_id": client_order_id,
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

                # Send Telegram notification if configured
                try:
                    alert_text = (
                        f"🟢 <b>DELTA LIVE ORDER SUBMITTED</b>\n\n"
                        f"• <b>Symbol:</b> {symbol}\n"
                        f"• <b>Side:</b> {side.upper()}\n"
                        f"• <b>Lots:</b> {risk_dec.lots} (0.001 XAUT/lot)\n"
                        f"• <b>Entry:</b> ${aligned_entry:.2f}\n"
                        f"• <b>Stop Loss:</b> ${aligned_sl:.2f}\n"
                        f"• <b>Take Profit:</b> ${aligned_tp:.2f}\n"
                        f"• <b>Order ID:</b> <code>{client_order_id}</code>\n"
                        f"• <b>Exchange ID:</b> <code>{exchange_id}</code>"
                    )
                    send_telegram_alert(alert_text)
                except Exception as tg_err:
                    logger.warning("Telegram notification failed: %s", tg_err)

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

    def cancel_all_product_orders(self) -> Dict[str, Any]:
        """Emergency kill-switch: cancel all open orders on Delta Exchange and reconcile local state."""
        logger.warning("Executing emergency kill-switch cancel all orders for product %s.", self.cfg.product_id)
        res = self.client.cancel_all_orders(product_id=self.cfg.product_id)
        
        # Mark all local active orders as cancelled
        active_orders = self.reconciliation_service.get_active_orders()
        for ord_rec in active_orders:
            self.reconciliation_service.update_result(
                client_order_id=ord_rec.client_order_id,
                status="CANCELLED_BY_KILL_SWITCH",
                error_message="Order cancelled during emergency kill-switch activation.",
            )

        try:
            send_telegram_alert(
                f"🚨 <b>EMERGENCY KILL-SWITCH ACTIVATED</b>\n\n"
                f"• All open orders on Delta Exchange cancelled.\n"
                f"• Local active orders marked cancelled: {len(active_orders)}"
            )
        except Exception as tg_err:
            logger.warning("Telegram kill-switch notification failed: %s", tg_err)

        return {
            "success": res.get("success", False),
            "exchange_response": res,
            "orders_marked_cancelled": len(active_orders),
        }

    def cancel_stale_orders(self, max_age_minutes: int = 5) -> List[Dict[str, Any]]:
        """Cancel unfilled limit orders older than max_age_minutes to avoid stale entries."""
        now = datetime.now(timezone.utc)
        cancelled = []
        active_orders = self.reconciliation_service.get_active_orders()
        for ord_rec in active_orders:
            if ord_rec.status in ("PENDING", "ACCEPTED") and ord_rec.created_at:
                try:
                    created_dt = datetime.fromisoformat(ord_rec.created_at)
                    age_seconds = (now - created_dt).total_seconds()
                    if age_seconds >= (max_age_minutes * 60):
                        logger.warning(
                            "Order %s is stale (age: %.1f mins >= %d mins). Auto-cancelling.",
                            ord_rec.client_order_id, age_seconds / 60.0, max_age_minutes
                        )
                        if ord_rec.exchange_order_id:
                            self.client.cancel_order(ord_rec.exchange_order_id, self.cfg.product_id)

                        self.reconciliation_service.update_result(
                            client_order_id=ord_rec.client_order_id,
                            status="EXPIRED_UNFILLED",
                            error_message=f"Auto-cancelled: order remained unfilled for >{max_age_minutes} minutes.",
                        )
                        cancelled.append({"client_order_id": ord_rec.client_order_id, "age_minutes": age_seconds / 60.0})
                        try:
                            send_telegram_alert(
                                f"⚠️ <b>STALE ORDER AUTO-CANCELLED</b>\n\n"
                                f"• Order <code>{ord_rec.client_order_id}</code> cancelled.\n"
                                f"• Unfilled after {int(age_seconds / 60)} minutes."
                            )
                        except Exception as tg_err:
                            logger.warning("Telegram stale notification failed: %s", tg_err)
                except Exception as exc:
                    logger.warning("Could not assess age for order %s: %s", ord_rec.client_order_id, exc)
        return cancelled


    def reconcile_order(self, client_order_id: str) -> Optional[LiveOrderRecord]:
        """Reconcile a specific order with the exchange."""
        return self.reconciliation_service.reconcile_with_exchange(client_order_id, self.client)

    def reconcile_all_pending(self) -> List[LiveOrderRecord]:
        """Reconcile all unconfirmed or pending orders with the exchange."""
        return self.reconciliation_service.reconcile_all_pending(self.client)

    def manage_open_positions(self, current_price: float) -> Optional[str]:
        """Audit live position excursion and ratchet stop-loss to Breakeven when reaching threshold R."""
        if not getattr(self.cfg, "enable_multi_stage_exits", True):
            return None

        be_trigger_r = float(getattr(self.cfg, "breakeven_trigger_r", 0.8))

        try:
            pos_res = self.account_service._fetch_open_positions(self.cfg.product_id)
            if not pos_res.get("success"):
                return None

            positions = pos_res.get("result", [])
            if not positions:
                self._ratcheted_positions.clear()
                self._position_initial_sl.clear()
                self._position_stage.clear()
                self._position_start_time.clear()
                return None

            for pos in positions:
                size = int(pos.get("size", 0))
                entry_price = float(pos.get("entry_price", 0))
                if size == 0 or entry_price <= 0:
                    continue

                pos_key = f"{self.cfg.product_id}_{entry_price}_{size}"
                current_stage = self._position_stage.get(pos_key, 0)
                if current_stage >= 4:
                    continue

                if pos_key not in self._position_start_time:
                    self._position_start_time[pos_key] = datetime.now(timezone.utc)
                age_minutes = (datetime.now(timezone.utc) - self._position_start_time[pos_key]).total_seconds() / 60.0

                # Query open protective stop orders on Delta Exchange
                ord_res = self.account_service._fetch_open_orders(self.cfg.product_id)
                if not ord_res.get("success"):
                    continue

                open_orders = ord_res.get("result", [])
                
                # Identify stop order associated with this position
                stop_order = None
                for ord_item in open_orders:
                    is_stop = (
                        ord_item.get("stop_price") is not None
                        or ord_item.get("bracket_stop_loss_price") is not None
                        or ord_item.get("order_type") in ("stop_order", "stop_limit_order")
                    )
                    expected_side = "sell" if size > 0 else "buy"
                    if is_stop and ord_item.get("side") == expected_side:
                        stop_order = ord_item
                        break

                if not stop_order:
                    continue

                stop_id = str(stop_order.get("id"))
                current_sl = float(stop_order.get("stop_price") or stop_order.get("limit_price") or 0)
                if current_sl <= 0:
                    continue

                if pos_key not in self._position_initial_sl:
                    self._position_initial_sl[pos_key] = current_sl
                initial_sl = self._position_initial_sl[pos_key]

                # LONG Position Evaluation
                if size > 0:
                    risk_dist = entry_price - initial_sl
                    if risk_dist <= 0:
                        risk_dist = max(entry_price - current_sl, 1.0)
                    excursion = current_price - entry_price
                    r_achieved = excursion / risk_dist
                    is_stagnant_in_profit = (age_minutes >= 45.0 and r_achieved >= 0.20)

                    target_sl = None
                    target_stage = 0
                    stage_desc = ""

                    if r_achieved >= 2.2 and current_stage < 4:
                        target_sl = round(entry_price + (1.70 * risk_dist), 2)
                        target_stage = 4
                        stage_desc = f"Ratcheted LONG Stop-Loss to Lock +1.70R Profit (${target_sl:.2f})"
                    elif r_achieved >= 1.6 and current_stage < 3:
                        target_sl = round(entry_price + (1.10 * risk_dist), 2)
                        target_stage = 3
                        stage_desc = f"Ratcheted LONG Stop-Loss to Lock +1.10R Profit (${target_sl:.2f})"
                    elif r_achieved >= 1.0 and current_stage < 2:
                        target_sl = round(entry_price + (0.50 * risk_dist), 2)
                        target_stage = 2
                        stage_desc = f"Ratcheted LONG Stop-Loss to Lock +0.50R Profit (${target_sl:.2f})"
                    elif (r_achieved >= be_trigger_r or is_stagnant_in_profit) and current_stage < 1:
                        target_sl = round(entry_price + 0.10, 2)
                        target_stage = 1
                        stage_desc = f"Ratcheted LONG Stop-Loss to Breakeven (${target_sl:.2f})"

                    if target_sl is not None and (target_sl > current_sl + 0.05 or current_stage < target_stage):
                        new_limit = round(target_sl - 0.05, 2)
                        chg_res = self.client.change_order(
                            order_id=stop_id,
                            product_id=self.cfg.product_id,
                            stop_price=target_sl,
                            limit_price=new_limit,
                        )
                        if chg_res.get("success"):
                            self._position_stage[pos_key] = target_stage
                            self._ratcheted_positions.add(pos_key)
                            msg = f"Multi-Stage Exit: {stage_desc} at +{r_achieved:.2f}R excursion!"
                            logger.info(msg)
                            try:
                                send_telegram_alert(
                                    f"🛡️ <b>STOP RATCHET ACTIVATED</b>\n\n"
                                    f"• <b>Symbol:</b> {self.cfg.symbol}\n"
                                    f"• <b>Side:</b> LONG\n"
                                    f"• <b>Entry:</b> ${entry_price:.2f}\n"
                                    f"• <b>New Stop:</b> ${target_sl:.2f}\n"
                                    f"• <b>Excursion:</b> +{r_achieved:.2f}R\n"
                                    f"• <b>Protection Stage:</b> {target_stage}/3"
                                )
                            except Exception:
                                pass
                            return msg

                # SHORT Position Evaluation
                elif size < 0:
                    risk_dist = initial_sl - entry_price
                    if risk_dist <= 0:
                        risk_dist = max(current_sl - entry_price, 1.0)
                    excursion = entry_price - current_price
                    r_achieved = excursion / risk_dist
                    is_stagnant_in_profit = (age_minutes >= 45.0 and r_achieved >= 0.20)

                    target_sl = None
                    target_stage = 0
                    stage_desc = ""

                    if r_achieved >= 2.2 and current_stage < 4:
                        target_sl = round(entry_price - (1.70 * risk_dist), 2)
                        target_stage = 4
                        stage_desc = f"Ratcheted SHORT Stop-Loss to Lock +1.70R Profit (${target_sl:.2f})"
                    elif r_achieved >= 1.6 and current_stage < 3:
                        target_sl = round(entry_price - (1.10 * risk_dist), 2)
                        target_stage = 3
                        stage_desc = f"Ratcheted SHORT Stop-Loss to Lock +1.10R Profit (${target_sl:.2f})"
                    elif r_achieved >= 1.0 and current_stage < 2:
                        target_sl = round(entry_price - (0.50 * risk_dist), 2)
                        target_stage = 2
                        stage_desc = f"Ratcheted SHORT Stop-Loss to Lock +0.50R Profit (${target_sl:.2f})"
                    elif (r_achieved >= be_trigger_r or is_stagnant_in_profit) and current_stage < 1:
                        target_sl = round(entry_price - 0.10, 2)
                        target_stage = 1
                        stage_desc = f"Ratcheted SHORT Stop-Loss to Breakeven (${target_sl:.2f})"

                    if target_sl is not None and (target_sl < current_sl - 0.05 or current_stage < target_stage):
                        new_limit = round(target_sl + 0.05, 2)
                        chg_res = self.client.change_order(
                            order_id=stop_id,
                            product_id=self.cfg.product_id,
                            stop_price=target_sl,
                            limit_price=new_limit,
                        )
                        if chg_res.get("success"):
                            self._position_stage[pos_key] = target_stage
                            self._ratcheted_positions.add(pos_key)
                            msg = f"Multi-Stage Exit: {stage_desc} at +{r_achieved:.2f}R excursion!"
                            logger.info(msg)
                            try:
                                send_telegram_alert(
                                    f"🛡️ <b>STOP RATCHET ACTIVATED</b>\n\n"
                                    f"• <b>Symbol:</b> {self.cfg.symbol}\n"
                                    f"• <b>Side:</b> SHORT\n"
                                    f"• <b>Entry:</b> ${entry_price:.2f}\n"
                                    f"• <b>New Stop:</b> ${target_sl:.2f}\n"
                                    f"• <b>Excursion:</b> +{r_achieved:.2f}R\n"
                                    f"• <b>Protection Stage:</b> {target_stage}/3"
                                )
                            except Exception:
                                pass
                            return msg

        except Exception as exc:
            logger.warning("Error during open position management: %s", exc)

        return None


