"""Delta Exchange Risk Management Module for XAUUSD / XAUTUSD derivatives.

Handles contract specifications (1 Lot = 0.001 XAUT), INR-denominated risk calculations,
tiny-account constraints (Rs 50 - 100 with up to 100x leverage), hard blocking rules,
and crash-safe SQLite persistence for daily trades and realized P&L.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

from config.orb_settings import ORBSettings, settings as default_orb_settings
from config.settings import settings as app_settings

logger = logging.getLogger(__name__)

CONTRACT_SIZE_XAUT = 0.001  # 1 Lot = 0.001 XAUT


@dataclass
class DeltaRiskDecision:
    """Result of evaluating an ORB setup against Delta Exchange risk constraints."""
    approved: bool
    lots: int
    loss_usd: float
    loss_inr: float
    fees_usd: float
    fees_inr: float
    spread_slippage_usd: float
    spread_slippage_inr: float
    margin_required_usd: float
    margin_required_inr: float
    max_risk_allowed_inr: float
    available_margin_inr: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeltaDailyRiskStore:
    """Thread-safe and persistent SQLite store for daily trade counts and realized P&L in INR."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or app_settings.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self):
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS delta_daily_risk (
                    date TEXT PRIMARY KEY,
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    realized_pnl_inr REAL NOT NULL DEFAULT 0.0,
                    realized_loss_inr REAL NOT NULL DEFAULT 0.0,
                    last_loss_at TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def get_day_stats(self, date_str: str) -> Dict[str, Any]:
        with self._lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT date, trade_count, realized_pnl_inr, realized_loss_inr, last_loss_at FROM delta_daily_risk WHERE date = ?",
                (date_str,),
            ).fetchone()
            if row:
                return dict(row)
            return {
                "date": date_str,
                "trade_count": 0,
                "realized_pnl_inr": 0.0,
                "realized_loss_inr": 0.0,
                "last_loss_at": None,
            }

    def record_trade_taken(self, date_str: str):
        with self._lock, self._get_connection() as conn:
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT INTO delta_daily_risk (date, trade_count, realized_pnl_inr, realized_loss_inr, updated_at)
                VALUES (?, 1, 0.0, 0.0, ?)
                ON CONFLICT(date) DO UPDATE SET
                    trade_count = trade_count + 1,
                    updated_at = excluded.updated_at
            """, (date_str, now_str))
            conn.commit()

    def record_trade_result(self, date_str: str, pnl_inr: float, is_new_trade: bool = False):
        """Record trade result. If is_new_trade is True, also increments trade_count."""
        with self._lock, self._get_connection() as conn:
            now_str = datetime.now(timezone.utc).isoformat()
            loss_inr = abs(pnl_inr) if pnl_inr < 0 else 0.0
            last_loss_at = now_str if pnl_inr < 0 else None
            add_count = 1 if is_new_trade else 0

            conn.execute("""
                INSERT INTO delta_daily_risk (date, trade_count, realized_pnl_inr, realized_loss_inr, last_loss_at, updated_at)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    trade_count = trade_count + ?,
                    realized_pnl_inr = realized_pnl_inr + excluded.realized_pnl_inr,
                    realized_loss_inr = realized_loss_inr + excluded.realized_loss_inr,
                    last_loss_at = COALESCE(excluded.last_loss_at, last_loss_at),
                    updated_at = excluded.updated_at
            """, (date_str, pnl_inr, loss_inr, last_loss_at, now_str, add_count))
            conn.commit()

    def reset_day(self, date_str: str):
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM delta_daily_risk WHERE date = ?", (date_str,))
            conn.commit()


def compute_lot_risk_and_costs(
    entry: float,
    sl: float,
    lots: int = 1,
    usd_inr_rate: float = 86.5,
    leverage: float = 100.0,
    fee_pct_per_side: float = 0.01,
    spread_pct_round_trip: float = 0.02,
    slippage_pct_round_trip: float = 0.02,
) -> Dict[str, float]:
    """Calculate USD and INR loss, fees, and margin for the given lot size."""
    notional_usd = entry * CONTRACT_SIZE_XAUT * lots
    gross_loss_usd = abs(entry - sl) * CONTRACT_SIZE_XAUT * lots
    round_trip_fee_usd = notional_usd * 2.0 * (fee_pct_per_side / 100.0)
    spread_slip_usd = notional_usd * (spread_pct_round_trip + slippage_pct_round_trip) / 100.0
    total_loss_usd = gross_loss_usd + round_trip_fee_usd + spread_slip_usd

    margin_usd = (notional_usd / leverage) if leverage > 0 else notional_usd

    return {
        "gross_loss_usd": round(gross_loss_usd, 5),
        "gross_loss_inr": round(gross_loss_usd * usd_inr_rate, 2),
        "fees_usd": round(round_trip_fee_usd, 5),
        "fees_inr": round(round_trip_fee_usd * usd_inr_rate, 2),
        "spread_slippage_usd": round(spread_slip_usd, 5),
        "spread_slippage_inr": round(spread_slip_usd * usd_inr_rate, 2),
        "total_loss_usd": round(total_loss_usd, 5),
        "total_loss_inr": round(total_loss_usd * usd_inr_rate, 2),
        "margin_required_usd": round(margin_usd, 5),
        "margin_required_inr": round(margin_usd * usd_inr_rate, 2),
    }


def evaluate_delta_trade(
    entry: float,
    sl: float,
    account_equity_inr: float,
    available_margin_inr: float,
    max_risk_per_trade_inr: float,
    daily_loss_limit_inr: float,
    usd_inr_rate: float = 86.5,
    leverage: float = 100.0,
    current_time: Optional[datetime] = None,
    timezone_str: str = "Asia/Kolkata",
    store: Optional[DeltaDailyRiskStore] = None,
    cfg: Optional[ORBSettings] = None,
) -> DeltaRiskDecision:
    """Evaluate trade proposal against strict tiny-account risk limits.
    
    Blocks the trade if:
    1. 1 trade already taken today.
    2. Daily loss limit in INR is reached.
    3. Minimum 1-lot loss at SL exceeds max risk budget in INR.
    4. Required margin for 1 lot exceeds available margin.
    """
    fee_pct = cfg.fee_pct_per_side if cfg else 0.01
    spread_pct = cfg.spread_pct_round_trip if cfg else 0.02
    slip_pct = cfg.slippage_pct_round_trip if cfg else 0.02

    def _block(reason: str, loss_costs: Optional[Dict[str, float]] = None) -> DeltaRiskDecision:
        lc = loss_costs or {}
        return DeltaRiskDecision(
            approved=False,
            lots=0,
            loss_usd=lc.get("total_loss_usd", 0.0),
            loss_inr=lc.get("total_loss_inr", 0.0),
            fees_usd=lc.get("fees_usd", 0.0),
            fees_inr=lc.get("fees_inr", 0.0),
            spread_slippage_usd=lc.get("spread_slippage_usd", 0.0),
            spread_slippage_inr=lc.get("spread_slippage_inr", 0.0),
            margin_required_usd=lc.get("margin_required_usd", 0.0),
            margin_required_inr=lc.get("margin_required_inr", 0.0),
            max_risk_allowed_inr=round(max_risk_per_trade_inr, 2),
            available_margin_inr=round(available_margin_inr, 2),
            reason=f"NO TRADE: {reason}",
        )

    if entry <= 0 or sl <= 0 or abs(entry - sl) < 1e-4:
        return _block("Invalid entry or stop-loss price provided.")

    if account_equity_inr <= 0 or available_margin_inr <= 0:
        return _block(f"Account has zero or negative available balance (equity: ₹{account_equity_inr:.2f}, margin: ₹{available_margin_inr:.2f}).")

    # 1. Check daily trade limits via persistent SQLite store
    dt = current_time or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    today_date = dt.astimezone(ZoneInfo(timezone_str)).date().isoformat()

    risk_store = store or DeltaDailyRiskStore()
    day_stats = risk_store.get_day_stats(today_date)

    if day_stats["trade_count"] >= 1:
        return _block("Maximum 1 trade per day limit already reached.")

    # 2. Check daily loss limit in fixed INR amount
    if day_stats["realized_loss_inr"] >= daily_loss_limit_inr:
        return _block(
            f"Daily loss limit reached (today's realized loss ₹{day_stats['realized_loss_inr']:.2f} >= limit ₹{daily_loss_limit_inr:.2f})."
        )

    # 3. Compute loss and required margin for 1 minimum lot (0.001 XAUT)
    min_lot_costs = compute_lot_risk_and_costs(
        entry=entry,
        sl=sl,
        lots=1,
        usd_inr_rate=usd_inr_rate,
        leverage=leverage,
        fee_pct_per_side=fee_pct,
        spread_pct_round_trip=spread_pct,
        slippage_pct_round_trip=slip_pct,
    )

    # Check minimum lot loss against risk budget in INR
    if min_lot_costs["total_loss_inr"] > max_risk_per_trade_inr:
        return _block(
            f"Minimum 1-lot loss at SL (₹{min_lot_costs['total_loss_inr']:.2f}) exceeds max risk budget (₹{max_risk_per_trade_inr:.2f}).",
            loss_costs=min_lot_costs,
        )

    # 4. Check required margin against available margin
    if min_lot_costs["margin_required_inr"] > available_margin_inr:
        return _block(
            f"Minimum 1-lot required margin (₹{min_lot_costs['margin_required_inr']:.2f}) exceeds available margin (₹{available_margin_inr:.2f}).",
            loss_costs=min_lot_costs,
        )

    # Sizing: determine max integer lots that fit both risk budget and margin
    max_risk_lots = int(max_risk_per_trade_inr // max(min_lot_costs["total_loss_inr"], 1e-4))
    max_margin_lots = int(available_margin_inr // max(min_lot_costs["margin_required_inr"], 1e-4))
    lots = min(max_risk_lots, max_margin_lots)
    lots = max(1, lots)

    final_costs = compute_lot_risk_and_costs(
        entry=entry,
        sl=sl,
        lots=lots,
        usd_inr_rate=usd_inr_rate,
        leverage=leverage,
        fee_pct_per_side=fee_pct,
        spread_pct_round_trip=spread_pct,
        slippage_pct_round_trip=slip_pct,
    )

    return DeltaRiskDecision(
        approved=True,
        lots=lots,
        loss_usd=final_costs["total_loss_usd"],
        loss_inr=final_costs["total_loss_inr"],
        fees_usd=final_costs["fees_usd"],
        fees_inr=final_costs["fees_inr"],
        spread_slippage_usd=final_costs["spread_slippage_usd"],
        spread_slippage_inr=final_costs["spread_slippage_inr"],
        margin_required_usd=final_costs["margin_required_usd"],
        margin_required_inr=final_costs["margin_required_inr"],
        max_risk_allowed_inr=round(max_risk_per_trade_inr, 2),
        available_margin_inr=round(available_margin_inr, 2),
        reason=f"Risk checks passed: Sized {lots} lot(s) (Risk: ₹{final_costs['total_loss_inr']:.2f} / ₹{max_risk_per_trade_inr:.2f}, Margin: ₹{final_costs['margin_required_inr']:.2f} / ₹{available_margin_inr:.2f}).",
    )
