"""Application configuration and environment settings."""

import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load local environment variables from .env if present
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


class Settings(BaseModel):
    """Central configuration for Local AI Trading Agent."""

    # Project paths
    project_root: Path = ROOT_DIR
    data_dir: Path = ROOT_DIR / "data"
    db_path: Path = ROOT_DIR / os.getenv("DATABASE_PATH", "data/trading_agent.db")

    # Ollama Local LLM Configuration
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
    ollama_timeout_seconds: int = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))

    # Trading Mode
    paper_trading_enabled: bool = os.getenv("PAPER_TRADING_ENABLED", "true").lower() == "true"
    default_starting_balance: float = float(os.getenv("DEFAULT_STARTING_BALANCE", "100000.0"))
    currency: str = os.getenv("CURRENCY", "USD")

    # Risk Management Settings
    max_risk_per_trade_pct: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "2.0"))  # % of equity at risk
    max_position_allocation_pct: float = float(os.getenv("MAX_POSITION_ALLOCATION_PCT", "20.0"))  # % of total portfolio per position
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))  # % daily max loss limit
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    default_stop_loss_pct: float = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "2.5"))
    default_take_profit_pct: float = float(os.getenv("DEFAULT_TAKE_PROFIT_PCT", "5.0"))

    # Execution Costs
    transaction_fee_pct: float = float(os.getenv("TRANSACTION_FEE_PCT", "0.1"))  # 0.1% per trade
    default_slippage_pct: float = float(os.getenv("DEFAULT_SLIPPAGE_PCT", "0.05"))  # 0.05% slippage

    # Market Data
    data_cache_expiry_minutes: int = int(os.getenv("DATA_CACHE_EXPIRY_MINUTES", "5"))
    default_symbol: str = os.getenv("DEFAULT_SYMBOL", "AAPL")

    model_config = {"arbitrary_types_allowed": True}


# Global settings singleton
settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
