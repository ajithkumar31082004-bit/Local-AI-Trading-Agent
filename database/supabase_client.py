"""Supabase client for cloud portfolio persistence and remote telemetry."""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = None

from config.settings import settings
from database.models import TradeRecord, PositionRecord

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Manages cloud synchronization with Supabase."""

    def __init__(
        self,
        url: Optional[str] = None,
        key: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        self.url = url or settings.supabase_url
        self.key = key or settings.supabase_key
        self.enabled = enabled if enabled is not None else settings.supabase_enabled
        self.client: Optional[Client] = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialize the Supabase client if enabled and credentials are present."""
        if not self.enabled or not self.url or not self.key or create_client is None:
            return
        try:
            self.client = create_client(self.url, self.key)
        except Exception as e:
            logger.warning("Could not initialize Supabase client: %s", e)
            self.client = None

    def check_connection(self) -> Dict[str, Any]:
        """Test authentication and connectivity to Supabase."""
        if create_client is None:
            return {"available": False, "error": "supabase-py package is not installed."}
        if not self.url or not self.key:
            return {"available": False, "error": "Supabase URL or Key is missing."}
        if not self.client:
            self._init_client()
        if not self.client:
            return {"available": False, "error": "Failed to create Supabase client."}

        try:
            # Test pinging the account table or auth
            res = self.client.table("account").select("id").limit(1).execute()
            return {"available": True, "error": None, "url": self.url}
        except Exception as e:
            err_str = str(e)
            # If table doesn't exist yet, connection is still valid
            if "relation" in err_str.lower() or "42p01" in err_str.lower() or "does not exist" in err_str.lower():
                return {"available": True, "error": None, "url": self.url, "notice": "Tables not yet initialized in Supabase."}
            return {"available": False, "error": err_str, "url": self.url}

    def sync_account(self, account_data: Dict[str, Any]) -> bool:
        """Upsert current account balances to Supabase."""
        if not self.client:
            return False
        try:
            payload = {
                "id": 1,
                "initial_balance": account_data.get("initial_balance", 100000.0),
                "cash_balance": account_data.get("cash_balance", 100000.0),
                "realized_pnl": account_data.get("realized_pnl", 0.0),
                "total_fees_paid": account_data.get("total_fees_paid", 0.0),
                "peak_equity": account_data.get("peak_equity", 100000.0),
                "updated_at": account_data.get("updated_at", datetime.now().isoformat()),
            }
            self.client.table("account").upsert(payload).execute()
            return True
        except Exception as e:
            logger.debug("Supabase sync_account failed: %s", e)
            return False

    def sync_position(self, pos: PositionRecord) -> bool:
        """Upsert position state to Supabase."""
        if not self.client:
            return False
        try:
            payload = {
                "symbol": pos.symbol.upper(),
                "quantity": pos.quantity,
                "avg_entry_price": pos.avg_entry_price,
                "current_price": pos.current_price,
                "market_value": pos.market_value,
                "unrealized_pnl": pos.unrealized_pnl,
                "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                "realized_pnl": pos.realized_pnl,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "opened_at": pos.opened_at,
                "updated_at": pos.updated_at,
            }
            self.client.table("positions").upsert(payload, on_conflict="symbol").execute()
            return True
        except Exception as e:
            logger.debug("Supabase sync_position failed: %s", e)
            return False

    def sync_trade(self, trade: TradeRecord) -> bool:
        """Insert a newly executed trade into Supabase."""
        if not self.client:
            return False
        try:
            payload = {
                "symbol": trade.symbol.upper(),
                "side": trade.side,
                "quantity": trade.quantity,
                "price": trade.price,
                "fee": trade.fee,
                "slippage": trade.slippage,
                "total_cost": trade.total_cost,
                "timestamp": trade.timestamp,
                "strategy": trade.strategy,
                "status": trade.status,
                "notes": trade.notes or "",
            }
            self.client.table("trades").insert(payload).execute()
            return True
        except Exception as e:
            logger.debug("Supabase sync_trade failed: %s", e)
            return False

    def reset_remote_account(self, starting_balance: float = 100000.0) -> bool:
        """Reset remote tables on account reset."""
        if not self.client:
            return False
        try:
            self.client.table("positions").delete().neq("id", 0).execute()
            self.client.table("trades").delete().neq("id", 0).execute()
            self.sync_account({
                "initial_balance": starting_balance,
                "cash_balance": starting_balance,
                "realized_pnl": 0.0,
                "total_fees_paid": 0.0,
                "peak_equity": starting_balance,
                "updated_at": datetime.now().isoformat(),
            })
            return True
        except Exception as e:
            logger.debug("Supabase reset_remote_account failed: %s", e)
            return False
