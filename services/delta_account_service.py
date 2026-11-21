"""Dedicated authenticated account, equity, and margin service for Delta Exchange India."""
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import requests

from config.orb_settings import ORBSettings, settings as default_orb_settings
from market_data.delta_exchange import DeltaExchangeClient

logger = logging.getLogger(__name__)


def _to_decimal(val: Any, default: str = "0.0") -> Decimal:
    """Safely convert any numeric or string exchange value to Decimal."""
    if val is None:
        return Decimal(default)
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


@dataclass(frozen=True)
class DeltaWalletSnapshot:
    """Detailed verified snapshot of a specific asset wallet."""
    asset_symbol: str
    wallet_balance: Decimal
    available_balance: Decimal  # Free collateral available for new orders
    position_margin: Decimal    # Collateral locked in open positions
    order_margin: Decimal       # Collateral reserved in pending open orders
    unrealized_pnl: Decimal
    total_equity: Decimal       # Net asset equity: balance + unrealized P&L
    timestamp: datetime
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class DeltaAccountState:
    """Verified live account telemetry, margin limits, and open derivative orders."""
    is_valid: bool
    equity: Decimal
    available_margin: Decimal
    used_margin: Decimal
    unrealized_pnl: Decimal
    currency: str
    last_synced_at: Optional[datetime]
    error: Optional[str] = None
    open_positions: List[Dict[str, Any]] = field(default_factory=list)
    open_orders: List[Dict[str, Any]] = field(default_factory=list)
    wallets: Dict[str, DeltaWalletSnapshot] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "equity": float(self.equity),
            "available_margin": float(self.available_margin),
            "used_margin": float(self.used_margin),
            "unrealized_pnl": float(self.unrealized_pnl),
            "currency": self.currency,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "error": self.error,
            "open_positions_count": len(self.open_positions),
            "open_orders_count": len(self.open_orders),
        }


class DeltaAccountService:
    """Service for retrieving and validating real account equity and margin state."""

    def __init__(
        self,
        client: Optional[DeltaExchangeClient] = None,
        cfg: Optional[ORBSettings] = None,
    ):
        self.cfg = cfg or default_orb_settings
        self.client = client or DeltaExchangeClient(
            api_key=self.cfg.api_key,
            api_secret=self.cfg.api_secret,
            base_url=self.cfg.api_base_url,
            enabled=True,
        )

    def get_account_state(self, settling_asset: str = "USD") -> DeltaAccountState:
        """Retrieve verified live equity, margin, and positions.
        
        Never returns simulated defaults or zeros on failure. If authentication
        or retrieval fails, returns an invalid state with a descriptive error.
        """
        now = datetime.now(timezone.utc)

        if not self.client.api_key or not self.client.api_secret:
            return DeltaAccountState(
                is_valid=False,
                equity=Decimal("0"),
                available_margin=Decimal("0"),
                used_margin=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                currency=settling_asset,
                last_synced_at=None,
                error="Delta Exchange API credentials missing or unconfigured.",
            )

        # 1. Fetch wallet balances
        wallets_res = self._fetch_wallet_balances()
        if not wallets_res.get("success"):
            return DeltaAccountState(
                is_valid=False,
                equity=Decimal("0"),
                available_margin=Decimal("0"),
                used_margin=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                currency=settling_asset,
                last_synced_at=None,
                error=wallets_res.get("error", "Failed to retrieve wallet balances."),
            )

        wallet_list = wallets_res.get("result", [])
        snapshots: Dict[str, DeltaWalletSnapshot] = {}

        for item in wallet_list:
            sym = str(item.get("asset_symbol", "")).upper()
            wb = _to_decimal(item.get("balance"))
            ab = _to_decimal(item.get("available_balance"))
            pm = _to_decimal(item.get("position_margin"))
            om = _to_decimal(item.get("order_margin") or item.get("strategy_blocked_amount"))
            upnl = _to_decimal(item.get("unrealized_pnl") or item.get("unrealized_cash_flow"))
            eq = wb + upnl

            snapshots[sym] = DeltaWalletSnapshot(
                asset_symbol=sym,
                wallet_balance=wb,
                available_balance=ab,
                position_margin=pm,
                order_margin=om,
                unrealized_pnl=upnl,
                total_equity=eq,
                timestamp=now,
                raw=item,
            )

        # Match collateral: try settling_asset, fallback to USDT if USD not found
        target_asset = settling_asset.upper()
        if target_asset not in snapshots and target_asset == "USD" and "USDT" in snapshots:
            target_asset = "USDT"

        selected_wallet = snapshots.get(target_asset)
        if not selected_wallet:
            # If no direct match, check if there is an active non-zero balance wallet
            non_zero = [w for w in snapshots.values() if w.wallet_balance > 0]
            if non_zero:
                selected_wallet = non_zero[0]
                target_asset = selected_wallet.asset_symbol
            else:
                return DeltaAccountState(
                    is_valid=False,
                    equity=Decimal("0"),
                    available_margin=Decimal("0"),
                    used_margin=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                    currency=settling_asset,
                    last_synced_at=now,
                    error=f"No wallet balance found for settling asset '{settling_asset}' or 'USDT'.",
                    wallets=snapshots,
                )

        # 2. Fetch open positions for the target product
        pos_res = self._fetch_open_positions(self.cfg.product_id)
        open_positions = pos_res.get("result", []) if pos_res.get("success") else []

        # 3. Fetch pending orders for the target product
        ord_res = self._fetch_open_orders(self.cfg.product_id)
        open_orders = ord_res.get("result", []) if ord_res.get("success") else []

        used_margin = selected_wallet.position_margin + selected_wallet.order_margin

        return DeltaAccountState(
            is_valid=True,
            equity=selected_wallet.total_equity,
            available_margin=selected_wallet.available_balance,
            used_margin=used_margin,
            unrealized_pnl=selected_wallet.unrealized_pnl,
            currency=target_asset,
            last_synced_at=now,
            error=None,
            open_positions=open_positions,
            open_orders=open_orders,
            wallets=snapshots,
        )

    def _fetch_wallet_balances(self) -> Dict[str, Any]:
        path = "/v2/wallet/balances"
        try:
            headers = self.client._get_headers("GET", path)
            url = f"{self.client.base_url}{path}"
            res = requests.get(url, headers=headers, timeout=self.cfg.request_timeout_seconds)
            if res.status_code == 200:
                data = res.json()
                return {"success": True, "result": data.get("result", [])}
            return {
                "success": False,
                "error": f"Wallet query failed with HTTP {res.status_code}: {res.text[:120]}",
            }
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Wallet balance query timed out."}
        except Exception as exc:
            return {"success": False, "error": f"Wallet balance query error: {exc}"}

    def _fetch_open_positions(self, product_id: Optional[int] = None) -> Dict[str, Any]:
        path = "/v2/positions/margined"
        qs = f"?product_ids={product_id}" if product_id else ""
        try:
            headers = self.client._get_headers("GET", path, query_string=qs)
            url = f"{self.client.base_url}{path}{qs}"
            res = requests.get(url, headers=headers, timeout=self.cfg.request_timeout_seconds)
            if res.status_code == 200:
                data = res.json()
                positions = data.get("result", [])
                # Filter for nonzero size if returned as list
                active = [p for p in positions if int(p.get("size", 0)) != 0]
                return {"success": True, "result": active}
            return {"success": False, "error": f"Positions query HTTP {res.status_code}: {res.text[:120]}"}
        except Exception as exc:
            return {"success": False, "error": f"Positions query failed: {exc}"}

    def _fetch_open_orders(self, product_id: Optional[int] = None) -> Dict[str, Any]:
        path = "/v2/orders"
        qs = f"?states=open,pending&product_ids={product_id}" if product_id else "?states=open,pending"
        try:
            headers = self.client._get_headers("GET", path, query_string=qs)
            url = f"{self.client.base_url}{path}{qs}"
            res = requests.get(url, headers=headers, timeout=self.cfg.request_timeout_seconds)
            if res.status_code == 200:
                data = res.json()
                return {"success": True, "result": data.get("result", [])}
            return {"success": False, "error": f"Orders query HTTP {res.status_code}: {res.text[:120]}"}
        except Exception as exc:
            return {"success": False, "error": f"Orders query failed: {exc}"}
