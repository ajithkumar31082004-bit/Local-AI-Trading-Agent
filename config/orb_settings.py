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

class ConfigurationError(ValueError):
    """Raised when application or trading mode configuration fails validation."""
    pass


@dataclass(frozen=True)
class ORBSettings:
    strategy_type: str = os.getenv("STRATEGY_TYPE", "ORB").upper()  # "ORB", "TREND_FOLLOWING", "DUAL"
    symbol: str = os.getenv("ORB_SYMBOL", "XAUTUSD")
    mode: str = os.getenv("TRADING_MODE", os.getenv("ORB_MODE", "PAPER")).upper()
    live_trading_acknowledged: str = os.getenv("LIVE_TRADING_ACKNOWLEDGED", os.getenv("ORB_LIVE_TRADING_ACKNOWLEDGED", "NO")).upper()
    kill_switch_active: bool = os.getenv("EMERGENCY_KILL_SWITCH", "false").lower() == "true"
    timezone: str = os.getenv("ORB_TIMEZONE", "Asia/Kolkata")
    session_start: str = os.getenv("ORB_SESSION_START", "09:00")
    session_end: str = os.getenv("ORB_SESSION_END", "23:30")
    no_entry_minutes_after_open: int = _int("ORB_NO_ENTRY_MINUTES_AFTER_OPEN", 45)
    last_entry_time: str = os.getenv("ORB_LAST_ENTRY_TIME", "22:00")
    cooldown_bars_after_loss: int = _int("ORB_COOLDOWN_BARS_AFTER_LOSS", 6)
    daily_loss_limit_r: float = _float("ORB_DAILY_LOSS_LIMIT_R", 2.0)
    use_regime_filter: bool = os.getenv("USE_REGIME_FILTER", "false").lower() == "true"
    min_15m_adx: float = _float("MIN_15M_ADX", 22.0)
    min_ema_separation_atr: float = _float("MIN_EMA_SEPARATION_ATR", 0.3)
    min_stop_distance_atr: float = _float("MIN_STOP_DISTANCE_ATR", 1.0)
    max_friction_to_risk_ratio: float = _float("MAX_FRICTION_TO_RISK_RATIO", 0.25)
    orb_minutes: int = _int("ORB_MINUTES", 15)
    ema_period: int = _int("ORB_EMA_PERIOD", 20)
    ema_fast_period: int = _int("ORB_EMA_FAST_PERIOD", 20)
    ema_slow_period: int = _int("ORB_EMA_SLOW_PERIOD", 50)
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
    api_base_url: str = os.getenv("ORB_DELTA_API_BASE_URL", os.getenv("DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")).rstrip("/").replace("https://india.delta.exchange", "https://api.india.delta.exchange")
    api_key: str = os.getenv("DELTA_EXCHANGE_API_KEY", "")
    api_secret: str = os.getenv("DELTA_EXCHANGE_API_SECRET", "")
    product_id: int = _int("ORB_DELTA_PRODUCT_ID", 131253)
    request_timeout_seconds: int = _int("DELTA_PUBLIC_TIMEOUT_SECONDS", 10)


def validate_settings(cfg: ORBSettings) -> None:
    """Strictly validate trading mode configuration without silent fallbacks."""
    if cfg.mode not in {"PAPER", "ALERT_ONLY", "LIVE"}:
        raise ConfigurationError(
            f"Invalid trading mode: '{cfg.mode}'. Supported modes are: PAPER, ALERT_ONLY, LIVE."
        )
    if cfg.mode == "LIVE":
        if cfg.live_trading_acknowledged != "YES":
            raise ConfigurationError(
                "LIVE trading mode requested but secondary confirmation switch LIVE_TRADING_ACKNOWLEDGED is not 'YES'. "
                "Refusing to execute live orders."
            )
        if not cfg.api_key or cfg.api_key.startswith("<") or not cfg.api_secret or cfg.api_secret.startswith("<"):
            raise ConfigurationError(
                "LIVE trading mode requires valid, non-placeholder DELTA_EXCHANGE_API_KEY and DELTA_EXCHANGE_API_SECRET. "
                "Please configure real exchange credentials."
            )

    if cfg.risk_pct <= 0 or cfg.risk_pct > 5.0:
        raise ConfigurationError(f"risk_pct must be between 0.01% and 5.0%, got {cfg.risk_pct}%")

    if cfg.daily_loss_limit_pct <= 0 or cfg.daily_loss_limit_pct > 20.0:
        raise ConfigurationError(f"daily_loss_limit_pct must be between 0.1% and 20.0%, got {cfg.daily_loss_limit_pct}%")

    if cfg.contract_size_xaut <= 0:
        raise ConfigurationError(f"contract_size_xaut must be positive, got {cfg.contract_size_xaut}")

    if cfg.orb_minutes != 15:
        raise ConfigurationError(f"orb_minutes must be exactly 15 for 3x5m ORB strategy, got {cfg.orb_minutes}")


settings = ORBSettings()
validate_settings(settings)
