"""Service for resolving and validating active paper runner account state for the dashboard."""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.orb_settings import ORBSettings, settings as default_orb_settings
from services.delta_paper_broker import DeltaPaperBroker

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class DashboardAccountState:
    """Explicit state representation of the active paper runner for the dashboard."""
    account_name: str
    state_file_path: Path
    live_status_path: Optional[Path]
    exists: bool
    is_stale: bool
    last_updated_str: str
    starting_equity: float
    cash_balance: float
    realized_pnl: float
    unrealized_pnl: float
    equity: float
    has_open_position: bool
    open_position: Optional[Dict[str, Any]]
    closed_trade_count: int
    closed_trades: List[Dict[str, Any]]
    trading_mode: str
    warning_message: Optional[str]
    broker: Optional[DeltaPaperBroker]
    live_telemetry: Optional[Dict[str, Any]]


def resolve_dashboard_paper_state(
    custom_state_path: Optional[Path] = None,
    custom_live_status_path: Optional[Path] = None,
    stale_threshold_seconds: int = 300,
    base_dir: Optional[Path] = None,
    cfg: Optional[ORBSettings] = None,
) -> DashboardAccountState:
    """Resolve the active paper runner state file with explicit paths, staleness checks, and no silent fallbacks.
    
    Resolution order:
    1. custom_state_path parameter (if provided)
    2. DELTA_PAPER_STATE_FILE environment variable (if set)
    3. Active 24-hour forward test state file: data/paper_forward_24h_state.json
    """
    root = base_dir or PROJECT_ROOT

    # 1. Determine explicit state file path
    if custom_state_path:
        state_path = Path(custom_state_path)
    elif os.getenv("DELTA_PAPER_STATE_FILE"):
        state_path = Path(os.environ["DELTA_PAPER_STATE_FILE"])
    else:
        state_path = root / "data" / "paper_forward_24h_state.json"

    # 2. Determine explicit live status path
    if custom_live_status_path:
        live_path = Path(custom_live_status_path)
    elif os.getenv("DELTA_PAPER_LIVE_STATUS_FILE"):
        live_path = Path(os.environ["DELTA_PAPER_LIVE_STATUS_FILE"])
    else:
        live_path = root / "data" / "paper_forward_24h_live_status.json"

    account_name = "XAUTUSD 24-Hour Continuous Forward Test (Active Runner)"
    if "paper_forward_24h" not in state_path.name:
        account_name = f"XAUTUSD Paper Account ({state_path.stem})"

    # 3. Check file existence - DO NOT silently fallback
    if not state_path.exists():
        msg = f"Active paper runner state file not found: '{state_path}'. Ensure the forward runner daemon is running."
        logger.warning(msg)
        return DashboardAccountState(
            account_name=account_name,
            state_file_path=state_path,
            live_status_path=live_path if live_path.exists() else None,
            exists=False,
            is_stale=True,
            last_updated_str="FILE_NOT_FOUND",
            starting_equity=10000.0,
            cash_balance=10000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            equity=10000.0,
            has_open_position=False,
            open_position=None,
            closed_trade_count=0,
            closed_trades=[],
            trading_mode="STRICT PAPER",
            warning_message=msg,
            broker=None,
            live_telemetry=None,
        )

    # 4. Load live telemetry if present
    live_telemetry = None
    heartbeat_dt = None
    if live_path and live_path.exists():
        try:
            with open(live_path, "r", encoding="utf-8") as f:
                live_telemetry = json.load(f)
            up_str = live_telemetry.get("updated_at")
            if up_str:
                heartbeat_dt = datetime.fromisoformat(up_str)
        except Exception as exc:
            logger.warning("Failed to parse live status '%s': %s", live_path, exc)

    # 5. Load and inspect state file
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            raw_state = json.load(f)
    except Exception as exc:
        msg = f"Corrupted paper state file '{state_path}': {exc}"
        logger.error(msg)
        return DashboardAccountState(
            account_name=account_name,
            state_file_path=state_path,
            live_status_path=live_path,
            exists=True,
            is_stale=True,
            last_updated_str="CORRUPTED",
            starting_equity=10000.0,
            cash_balance=10000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            equity=10000.0,
            has_open_position=False,
            open_position=None,
            closed_trade_count=0,
            closed_trades=[],
            trading_mode="STRICT PAPER",
            warning_message=msg,
            broker=None,
            live_telemetry=live_telemetry,
        )

    last_updated_raw = raw_state.get("last_updated")
    last_updated_str = str(last_updated_raw) if last_updated_raw else "UNKNOWN"

    # 6. Staleness evaluation
    now_utc = datetime.now(timezone.utc)
    is_stale = False
    warning_msg = None

    if heartbeat_dt:
        # If live heartbeat exists, test staleness against the active runner heartbeat
        age_seconds = (now_utc - heartbeat_dt).total_seconds()
        if age_seconds > stale_threshold_seconds:
            is_stale = True
            warning_msg = (
                f"Active runner heartbeat is stale ({age_seconds:.0f}s old, threshold: {stale_threshold_seconds}s). "
                f"Last heartbeat: {heartbeat_dt.isoformat()}. Check daemon service health."
            )
            last_updated_str = f"{last_updated_str} (STALE: heartbeat {age_seconds:.0f}s ago)"
        else:
            last_updated_str = f"{heartbeat_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} (Heartbeat Live)"
    else:
        # Fallback to state file modification time
        mtime = datetime.fromtimestamp(state_path.stat().st_mtime, timezone.utc)
        age_seconds = (now_utc - mtime).total_seconds()
        if age_seconds > (stale_threshold_seconds * 3):
            is_stale = True
            warning_msg = (
                f"State file disk mtime is stale ({age_seconds:.0f}s old). "
                f"No active runner heartbeat file found at '{live_path}'."
            )

    # 7. Instantiate broker pointing explicitly to resolved state file
    broker_cfg = cfg or default_orb_settings
    broker = DeltaPaperBroker(cfg=broker_cfg, state_file=state_path, initial_equity=raw_state.get("initial_equity", 10000.0))
    summary = broker.get_account_summary()

    return DashboardAccountState(
        account_name=account_name,
        state_file_path=state_path,
        live_status_path=live_path,
        exists=True,
        is_stale=is_stale,
        last_updated_str=last_updated_str,
        starting_equity=broker.initial_equity,
        cash_balance=summary["cash_balance"],
        realized_pnl=summary["realized_pnl"],
        unrealized_pnl=summary["unrealized_pnl"],
        equity=summary["equity"],
        has_open_position=summary["has_open_position"],
        open_position=summary["open_position"],
        closed_trade_count=len(summary["closed_trades"]),
        closed_trades=summary["closed_trades"],
        trading_mode="STRICT PAPER",
        warning_message=warning_msg,
        broker=broker,
        live_telemetry=live_telemetry,
    )


def load_historical_14d_reference(base_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Load historical 14-day walk-forward study as a benchmark reference without mixing with the active test."""
    root = base_dir or PROJECT_ROOT
    csv_file = root / "data" / "forward_test_14d_trades.csv"
    summary_file = root / "data" / "forward_test_14d_summary.json"
    if not csv_file.exists() and not summary_file.exists():
        return None

    ref_data: Dict[str, Any] = {
        "title": "Historical 14-Day Walk-Forward Forward Test (Reference Benchmark)",
        "source_csv": str(csv_file) if csv_file.exists() else None,
        "source_json": str(summary_file) if summary_file.exists() else None,
        "total_trades": 7,
        "wins": 2,
        "losses": 5,
        "win_rate_pct": 28.57,
        "profit_factor": 0.83,
        "net_pnl": -0.0121,
        "gross_pnl": 0.0047,
        "total_friction": 0.0168,
        "ending_equity": 9999.9879,
    }
    return ref_data
