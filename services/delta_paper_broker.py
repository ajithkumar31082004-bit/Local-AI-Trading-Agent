"""Isolated simulated Paper Broker for XAUTUSD on Delta Exchange India.

STRICT SAFETY GUARANTEE:
- NEVER PLACES REAL ORDERS.
- NEVER MODIFIES REAL POSITIONS OR WALLET BALANCES.
- TOTALLY DECOUPLED FROM LIVE ORDER ENDPOINTS.
- ENFORCES PERSISTENT ACCOUNT STATE ACROSS APPLICATION RESTARTS.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from config.orb_settings import ORBSettings, settings as default_orb_settings

logger = logging.getLogger(__name__)


class PaperSafetyViolation(RuntimeError):
    """Raised whenever an operation attempts to breach the PAPER trading boundary."""
    pass


class DeltaPaperBroker:
    """Manages simulated paper trading execution, risk sizing, and persistent state for XAUTUSD."""

    def __init__(
        self,
        cfg: Optional[ORBSettings] = None,
        state_file: Optional[Path] = None,
        initial_equity: Optional[float] = None,
    ):
        self.cfg = cfg or default_orb_settings

        # Hard safety verification
        if getattr(self.cfg, "mode", "PAPER") == "LIVE" and not getattr(self.cfg, "live_trading_enabled", False):
            raise PaperSafetyViolation("CRITICAL: Live trading requested in paper broker context.")

        self.initial_equity = float(initial_equity or getattr(self.cfg, "paper_initial_equity", 10000.0))
        self.state_file = Path(state_file) if state_file else (Path(self.cfg.state_path).parent / "paper_account_xautusd.json")
        self._lock = threading.RLock()

        # In-memory account state
        self.cash_balance: float = self.initial_equity
        self.realized_pnl: float = 0.0
        self.total_fees: float = 0.0
        self.total_slippage: float = 0.0
        self.peak_equity: float = self.initial_equity
        self.open_position: Optional[Dict[str, Any]] = None
        self.closed_trades: List[Dict[str, Any]] = []

        # Load persisted state if exists
        self._load_state()

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load persistent paper account state from disk."""
        if not self.state_file.exists():
            self._save_state()
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.cash_balance = float(data.get("cash_balance", self.initial_equity))
            self.realized_pnl = float(data.get("realized_pnl", 0.0))
            self.total_fees = float(data.get("total_fees", 0.0))
            self.total_slippage = float(data.get("total_slippage", 0.0))
            self.peak_equity = float(data.get("peak_equity", self.cash_balance))
            self.open_position = data.get("open_position")
            self.closed_trades = data.get("closed_trades", [])
            logger.info(
                "Loaded persisted paper account state: Balance=$%.2f, Realized P&L=$%.2f, Closed Trades=%d, Open Position=%s",
                self.cash_balance,
                self.realized_pnl,
                len(self.closed_trades),
                "YES" if self.open_position else "NO",
            )
        except Exception as exc:
            logger.warning("Could not read paper account state file (%s); starting fresh: %s", self.state_file, exc)
            self._save_state()

    def _save_state(self) -> None:
        """Atomically persist paper account state to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbol": self.cfg.symbol,
            "initial_equity": self.initial_equity,
            "cash_balance": round(self.cash_balance, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "total_fees": round(self.total_fees, 4),
            "total_slippage": round(self.total_slippage, 4),
            "peak_equity": round(self.peak_equity, 4),
            "open_position": self.open_position,
            "closed_trades": self.closed_trades,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_file.replace(self.state_file)
        except Exception as exc:
            logger.error("Failed to persist paper account state: %s", exc)

    def reset_account(self, starting_equity: Optional[float] = None) -> None:
        """Explicitly reset paper account (only for testing or administrative reset)."""
        with self._lock:
            equity = float(starting_equity or self.initial_equity)
            self.initial_equity = equity
            self.cash_balance = equity
            self.realized_pnl = 0.0
            self.total_fees = 0.0
            self.total_slippage = 0.0
            self.peak_equity = equity
            self.open_position = None
            self.closed_trades = []
            self._save_state()
            logger.info("Paper account reset to $%.2f.", equity)

    # -------------------------------------------------------------------------
    # Hard Safety Guard (Blocks Live Execution)
    # -------------------------------------------------------------------------

    def execute_live_order(self, *args, **kwargs) -> Any:
        """Strictly blocks any live exchange order call."""
        raise PaperSafetyViolation(
            "CRITICAL SECURITY VIOLATION: execute_live_order was invoked on DeltaPaperBroker. "
            "Real order submission is strictly prohibited in PAPER mode."
        )

    # -------------------------------------------------------------------------
    # Fee Scenarios & Cost Modeling
    # -------------------------------------------------------------------------

    def _get_fee_rates(self) -> Tuple[float, float]:
        """Return (entry_fee_pct, exit_fee_pct) based on configured fee_scenario.
        
        Scenarios:
        - MAKER_MAKER: 0.01% entry, 0.01% exit (passive limit orders on both sides)
        - MAKER_TAKER: 0.01% entry, 0.05% exit (limit entry, stop-market / taker exit)
        - TAKER_TAKER: 0.05% entry, 0.05% exit (aggressive market orders on both sides)
        """
        scenario = getattr(self.cfg, "fee_scenario", "MAKER_MAKER").upper()
        maker = getattr(self.cfg, "maker_fee_pct", 0.01)
        taker = getattr(self.cfg, "taker_fee_pct", 0.05)

        if scenario == "MAKER_TAKER":
            return maker, taker
        elif scenario == "TAKER_TAKER":
            return taker, taker
        else:  # MAKER_MAKER or fallback to fee_pct_per_side
            fee = getattr(self.cfg, "fee_pct_per_side", maker)
            return fee, fee

    # -------------------------------------------------------------------------
    # Simulated Order Execution
    # -------------------------------------------------------------------------

    def open_simulated_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        lots: int,
        strategy_name: str,
        signal_reason: str = "",
        candle_timestamp: Optional[str] = None,
        order_type: str = "LIMIT",
        bar_high: Optional[float] = None,
        bar_low: Optional[float] = None,
        fill_ratio: float = 1.0,
        market_regime: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Open a simulated paper position with realistic friction (spread, slippage, fees).
        
        Supports:
        - Maker orders that fill vs. do not fill (based on bar high/low price bounds)
        - Partial fills (via fill_ratio between 0.0 and 1.0)
        - Regime-dependent spread & slippage (wider during volatile breakouts)
        - Configurable fee scenarios and GST
        """
        clean_dir = direction.strip().upper()
        clean_sym = symbol.strip().upper()

        if clean_dir not in {"LONG", "SHORT"}:
            return False, f"Invalid direction: '{clean_dir}'", None
        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            return False, "Invalid price parameters (entry, SL, TP must be positive)", None
        if lots < 1:
            return False, f"Lots must be >= 1, got {lots}", None

        with self._lock:
            # 1. Ensure only 1 open position at a time
            if self.open_position is not None:
                return False, f"Position already open: {self.open_position['direction']} {self.open_position['lots']} lots {self.open_position['symbol']}", None

            # 2. Check Maker Limit order fill condition (orders that do NOT fill)
            order_kind = order_type.strip().upper()
            if order_kind == "LIMIT":
                if clean_dir == "LONG" and bar_low is not None and bar_low > entry_price:
                    msg = f"Maker LIMIT BUY unfilled: bar low (${bar_low:.2f}) > limit entry (${entry_price:.2f})"
                    logger.info(msg)
                    return False, msg, None
                elif clean_dir == "SHORT" and bar_high is not None and bar_high < entry_price:
                    msg = f"Maker LIMIT SELL unfilled: bar high (${bar_high:.2f}) < limit entry (${entry_price:.2f})"
                    logger.info(msg)
                    return False, msg, None

            # 3. Dynamic regime-dependent spread & slippage multiplier
            regime = market_regime or "RANGING"
            if regime == "VOLATILE_BREAKOUT":
                regime_multiplier = 1.5  # 50% wider spread and slippage under high volatility
            elif regime == "UNCERTAIN":
                regime_multiplier = 1.25
            else:
                regime_multiplier = 1.0

            # Compute fill price with realistic entry spread and slippage
            spread_half = ((self.cfg.spread_pct_round_trip / 2.0) * regime_multiplier) / 100.0
            slip_half = ((self.cfg.slippage_pct_round_trip / 2.0) * regime_multiplier) / 100.0

            if clean_dir == "LONG":
                fill_price = round(entry_price * (1.0 + spread_half + slip_half), 2)
            else:
                fill_price = round(entry_price * (1.0 - spread_half - slip_half), 2)

            # 4. Partial fill modeling
            actual_ratio = max(0.01, min(1.0, float(fill_ratio)))
            executed_lots = max(1, int(round(lots * actual_ratio))) if lots > 1 else lots
            contract_qty = round(executed_lots * self.cfg.contract_size_xaut * (actual_ratio if lots == 1 else 1.0), 6)
            notional = round(fill_price * contract_qty, 2)
            margin_required = round(notional / self.cfg.leverage, 2)

            entry_fee_pct, _ = self._get_fee_rates()
            # If explicit taker entry requested, override entry fee rate to taker
            if order_kind == "MARKET" or order_kind == "TAKER":
                entry_fee_pct = getattr(self.cfg, "taker_fee_pct", 0.05)

            entry_fee = round(notional * (entry_fee_pct / 100.0), 4)
            entry_gst = round(entry_fee * (self.cfg.gst_rate_pct / 100.0), 4) if getattr(self.cfg, "apply_gst", False) else 0.0
            slippage_cost = round(abs(fill_price - entry_price) * contract_qty, 4)

            # 5. Balance verification
            entry_outflow = entry_fee + entry_gst
            if self.cash_balance < (margin_required + entry_outflow):
                return False, f"Insufficient paper cash: required ${margin_required + entry_outflow:.2f}, available ${self.cash_balance:.2f}", None

            # Deduct entry fee from cash (4-decimal precision for micro contracts)
            self.cash_balance = round(self.cash_balance - entry_outflow, 4)
            self.total_fees += entry_fee
            self.total_slippage += slippage_cost

            if candle_timestamp:
                try:
                    ts_str = str(candle_timestamp).replace("Z", "+00:00")
                    parsed_dt = datetime.fromisoformat(ts_str)
                    ts_fmt = parsed_dt.strftime("%Y%m%d_%H%M%S")
                except Exception:
                    ts_fmt = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            else:
                ts_fmt = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

            trade_id = f"PAPER_{ts_fmt}_{clean_dir}"
            now_iso = datetime.now(timezone.utc).isoformat()

            pos = {
                "trade_id": trade_id,
                "symbol": clean_sym,
                "strategy_name": strategy_name,
                "direction": clean_dir,
                "entry_time": candle_timestamp or now_iso,
                "entry_price": fill_price,
                "signal_entry_price": entry_price,
                "market_price": fill_price,
                "lots": executed_lots,
                "requested_lots": lots,
                "contract_qty": contract_qty,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "margin": margin_required,
                "order_type": order_kind,
                "fill_ratio": actual_ratio,
                "market_regime": regime,
                "entry_fee": entry_fee,
                "entry_gst": entry_gst,
                "exit_fee": 0.0,
                "exit_gst": 0.0,
                "slippage_cost": slippage_cost,
                "unrealized_pnl": 0.0,
                "signal_reason": signal_reason,
            }

            self.open_position = pos
            self._save_state()

            msg = (
                f"Simulated {clean_dir} filled: {lots} lots ({contract_qty} XAUT) @ ${fill_price:.2f} "
                f"[SL: ${stop_loss:.2f}, TP: ${take_profit:.2f}, Margin: ${margin_required:.2f}, Fee: ${entry_fee:.4f}]"
            )
            logger.info(msg)
            return True, msg, dict(pos)

    def close_simulated_trade(
        self,
        exit_price: float,
        exit_reason: str,
        exit_timestamp: Optional[str] = None,
        order_type: Optional[str] = None,
        market_regime: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Close current open simulated position, calculate final P&L and friction, and record."""
        with self._lock:
            if self.open_position is None:
                return False, "No open simulated position to close.", None

            pos = dict(self.open_position)
            side = pos["direction"]
            contract_qty = pos["contract_qty"]
            entry_price = pos["entry_price"]

            # Compute regime-dependent spread and slippage
            regime = market_regime or pos.get("market_regime") or "RANGING"
            if regime == "VOLATILE_BREAKOUT":
                regime_multiplier = 1.5
            elif regime == "UNCERTAIN":
                regime_multiplier = 1.25
            else:
                regime_multiplier = 1.0

            spread_half = ((self.cfg.spread_pct_round_trip / 2.0) * regime_multiplier) / 100.0
            slip_half = ((self.cfg.slippage_pct_round_trip / 2.0) * regime_multiplier) / 100.0

            if side == "LONG":
                # Long exits by selling at Bid
                exit_fill = round(exit_price * (1.0 - spread_half - slip_half), 2)
            else:
                # Short exits by buying at Ask
                exit_fill = round(exit_price * (1.0 + spread_half + slip_half), 2)

            exit_notional = round(exit_fill * contract_qty, 2)

            # Determine exit fee rate based on order type and scenario
            if order_type:
                resolved_exit_type = order_type.strip().upper()
                exit_fee_pct = getattr(self.cfg, "taker_fee_pct", 0.05) if resolved_exit_type in {"TAKER", "MARKET"} else getattr(self.cfg, "maker_fee_pct", 0.01)
            elif exit_reason.upper() in {"SL", "STOP_LOSS"} and getattr(self.cfg, "fee_scenario", "MAKER_MAKER") != "MAKER_MAKER":
                resolved_exit_type = "TAKER"
                exit_fee_pct = getattr(self.cfg, "taker_fee_pct", 0.05)
            else:
                _, exit_fee_pct = self._get_fee_rates()
                resolved_exit_type = "MAKER" if exit_fee_pct <= getattr(self.cfg, "maker_fee_pct", 0.01) else "TAKER"

            exit_fee = round(exit_notional * (exit_fee_pct / 100.0), 4)
            exit_gst = round(exit_fee * (self.cfg.gst_rate_pct / 100.0), 4) if getattr(self.cfg, "apply_gst", False) else 0.0
            exit_slippage = round(abs(exit_fill - exit_price) * contract_qty, 4)

            # Gross P&L (computed to 4 decimal places for micro-contract precision)
            if side == "LONG":
                gross_pnl = round((exit_price - pos["signal_entry_price"]) * contract_qty, 4)
            else:
                gross_pnl = round((pos["signal_entry_price"] - exit_price) * contract_qty, 4)

            total_fees = round(pos["entry_fee"] + exit_fee, 4)
            gst_amount = round(total_fees * (self.cfg.gst_rate_pct / 100.0), 4)
            deducted_gst = gst_amount if getattr(self.cfg, "apply_gst", False) else 0.0

            total_price_impact = round(pos["slippage_cost"] + exit_slippage, 4)
            spread_cost = round(total_price_impact / 2.0, 4)
            slippage_cost = round(total_price_impact / 2.0, 4)
            total_friction = round(total_fees + deducted_gst + total_price_impact, 4)
            net_pnl = round(gross_pnl - total_friction, 4)

            # Trade-level R-Multiple: Net P&L relative to planned risk on this specific position
            trade_risk_cash = round(abs(pos["entry_price"] - pos["stop_loss"]) * contract_qty, 4)
            trade_r_multiple = round(net_pnl / trade_risk_cash, 2) if trade_risk_cash > 0 else 0.0

            # Portfolio-level R-Multiple: Net P&L relative to full account risk budget
            portfolio_risk_budget = self.initial_equity * (self.cfg.risk_pct / 100.0)
            portfolio_r_multiple = round(net_pnl / portfolio_risk_budget, 4) if portfolio_risk_budget > 0 else 0.0

            # Update cash balance: restore deducted entry costs and apply net_pnl
            entry_deducted = pos["entry_fee"] + pos.get("entry_gst", 0.0)
            self.cash_balance = round(self.cash_balance + net_pnl + entry_deducted, 4)
            self.realized_pnl = round(self.realized_pnl + net_pnl, 4)
            self.total_fees = round(self.total_fees + exit_fee, 4)
            self.total_slippage = round(self.total_slippage + exit_slippage, 4)

            current_eq = self.get_account_summary()["equity"]
            self.peak_equity = max(self.peak_equity, current_eq)

            now_iso = datetime.now(timezone.utc).isoformat()
            closed_record = {
                "trade_id": pos["trade_id"],
                "symbol": pos["symbol"],
                "strategy_name": pos["strategy_name"],
                "direction": side,
                "entry_time": pos["entry_time"],
                "entry_price": entry_price,
                "lots": pos["lots"],
                "contract_qty": contract_qty,
                "stop_loss": pos["stop_loss"],
                "take_profit": pos["take_profit"],
                "exit_time": exit_timestamp or now_iso,
                "exit_price": exit_fill,
                "trigger_price": exit_price,
                "gross_pnl": gross_pnl,
                "fees": total_fees,
                "gst": gst_amount,
                "gst_applied": getattr(self.cfg, "apply_gst", False),
                "fee_scenario": getattr(self.cfg, "fee_scenario", "MAKER_MAKER"),
                "entry_order_type": pos.get("order_type", "LIMIT"),
                "exit_order_type": resolved_exit_type,
                "fill_ratio": pos.get("fill_ratio", 1.0),
                "market_regime": regime,
                "spread_cost": spread_cost,
                "slippage_cost": slippage_cost,
                "slippage": total_price_impact,
                "total_friction": total_friction,
                "net_pnl": net_pnl,
                "r_multiple": trade_r_multiple,
                "trade_r_multiple": trade_r_multiple,
                "portfolio_r_multiple": portfolio_r_multiple,
                "trade_risk_cash": trade_risk_cash,
                "exit_reason": exit_reason,
                "signal_reason": pos.get("signal_reason", ""),
            }

            self.closed_trades.append(closed_record)
            self.open_position = None
            self._save_state()

            msg = (
                f"Simulated {side} closed via {exit_reason} @ ${exit_fill:.2f} "
                f"[Gross: ${gross_pnl:+.4f}, Friction: ${total_friction:.4f}, Net: ${net_pnl:+.4f} ({trade_r_multiple:+.2f}R trade / {portfolio_r_multiple:+.4f}R portfolio)]"
            )
            logger.info(msg)
            return True, msg, closed_record

    # -------------------------------------------------------------------------
    # Position & SL/TP Evaluation
    # -------------------------------------------------------------------------

    def evaluate_open_position_sl_tp(
        self,
        current_price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Check if active open position touched Stop Loss or Take Profit."""
        with self._lock:
            if self.open_position is None:
                return None
            pos = dict(self.open_position)

        side = pos["direction"]
        sl = float(pos["stop_loss"])
        tp = float(pos["take_profit"])

        bar_high = float(high if high is not None else current_price)
        bar_low = float(low if low is not None else current_price)

        hit_sl = (bar_low <= sl) if side == "LONG" else (bar_high >= sl)
        hit_tp = (bar_high >= tp) if side == "LONG" else (bar_low <= tp)

        if hit_sl or hit_tp:
            # Conservative collision rule: if both SL and TP are touched in same bar, SL wins
            if hit_sl:
                exit_price = sl
                reason = "SL"
            else:
                exit_price = tp
                reason = "TP"

            _, _, closed_trade = self.close_simulated_trade(
                exit_price=exit_price,
                exit_reason=reason,
                exit_timestamp=timestamp,
            )
            return closed_trade

        # Otherwise update unrealized P&L
        with self._lock:
            if self.open_position is not None:
                qty = self.open_position["contract_qty"]
                e_price = self.open_position["entry_price"]
                if side == "LONG":
                    unrealized_gross = (current_price - e_price) * qty
                else:
                    unrealized_gross = (e_price - current_price) * qty
                self.open_position["market_price"] = current_price
                self.open_position["unrealized_pnl"] = round(unrealized_gross - pos["entry_fee"], 2)

        return None

    # -------------------------------------------------------------------------
    # Telemetry & Metrics
    # -------------------------------------------------------------------------

    def get_account_summary(self) -> Dict[str, Any]:
        """Return comprehensive simulated account summary and performance metrics."""
        with self._lock:
            pos = dict(self.open_position) if self.open_position else None
            unrealized_pnl = float(pos["unrealized_pnl"]) if pos else 0.0
            margin_used = float(pos["margin"]) if pos else 0.0
            equity = round(self.cash_balance + margin_used + unrealized_pnl, 4)
            trades = list(self.closed_trades)

        total_trades = len(trades)
        wins = [t for t in trades if t["net_pnl"] > 0]
        losses = [t for t in trades if t["net_pnl"] <= 0]
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_gains = sum(t["net_pnl"] for t in wins)
        gross_losses = abs(sum(t["net_pnl"] for t in losses))
        profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else (999.0 if gross_gains > 0 else 0.0)

        # Drawdown calculation
        peak = self.initial_equity
        max_dd_cash = 0.0
        max_dd_pct = 0.0
        running_equity = self.initial_equity

        for t in trades:
            running_equity += t["net_pnl"]
            if running_equity > peak:
                peak = running_equity
            dd_cash = peak - running_equity
            dd_pct = (dd_cash / peak * 100.0) if peak > 0 else 0.0
            max_dd_cash = max(max_dd_cash, dd_cash)
            max_dd_pct = max(max_dd_pct, dd_pct)

        return {
            "mode": "PAPER",
            "symbol": self.cfg.symbol,
            "initial_equity": self.initial_equity,
            "cash_balance": round(self.cash_balance, 4),
            "margin_used": margin_used,
            "unrealized_pnl": round(unrealized_pnl, 4),
            "equity": equity,
            "realized_pnl": round(self.realized_pnl, 4),
            "total_fees": round(self.total_fees, 4),
            "total_slippage": round(self.total_slippage, 4),
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_cash": round(max_dd_cash, 2),
            "has_open_position": pos is not None,
            "open_position": pos,
            "closed_trades": trades,
        }
