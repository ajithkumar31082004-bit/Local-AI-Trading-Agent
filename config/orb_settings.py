"""Configuration for the XAUTUSD ORB + EMA20 + Volume assistant."""
from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def _float(name: str, default: float) -> float:
    try: return float(os.getenv(name, str(default)))
    except (TypeError, ValueError): return default

def _int(name: str, default: int) -> int:
    try: return int(os.getenv(name, str(default)))
    except (TypeError, ValueError): return default

@dataclass(frozen=True)
class ORBSettings:
    symbol: str = os.getenv("ORB_SYMBOL", "XAUTUSD")
    mode: str = os.getenv("ORB_MODE", "PAPER").upper()
    timezone: str = os.getenv("ORB_TIMEZONE", "Asia/Kolkata")
    session_start: str = os.getenv("ORB_SESSION_START", "09:00")
    orb_minutes: int = _int("ORB_MINUTES", 15)
    ema_period: int = _int("ORB_EMA_PERIOD", 20)
    volume_period: int = _int("ORB_VOLUME_PERIOD", 20)
    max_ema_crosses: int = _int("ORB_MAX_EMA_CROSSES", 2)
    max_extension_atr: float = _float("ORB_MAX_EXTENSION_ATR", 1.0)
    atr_period: int = _int("ORB_ATR_PERIOD", 14)
    retest_tolerance: float = _float("ORB_RETEST_TOLERANCE", 0.15)
    rejection_wick_body_ratio: float = _float("ORB_REJECTION_WICK_BODY_RATIO", 1.0)
    max_bars_after_breakout: int = _int("ORB_MAX_BARS_AFTER_BREAKOUT", 12)
    stop_buffer: float = _float("ORB_STOP_BUFFER", 0.05)
    target_r: float = _float("ORB_TARGET_R", 2.0)
    risk_pct: float = _float("ORB_MAX_RISK_PCT", 0.5)
    daily_loss_limit_pct: float = _float("ORB_DAILY_LOSS_LIMIT_PCT", 1.5)
    max_trades_per_day: int = _int("ORB_MAX_TRADES_PER_DAY", 1)
    cooldown_after_loss_minutes: int = _int("ORB_LOSS_COOLDOWN_MINUTES", 60)
    contract_size_xaut: float = _float("ORB_CONTRACT_SIZE_XAUT", 0.001)
    lot_increment: int = _int("ORB_LOT_INCREMENT", 1)
    leverage: float = _float("ORB_LEVERAGE", 2.0)
    fee_pct_per_side: float = _float("ORB_FEE_PCT_PER_SIDE", 0.01)
    spread_pct_round_trip: float = _float("ORB_SPREAD_PCT_ROUND_TRIP", 0.02)
    slippage_pct_round_trip: float = _float("ORB_SLIPPAGE_PCT_ROUND_TRIP", 0.02)
    max_margin_pct_equity: float = _float("ORB_MAX_MARGIN_PCT_EQUITY", 10.0)
    state_path: Path = ROOT / "data" / "orb_risk_state.json"
    journal_path: Path = ROOT / "data" / "orb_journal.sqlite3"
    api_base_url: str = os.getenv("ORB_DELTA_API_BASE_URL", "https://api.india.delta.exchange").rstrip("/")
    request_timeout_seconds: int = _int("DELTA_PUBLIC_TIMEOUT_SECONDS", 10)

settings = ORBSettings()
if settings.mode not in {"PAPER", "ALERT_ONLY"}:
    # v1 intentionally has no live-order mode.
    object.__setattr__(settings, "mode", "PAPER")
