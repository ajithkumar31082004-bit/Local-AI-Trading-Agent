"""Instrument resolution and specification validation for Delta Exchange products."""
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Dict, Any, Tuple
import requests

from config.orb_settings import ORBSettings, settings as default_orb_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeltaProductSpecs:
    product_id: int
    symbol: str
    description: str
    contract_type: str
    contract_value: Decimal     # Contract multiplier in XAUT (e.g. 0.001)
    contract_unit_currency: str # XAUT
    quoting_asset: str          # USD
    settling_asset: str         # USD
    tick_size: Decimal          # e.g. 0.01
    maker_commission_rate: Decimal
    taker_commission_rate: Decimal
    initial_margin_rate: Decimal # e.g. 0.01 (1%)
    trading_status: str         # "operational"
    state: str                  # "live"
    is_tradable: bool


class DeltaInstrumentService:
    """Service to validate instrument specifications against strategy assumptions."""

    def __init__(self, cfg: Optional[ORBSettings] = None):
        self.cfg = cfg or default_orb_settings
        self._cache: Dict[int, DeltaProductSpecs] = {}

    def get_product_specs(self, product_id: Optional[int] = None) -> Optional[DeltaProductSpecs]:
        """Fetch and cache official product specifications."""
        pid = product_id or self.cfg.product_id
        if pid in self._cache:
            return self._cache[pid]

        url = f"{self.cfg.api_base_url}/v2/products/{pid}"
        try:
            res = requests.get(url, timeout=self.cfg.request_timeout_seconds)
            if res.status_code != 200:
                logger.error("Failed to fetch product specs for %s: HTTP %s", pid, res.status_code)
                return None
            data = res.json().get("result")
            if not data:
                return None

            specs = DeltaProductSpecs(
                product_id=int(data["id"]),
                symbol=str(data["symbol"]).upper(),
                description=str(data.get("description", "")),
                contract_type=str(data.get("contract_type", "")),
                contract_value=Decimal(str(data.get("contract_value", "0.001"))),
                contract_unit_currency=str(data.get("contract_unit_currency", "XAUT")),
                quoting_asset=str(data.get("quoting_asset", {}).get("symbol", "USD")),
                settling_asset=str(data.get("settling_asset", {}).get("symbol", "USD")),
                tick_size=Decimal(str(data.get("tick_size", "0.01"))),
                maker_commission_rate=Decimal(str(data.get("maker_commission_rate", "0.0001"))),
                taker_commission_rate=Decimal(str(data.get("taker_commission_rate", "0.0001"))),
                initial_margin_rate=Decimal(str(data.get("initial_margin", "1.0"))) / Decimal("100.0"),
                trading_status=str(data.get("trading_status", "")),
                state=str(data.get("state", "")),
                is_tradable=(
                    str(data.get("trading_status", "")).lower() == "operational"
                    and str(data.get("state", "")).lower() == "live"
                ),
            )
            self._cache[pid] = specs
            return specs
        except Exception as exc:
            logger.error("Error fetching product specs for %s: %s", pid, exc)
            return None

    def validate_for_strategy(
        self,
        expected_symbol: str = "XAUTUSD",
        expected_contract_type: str = "perpetual_futures",
    ) -> Tuple[bool, str, Optional[DeltaProductSpecs]]:
        """Verify that the exchange instrument matches strategy assumptions."""
        specs = self.get_product_specs()
        if not specs:
            return False, f"Could not retrieve instrument specifications for product ID {self.cfg.product_id}.", None

        if specs.symbol != expected_symbol:
            return False, f"Symbol mismatch: expected '{expected_symbol}', exchange has '{specs.symbol}'.", specs

        if specs.contract_type != expected_contract_type:
            return False, f"Contract type mismatch: expected '{expected_contract_type}', got '{specs.contract_type}'.", specs

        if not specs.is_tradable:
            return False, f"Product {specs.symbol} is not tradable: state={specs.state}, status={specs.trading_status}.", specs

        expected_contract_size = Decimal(str(self.cfg.contract_size_xaut))
        if specs.contract_value != expected_contract_size:
            return False, (
                f"Contract size mismatch: strategy configured for {expected_contract_size} XAUT, "
                f"exchange product specifies {specs.contract_value} XAUT."
            ), specs

        return True, "Instrument validated successfully for strategy.", specs

    @staticmethod
    def align_price_to_tick(price: float, tick_size: Decimal) -> float:
        """Round price to the nearest allowable tick size."""
        dec_price = Decimal(str(price))
        quantized = (dec_price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size
        return float(quantized)
