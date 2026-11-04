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
        self.state_file = state_file or (Path(self.cfg.state_path).parent / "paper_account_xautusd.json")
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
            "cash_balance": round(self.cash_balance, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "total_fees": round(self.total_fees, 4),
            "total_slippage": round(self.total_slippage, 4),
            "peak_equity": round(self.peak_equity, 2),
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
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Open a simulated paper position with realistic friction (spread, slippage, fees)."""
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

            # 2. Compute fill price with realistic entry spread and slippage
            # Long enters at Ask (+ half spread + slippage), Short enters at Bid (- half spread - slippage)
            spread_half = (self.cfg.spread_pct_round_trip / 2.0) / 100.0
            slip_half = (self.cfg.slippage_pct_round_trip / 2.0) / 100.0

            if clean_dir == "LONG":
                fill_price = round(entry_price * (1.0 + spread_half + slip_half), 2)
            else:
                fill_price = round(entry_price * (1.0 - spread_half - slip_half), 2)

            contract_qty = round(lots * self.cfg.contract_size_xaut, 6)
            notional = round(fill_price * contract_qty, 2)
            margin_required = round(notional / self.cfg.leverage, 2)
            entry_fee = round(notional * (self.cfg.fee_pct_per_side / 100.0), 4)
            slippage_cost = round(abs(fill_price - entry_price) * contract_qty, 4)

            # 3. Balance verification
            if self.cash_balance < (margin_required + entry_fee):
                return False, f"Insufficient paper cash: required ${margin_required + entry_fee:.2f}, available ${self.cash_balance:.2f}", None

            # Deduct entry fee from cash
            self.cash_balance = round(self.cash_balance - entry_fee, 2)
            self.total_fees += entry_fee
            self.total_slippage += slippage_cost

            trade_id = f"PAPER_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{clean_dir}"
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
                "lots": lots,
                "contract_qty": contract_qty,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "margin": margin_required,
                "entry_fee": entry_fee,
                "exit_fee": 0.0,
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
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Close current open simulated position, calculate final P&L and friction, and record."""
        with self._lock:
            if self.open_position is None:
                return False, "No open simulated position to close.", None

            pos = dict(self.open_position)
            side = pos["direction"]
            contract_qty = pos["contract_qty"]
            entry_price = pos["entry_price"]

            # Compute realistic exit fill with spread and slippage
            spread_half = (self.cfg.spread_pct_round_trip / 2.0) / 100.0
            slip_half = (self.cfg.slippage_pct_round_trip / 2.0) / 100.0

            if side == "LONG":
                # Long exits by selling at Bid
                exit_fill = round(exit_price * (1.0 - spread_half - slip_half), 2)
            else:
                # Short exits by buying at Ask
                exit_fill = round(exit_price * (1.0 + spread_half + slip_half), 2)

            exit_notional = round(exit_fill * contract_qty, 2)
            exit_fee = round(exit_notional * (self.cfg.fee_pct_per_side / 100.0), 4)
            exit_slippage = round(abs(exit_fill - exit_price) * contract_qty, 4)

            # Gross P&L
            if side == "LONG":
                gross_pnl = round((exit_price - pos["signal_entry_price"]) * contract_qty, 2)
            else:
                gross_pnl = round((pos["signal_entry_price"] - exit_price) * contract_qty, 2)

            total_friction = round(pos["entry_fee"] + exit_fee + pos["slippage_cost"] + exit_slippage, 4)
            net_pnl = round(gross_pnl - total_friction, 2)

            # R-Multiple calculation based on initial risk budget
            risk_cash = self.initial_equity * (self.cfg.risk_pct / 100.0)
            r_multiple = round(net_pnl / risk_cash, 4) if risk_cash > 0 else 0.0

            # Update cash balance and stats
            self.cash_balance += round(net_pnl, 2)
            self.realized_pnl += round(net_pnl, 2)
            self.total_fees += exit_fee
            self.total_slippage += exit_slippage

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
                "fees": round(pos["entry_fee"] + exit_fee, 4),
                "slippage": round(pos["slippage_cost"] + exit_slippage, 4),
                "total_friction": total_friction,
                "net_pnl": net_pnl,
                "r_multiple": r_multiple,
                "exit_reason": exit_reason,
                "signal_reason": pos.get("signal_reason", ""),
            }

            self.closed_trades.append(closed_record)
            self.open_position = None
            self._save_state()

            msg = (
                f"Simulated {side} closed via {exit_reason} @ ${exit_fill:.2f} "
                f"[Gross: ${gross_pnl:+.2f}, Friction: ${total_friction:.2f}, Net: ${net_pnl:+.2f} ({r_multiple:+.2f}R)]"
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
            equity = round(self.cash_balance + margin_used + unrealized_pnl, 2)
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
            "cash_balance": round(self.cash_balance, 2),
            "margin_used": margin_used,
            "unrealized_pnl": round(unrealized_pnl, 2),
            "equity": equity,
            "realized_pnl": round(self.realized_pnl, 2),
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
