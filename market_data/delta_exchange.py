"""Delta Exchange API integration for market data and wallet telemetry."""

import time
import hmac
import hashlib
import logging
from typing import Optional, Dict, Any, List
import requests

from config.settings import settings

logger = logging.getLogger(__name__)


class DeltaExchangeClient:
    """Client for Delta Exchange REST API with HMAC-SHA256 signature authentication."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        self.api_key = api_key or settings.delta_exchange_api_key
        self.api_secret = api_secret or settings.delta_exchange_api_secret
        self.base_url = (base_url or settings.delta_exchange_base_url).rstrip("/")
        self.enabled = enabled if enabled is not None else settings.delta_exchange_enabled

    def _generate_signature(self, method: str, path: str, timestamp: str, payload: str = "") -> str:
        """Create HMAC-SHA256 signature required by Delta Exchange."""
        message = method.upper() + timestamp + path + payload
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _get_headers(self, method: str, path: str, payload: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LocalAITradingAgent/1.0",
        }
        if self.api_key and self.api_secret:
            headers["api-key"] = self.api_key
            headers["timestamp"] = timestamp
            headers["signature"] = self._generate_signature(method, path, timestamp, payload)
        return headers

    def check_connection(self) -> Dict[str, Any]:
        """Test public and authenticated connectivity to Delta Exchange."""
        if not self.enabled:
            return {"available": False, "authenticated": False, "error": "Delta Exchange integration is disabled in settings."}

        # 1. Test public endpoint (e.g. /v2/products)
        try:
            pub_res = requests.get(f"{self.base_url}/v2/products", timeout=5)
            if pub_res.status_code != 200:
                # Try fallback global domain if india fails
                alt_url = "https://api.delta.exchange" if "india" in self.base_url else "https://india.delta.exchange"
                try:
                    alt_res = requests.get(f"{alt_url}/v2/products", timeout=5)
                    if alt_res.status_code == 200:
                        self.base_url = alt_url
                except Exception as alt_err:  # noqa: BLE001
                    logger.debug("Delta Exchange fallback URL also unreachable: %s", alt_err)
        except Exception as e:
            return {"available": False, "authenticated": False, "error": f"Cannot reach Delta Exchange at {self.base_url}: {e}"}

        # 2. Test authenticated endpoint if keys present
        if not self.api_key or not self.api_secret:
            return {"available": True, "authenticated": False, "error": "API Key or Secret missing."}

        path = "/v2/wallet/balances"
        headers = self._get_headers("GET", path)
        try:
            auth_res = requests.get(f"{self.base_url}{path}", headers=headers, timeout=5)
            if auth_res.status_code == 200:
                data = auth_res.json()
                return {
                    "available": True,
                    "authenticated": True,
                    "base_url": self.base_url,
                    "balances": data.get("result", []),
                    "error": None,
                }
            return {
                "available": True,
                "authenticated": False,
                "base_url": self.base_url,
                "error": f"Auth failed with HTTP {auth_res.status_code}: {auth_res.text[:150]}",
            }
        except Exception as e:
            return {"available": True, "authenticated": False, "base_url": self.base_url, "error": str(e)}

    def get_wallet_balances(self) -> List[Dict[str, Any]]:
        """Retrieve real wallet asset balances (read-only)."""
        if not self.api_key or not self.api_secret:
            return []
        path = "/v2/wallet/balances"
        headers = self._get_headers("GET", path)
        try:
            res = requests.get(f"{self.base_url}{path}", headers=headers, timeout=5)
            if res.status_code == 200:
                return res.json().get("result", [])
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("Error fetching Delta wallet balances: %s", e)
            return []

    def get_ticker(self, symbol: str = "BTCUSD") -> Optional[Dict[str, Any]]:
        """Fetch real-time ticker data for a crypto product."""
        path = f"/v2/tickers/{symbol}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            res = requests.get(f"{self.base_url}{path}", headers=headers, timeout=5)
            if res.status_code == 200:
                return res.json().get("result")
            return None
        except Exception as e:
            logger.warning("Error fetching Delta ticker for %s: %s", symbol, e)
            return None
