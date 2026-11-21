"""Secure, read-only monitoring REST API for Delta Exchange India Paper Trading Agent on EC2.

Provides authenticated endpoints for mobile dashboard inspection:
- Health and system metrics (CPU, RAM, Disk, Uptime)
- Systemd service statuses (trading daemon, dashboard)
- Trading telemetry and account state (cash balance, equity, P&L, positions)
- Separated trade history (active 24h test vs historical benchmark)
- Git commit parity tracking (Local vs Remote vs EC2 Deployed)
- Deployment tracking records
- Live service logs with sensitive credential redaction
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

logger = logging.getLogger("delta_monitoring_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Base project paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
STATE_FILE_24H = DATA_DIR / "paper_forward_24h_state.json"
BENCHMARK_14D_CSV = DATA_DIR / "forward_test_14d_trades.csv"
DEPLOYMENTS_FILE = DATA_DIR / "deployments.json"
SERVICE_LOG_FILE = DATA_DIR / "trading_daemon.log"
FALLBACK_LOG_FILE = DATA_DIR / "paper_trading_service.log"

# Security Configuration (Strict Fail-Closed Enforcement)
DEFAULT_SECRET = "delta_paper_monitor_sec_key_2026_xaut"
MIN_KEY_LENGTH = 32
API_KEY = os.getenv("MONITORING_API_KEY", "").strip()
if not API_KEY or API_KEY == DEFAULT_SECRET or len(API_KEY) < MIN_KEY_LENGTH:
    logger.critical(
        "FATAL: MONITORING_API_KEY is missing, uses the insecure default, or has insufficient entropy (< %d chars).",
        MIN_KEY_LENGTH,
    )
    raise RuntimeError(
        f"Monitoring API cannot start without a secure API key (minimum {MIN_KEY_LENGTH} characters)."
    )

# Rate Limiter: In-memory sliding window with thread safety and memory bounds
def _get_validated_rate_limit() -> int:
    """Validate MONITORING_RATE_LIMIT environment variable. Defaults to 60 req/min."""
    raw = os.getenv("MONITORING_RATE_LIMIT", "60").strip()
    try:
        val = int(raw)
        if 1 <= val <= 10000:
            return val
        logger.warning("MONITORING_RATE_LIMIT %d out of bounds (1-10000). Falling back to 60.", val)
        return 60
    except (ValueError, TypeError):
        logger.warning("Invalid MONITORING_RATE_LIMIT '%s'. Falling back to 60.", raw)
        return 60


RATE_LIMIT_PER_MINUTE = _get_validated_rate_limit()
_request_counts: Dict[str, List[float]] = defaultdict(list)
_rate_limit_lock = threading.Lock()
MAX_TRACKED_IPS = 500

# Sensitive Patterns for Redaction
REDACTION_RULES = [
    (re.compile(r"(api[_-]?key|secret|token|password|auth|bearer)[\s=:\"']+([a-zA-Z0-9_\-\.]{8,})", re.IGNORECASE), r"\1=***REDACTED***"),
    (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----"), "***REDACTED_PRIVATE_KEY***"),
    (re.compile(r"(AWS_SECRET_ACCESS_KEY|DELTA_API_SECRET|DELTA_API_KEY)=([^\s]+)"), r"\1=***REDACTED***"),
]


def redact_sensitive_info(text: str) -> str:
    """Mask credentials, tokens, and private keys from returned text."""
    if not isinstance(text, str):
        return text
    sanitized = text
    for pattern, repl in REDACTION_RULES:
        sanitized = pattern.sub(repl, sanitized)
    return sanitized


def sanitize_dict(data: Any) -> Any:
    """Recursively sanitize dict and list objects."""
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if any(term in k.lower() for term in ("key", "secret", "password", "token", "auth")):
                cleaned[k] = "***REDACTED***"
            else:
                cleaned[k] = sanitize_dict(v)
        return cleaned
    elif isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    elif isinstance(data, str):
        return redact_sensitive_info(data)
    return data


# Authentication Dependency
security = HTTPBearer(auto_error=False)


def verify_monitoring_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> str:
    """Verify Bearer token or X-API-Key header with constant-time comparison and thread-safe rate limiting."""
    client_ip = request.client.host if request.client else "unknown"

    # Authenticate first; only count request against rate limit after token validation
    token = None
    if credentials:
        token = credentials.credentials
    elif "X-API-Key" in request.headers:
        token = request.headers["X-API-Key"]

    if not token or not secrets.compare_digest(token, API_KEY):
        logger.warning("Unauthorized access attempt to %s from %s", request.url.path, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing monitoring authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Enforce Rate Limiting (only for authenticated requests)
    now = time.time()
    cutoff = now - 60.0
    limit = _get_validated_rate_limit()

    with _rate_limit_lock:
        # Guarantee memory bound: if client_ip is new and dictionary is at capacity (>= MAX_TRACKED_IPS),
        # evict stale or oldest entries BEFORE inserting new key so size NEVER exceeds MAX_TRACKED_IPS.
        if client_ip not in _request_counts and len(_request_counts) >= MAX_TRACKED_IPS:
            stale_ips = [ip for ip, timestamps in _request_counts.items() if not timestamps or timestamps[-1] <= cutoff]
            for ip in stale_ips:
                del _request_counts[ip]
            if len(_request_counts) >= MAX_TRACKED_IPS:
                excess = len(_request_counts) - MAX_TRACKED_IPS + 1
                sorted_ips = sorted(_request_counts.items(), key=lambda item: item[1][-1] if item[1] else 0)
                for ip, _ in sorted_ips[:excess]:
                    del _request_counts[ip]

        _request_counts[client_ip] = [t for t in _request_counts[client_ip] if t > cutoff]
        if len(_request_counts[client_ip]) >= limit:
            logger.warning("Rate limit exceeded for IP: %s (limit: %d/min)", client_ip, limit)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {limit} requests per minute.",
            )
        _request_counts[client_ip].append(now)

    return token


# Initialize FastAPI Application
app = FastAPI(
    title="Delta Exchange India Paper Trading Agent — Monitor API",
    description="Secure, read-only operational telemetry and deployment inspection API.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS Configuration (Restricted to Explicit Trusted Origins, Credentials Disabled by Default)
raw_origins = os.getenv("MONITORING_ALLOWED_ORIGINS", "http://127.0.0.1,http://localhost").strip()
ALLOWED_ORIGINS = [orig.strip() for orig in raw_origins.split(",") if orig.strip()]
if any(orig == "*" for orig in ALLOWED_ORIGINS):
    logger.critical("FATAL: Wildcard origin '*' is strictly forbidden in MONITORING_ALLOWED_ORIGINS.")
    raise ValueError("CORS wildcard '*' is strictly forbidden for security compliance.")

ALLOW_CREDENTIALS = os.getenv("MONITORING_ALLOW_CREDENTIALS", "false").lower() in ("true", "1", "yes")
if ALLOW_CREDENTIALS and any(orig == "*" for orig in ALLOWED_ORIGINS):
    raise ValueError("CORS wildcard '*' with credentials allowed is strictly prohibited.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["GET"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: str
    version: str
    trading_mode: str
    live_trading_enabled: bool


class SystemStatusResponse(BaseModel):
    hostname: Optional[str] = None
    uptime_seconds: Optional[float] = None
    cpu_percent: Optional[float] = None  # Measured from /proc/stat delta (None when unavailable)
    memory_total_mb: Optional[float] = None
    memory_used_mb: Optional[float] = None
    memory_free_mb: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_used_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    disk_percent: Optional[float] = None
    load_averages: Optional[List[float]] = None
    telemetry_available: bool = True
    timestamp: str


class ServiceItem(BaseModel):
    name: str
    active: bool
    substate: str
    pid: Optional[int] = None
    uptime: Optional[str] = None
    memory_mb: Optional[float] = None
    restarts: int = 0
    description: str


class ServicesResponse(BaseModel):
    services: List[ServiceItem]
    timestamp: str


class TradingStatusResponse(BaseModel):
    symbol: str = "XAUTUSD"
    trading_mode: str = "PAPER"
    live_trading_enabled: bool = False
    live_trading_acknowledged: str = "NO"
    ws_connected: bool = False
    runner_ready: bool = False
    systemd_active: bool = False
    current_price: Optional[float] = None
    market_regime: str = "UNKNOWN"
    ticks_ingested: int = 0
    completed_5m_bars: int = 0
    completed_15m_bars: int = 0
    last_candle_timestamp: Optional[str] = None
    daily_trade_count: Optional[int] = None
    max_daily_trades: int = 1
    daily_risk_guard_active: Optional[bool] = None
    state_available: bool = True
    last_updated: Optional[str] = None


class AccountResponse(BaseModel):
    symbol: str = "XAUTUSD"
    initial_equity: Optional[float] = None
    cash_balance: Optional[float] = None
    current_equity: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    total_fees: Optional[float] = None
    total_slippage: Optional[float] = None
    peak_equity: Optional[float] = None
    open_position: Optional[Dict[str, Any]] = None
    last_updated: Optional[str] = None


class GitStatusResponse(BaseModel):
    branch: Optional[str] = None
    ec2_commit_sha: Optional[str] = None
    remote_commit_sha: Optional[str] = None
    in_sync: Optional[bool] = None
    is_clean: Optional[bool] = None
    sync_upstream_reference: str = "@{u} (cached remote tracking ref; requires network fetch to update)"
    latest_commit_message: Optional[str] = None
    latest_commit_author: Optional[str] = None
    latest_commit_date: Optional[str] = None
    uncommitted_files_count: int = 0


class DeploymentRecord(BaseModel):
    id: str
    timestamp: str
    commit_sha: str
    branch: str
    author: str
    environment: str = "EC2"
    status: str
    tests_passed: bool
    message: str


# ---------------------------------------------------------------------------
# Helper Utilities
# ---------------------------------------------------------------------------
def _get_systemd_status(service_name: str) -> ServiceItem:
    """Read systemd status using systemctl or fallback to mock/proc."""
    if "trading-daemon" in service_name:
        desc = "Delta Exchange Paper Trading Daemon"
    elif "shadow-observer" in service_name:
        desc = "Delta Exchange Shadow Observer"
    elif "monitoring-api" in service_name:
        desc = "Delta Monitoring REST API"
    else:
        desc = service_name
    try:
        res = subprocess.run(
            ["systemctl", "show", service_name, "--property=ActiveState,SubState,MainPID,NRestarts"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode == 0:
            props = {}
            for line in res.stdout.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v
            active = props.get("ActiveState") == "active"
            pid = int(props.get("MainPID", 0)) if props.get("MainPID") else None
            restarts = int(props.get("NRestarts", 0)) if props.get("NRestarts") else 0
            return ServiceItem(
                name=service_name,
                active=active,
                substate=props.get("SubState", "unknown"),
                pid=pid if pid and pid > 0 else None,
                uptime="Running" if active else "Stopped",
                memory_mb=None,
                restarts=restarts,
                description=desc,
            )
    except Exception:
        pass
    return ServiceItem(
        name=service_name,
        active=False,
        substate="unknown",
        pid=None,
        uptime=None,
        memory_mb=None,
        restarts=0,
        description=desc,
    )


def _load_24h_state() -> Dict[str, Any]:
    """Load the active 24-hour paper trading state file safely."""
    if not STATE_FILE_24H.exists():
        return {}
    try:
        with open(STATE_FILE_24H, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to read %s: %s", STATE_FILE_24H, exc)
        return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Public health check endpoint."""
    return HealthResponse(
        status="ok",
        service="delta-monitoring-api",
        timestamp=datetime.now(timezone.utc).isoformat(),
        version="1.0.0",
        trading_mode="PAPER",
        live_trading_enabled=False,
    )


@app.get("/api/ec2/status", response_model=SystemStatusResponse)
def get_ec2_status(token: str = Depends(verify_monitoring_auth)) -> SystemStatusResponse:
    """Return CPU, Memory, and Disk usage metrics of the EC2 instance without fabricated defaults."""
    import platform
    hostname = platform.node() or None
    telemetry_available = False

    # Memory info via /proc/meminfo (no fabricated defaults)
    mem_total: Optional[float] = None
    mem_free: Optional[float] = None
    mem_used: Optional[float] = None
    mem_pct: Optional[float] = None
    if Path("/proc/meminfo").exists():
        try:
            meminfo = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v = parts[1].strip().split()[0]
                        meminfo[k] = float(v) / 1024.0  # MB
            if "MemTotal" in meminfo:
                mem_total = round(meminfo["MemTotal"], 1)
                mem_free = round(meminfo.get("MemAvailable", meminfo.get("MemFree", 0.0)), 1)
                mem_used = round(mem_total - mem_free, 1)
                mem_pct = round((mem_used / mem_total) * 100.0, 1) if mem_total > 0 else 0.0
                telemetry_available = True
        except Exception:
            pass

    # Disk info
    disk_total_gb: Optional[float] = None
    disk_used_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    disk_pct: Optional[float] = None
    try:
        total_d, used_d, free_d = shutil.disk_usage("/")
        disk_total_gb = round(total_d / (1024**3), 2)
        disk_used_gb = round(used_d / (1024**3), 2)
        disk_free_gb = round(free_d / (1024**3), 2)
        disk_pct = round((used_d / total_d) * 100.0, 1) if total_d > 0 else 0.0
    except Exception:
        pass

    # Uptime & Load Averages (None when unavailable)
    uptime_sec: Optional[float] = None
    if Path("/proc/uptime").exists():
        try:
            with open("/proc/uptime", "r") as f:
                uptime_sec = round(float(f.read().split()[0]), 1)
                telemetry_available = True
        except Exception:
            pass

    loads: Optional[List[float]] = None
    if hasattr(os, "getloadavg"):
        try:
            loads = [round(x, 2) for x in os.getloadavg()]
        except Exception:
            pass

    # CPU: read /proc/stat for true utilisation (accounting for idle and iowait; excluding guest double-count)
    cpu_percent: Optional[float] = None
    if Path("/proc/stat").exists():
        try:
            def _read_cpu_times() -> tuple[int, int]:
                with open("/proc/stat", "r") as f:
                    line = f.readline()  # first line: "cpu  user nice system idle iowait irq softirq steal guest guest_nice"
                vals = [int(x) for x in line.split()[1:]]
                user = vals[0] if len(vals) > 0 else 0
                nice = vals[1] if len(vals) > 1 else 0
                system = vals[2] if len(vals) > 2 else 0
                idle = vals[3] if len(vals) > 3 else 0
                iowait = vals[4] if len(vals) > 4 else 0
                irq = vals[5] if len(vals) > 5 else 0
                softirq = vals[6] if len(vals) > 6 else 0
                steal = vals[7] if len(vals) > 7 else 0
                # Accounting: idle_all includes idle and iowait
                idle_all = idle + iowait
                # Total includes user, nice, system, irq, softirq, steal, idle_all
                # Guest and guest_nice are already accounted for in user and nice; do not add again
                total = user + nice + system + irq + softirq + steal + idle_all
                return idle_all, total

            idle0, total0 = _read_cpu_times()
            time.sleep(0.1)
            idle1, total1 = _read_cpu_times()
            delta_total = total1 - total0
            delta_idle = idle1 - idle0
            if delta_total > 0:
                raw_pct = (1.0 - (delta_idle / delta_total)) * 100.0
                cpu_percent = round(max(0.0, min(100.0, raw_pct)), 1)
                telemetry_available = True
        except Exception:
            pass

    return SystemStatusResponse(
        hostname=hostname,
        uptime_seconds=uptime_sec,
        cpu_percent=cpu_percent,
        memory_total_mb=mem_total,
        memory_used_mb=mem_used,
        memory_free_mb=mem_free,
        memory_percent=mem_pct,
        disk_total_gb=disk_total_gb,
        disk_used_gb=disk_used_gb,
        disk_free_gb=disk_free_gb,
        disk_percent=disk_pct,
        load_averages=loads,
        telemetry_available=telemetry_available,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/services", response_model=ServicesResponse)
def get_services(token: str = Depends(verify_monitoring_auth)) -> ServicesResponse:
    """Return status of delta trading daemon, shadow observer, and monitoring API systemd services."""
    services = [
        _get_systemd_status("delta-trading-daemon.service"),
        _get_systemd_status("delta-shadow-observer.service"),
        _get_systemd_status("delta-monitoring-api.service"),
    ]
    return ServicesResponse(
        services=services,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/trading/status", response_model=TradingStatusResponse)
def get_trading_status(token: str = Depends(verify_monitoring_auth)) -> TradingStatusResponse:
    """Return live paper runner telemetry parsed from state and logs (no fabricated fallbacks)."""
    state = _load_24h_state()
    state_available = bool(state)

    # Parse last log line for live tick and candle telemetry
    ticks = 0
    bars_5m = 0
    bars_15m = 0
    price: Optional[float] = None
    regime = "UNKNOWN"
    ws_ok = False
    runner_ready = False
    systemd_active = False

    active_log = None
    if SERVICE_LOG_FILE.exists():
        active_log = SERVICE_LOG_FILE
    elif FALLBACK_LOG_FILE.exists():
        active_log = FALLBACK_LOG_FILE

    now_utc = datetime.now(timezone.utc)
    now_ts = now_utc.timestamp()

    if active_log:
        try:
            with open(active_log, "r", encoding="utf-8") as f:
                lines = f.readlines()[-30:]
                for line in reversed(lines):
                    if "Ticks:" in line and "Price:" in line:
                        m_ticks = re.search(r"Ticks:\s*(\d+)", line)
                        m_price = re.search(r"Price:\s*\$\s*([\d\.]+)", line)
                        m_5m = re.search(r"5m:\s*(\d+)", line)
                        m_15m = re.search(r"15m:\s*(\d+)", line)
                        m_reg = re.search(r"Regime:\s*([A-Z_]+)", line)
                        if m_ticks:
                            ticks = int(m_ticks.group(1))
                        if m_price:
                            price = float(m_price.group(1))
                        if m_5m:
                            bars_5m = int(m_5m.group(1))
                        if m_15m:
                            bars_15m = int(m_15m.group(1))
                        if m_reg:
                            regime = m_reg.group(1)

                        # Finding B: Check log timestamp freshness (must be within last 60 seconds)
                        ts_match = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
                        log_fresh = False
                        if ts_match:
                            try:
                                dt_parsed = datetime.fromisoformat(ts_match.group(1).replace(" ", "T")).replace(tzinfo=timezone.utc)
                                if abs(now_ts - dt_parsed.timestamp()) <= 60.0:
                                    log_fresh = True
                            except Exception:
                                pass

                        # WebSocket connected requires fresh ticks/heartbeat, not historical lines
                        if log_fresh and (ticks > 0 or "WebSocket connected" in line):
                            ws_ok = True

                        # Runner ready requires confirmed initialization (fresh log heartbeat or explicit runner_ready state)
                        if log_fresh or state.get("runner_ready") is True:
                            runner_ready = True
                        break
        except Exception as exc:
            logger.warning("Error reading %s: %s", active_log, exc)

    # Check systemd status for delta-trading-daemon
    try:
        svc_item = _get_systemd_status("delta-trading-daemon.service")
        systemd_active = svc_item.active
        # Distinguish process activity from application initialization:
        # Active systemd unit only marks runner_ready if runner state confirms initialization
        if svc_item.active and (state.get("runner_ready") is True or runner_ready):
            runner_ready = True
    except Exception:
        pass

    daily_count: Optional[int] = None
    daily_guard: Optional[bool] = None

    if state_available:
        closed_trades = state.get("closed_trades", [])
        open_pos = state.get("open_position")

        # Finding E: Filter closed trades strictly by today's UTC trading-session date using entry_time
        today_utc = now_utc.strftime("%Y-%m-%d")
        daily_closed = []
        for tr in closed_trades:
            entry_ts = str(tr.get("entry_time") or tr.get("timestamp") or "")
            if not entry_ts and "exit_time" in tr:
                entry_ts = str(tr.get("exit_time", ""))
            if entry_ts.startswith(today_utc):
                daily_closed.append(tr)

        daily_count = len(daily_closed) + (1 if open_pos else 0)
        daily_guard = daily_count >= 1

    return TradingStatusResponse(
        symbol=state.get("symbol", "XAUTUSD"),
        trading_mode="PAPER",
        live_trading_enabled=False,
        live_trading_acknowledged="NO",
        ws_connected=ws_ok,
        runner_ready=runner_ready,
        systemd_active=systemd_active,
        current_price=price,
        market_regime=regime,
        ticks_ingested=ticks,
        completed_5m_bars=bars_5m,
        completed_15m_bars=bars_15m,
        last_candle_timestamp=state.get("last_updated"),
        daily_trade_count=daily_count,
        max_daily_trades=1,
        daily_risk_guard_active=daily_guard,
        state_available=state_available,
        last_updated=state.get("last_updated"),
    )


@app.get("/api/trading/account", response_model=AccountResponse)
def get_account_state(token: str = Depends(verify_monitoring_auth)) -> AccountResponse:
    """Return account cash balance, equity, and P&L breakdown without fabricated defaults."""
    state = _load_24h_state()
    cash = state.get("cash_balance")
    initial_eq = state.get("initial_equity")
    realized = state.get("realized_pnl")
    open_pos = state.get("open_position")

    unrealized: Optional[float] = None
    if open_pos:
        # Finding C: Missing unrealized P&L on open position must remain None, never assumed 0.0
        unrealized = open_pos.get("unrealized_pnl")
    elif state:
        unrealized = 0.0

    current_eq: Optional[float] = None
    if cash is not None:
        if open_pos and unrealized is None:
            # Cannot calculate current equity without knowing open position's unrealized P&L
            current_eq = None
        else:
            current_eq = round(cash + (unrealized if unrealized is not None else 0.0), 4)

    return AccountResponse(
        symbol=state.get("symbol", "XAUTUSD"),
        initial_equity=initial_eq,
        cash_balance=cash,
        current_equity=current_eq,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        total_fees=state.get("total_fees"),
        total_slippage=state.get("total_slippage"),
        peak_equity=state.get("peak_equity"),
        open_position=open_pos,
        last_updated=state.get("last_updated"),
    )


@app.get("/api/trading/trades")
def get_trades(token: str = Depends(verify_monitoring_auth)) -> Dict[str, Any]:
    """Return closed trades with dual P&L reporting, strictly segregated from the 14d benchmark."""
    state = _load_24h_state()
    active_closed = state.get("closed_trades", [])

    # Enrich active trades with clear dual gross P&L fields without fabricated constants
    enriched_active = []
    for tr in active_closed:
        entry_f = tr.get("entry_price")
        exit_f = tr.get("exit_price")
        qty = tr.get("contract_qty")
        sig_gross = tr.get("gross_pnl")
        price_impact = tr.get("slippage")

        # Compute fill-side gross P&L correctly accounting for trade direction and contract multiplier.
        # Delta Exchange XAUTUSD: 1 lot = 1 USD notional per point; direction = side ("BUY"/"SELL").
        # gross_pnl_fill = direction_sign × (exit - entry) × qty × contract_multiplier
        fill_gross = None
        if exit_f is not None and entry_f is not None and qty is not None:
            side_raw = str(tr.get("side", tr.get("direction", ""))).strip().upper()
            if any(s in side_raw for s in ("BUY", "LONG")):
                direction_sign = 1.0
            elif any(s in side_raw for s in ("SELL", "SHORT")):
                direction_sign = -1.0
            else:
                direction_sign = None  # Unknown or missing direction

            raw_mult = tr.get("contract_multiplier")
            contract_multiplier = None
            if raw_mult is not None:
                try:
                    parsed_mult = float(raw_mult)
                    if parsed_mult > 0:
                        contract_multiplier = parsed_mult
                except (ValueError, TypeError):
                    contract_multiplier = None

            # Finding C: Only compute fill gross if BOTH direction and multiplier are explicitly valid
            if direction_sign is not None and contract_multiplier is not None:
                fill_gross = round(direction_sign * (exit_f - entry_f) * qty * contract_multiplier, 4)
            else:
                fill_gross = None

        enriched_tr = dict(tr)
        enriched_tr["gross_pnl_signal"] = sig_gross
        enriched_tr["gross_pnl_fill"] = fill_gross
        enriched_tr["price_impact"] = price_impact
        enriched_tr["net_pnl"] = tr.get("net_pnl")
        enriched_active.append(enriched_tr)

    # Historical 14-day benchmark summary (strictly isolated)
    benchmark_trades: List[Dict[str, Any]] = []
    if BENCHMARK_14D_CSV.exists():
        try:
            with open(BENCHMARK_14D_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    benchmark_trades.append(row)
        except Exception as exc:
            logger.warning("Could not parse %s: %s", BENCHMARK_14D_CSV, exc)

    return {
        "active_forward_test_trades": enriched_active,
        "active_trade_count": len(enriched_active),
        "historical_14d_benchmark": {
            "total_trades": len(benchmark_trades),
            "trades": benchmark_trades,
            "notice": "Historical 14-day backtest benchmark is strictly isolated from active 24h forward test.",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/git/status", response_model=GitStatusResponse)
def get_git_status(token: str = Depends(verify_monitoring_auth)) -> GitStatusResponse:
    """Return current branch, commit SHA, and verified parity comparison against remote tracking branch."""
    branch: Optional[str] = None
    ec2_sha: Optional[str] = None
    remote_sha: Optional[str] = None
    in_sync: Optional[bool] = None
    is_clean: Optional[bool] = None
    msg: Optional[str] = None
    author: Optional[str] = None
    date_str: Optional[str] = None
    uncommitted = 0

    try:
        # Get current branch
        b_res = subprocess.run(["git", "--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=BASE_DIR, timeout=5.0)
        if b_res.returncode == 0 and b_res.stdout.strip():
            branch = b_res.stdout.strip()

        # Get local commit SHA
        sha_res = subprocess.run(["git", "--no-optional-locks", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=BASE_DIR, timeout=5.0)
        if sha_res.returncode == 0 and sha_res.stdout.strip():
            ec2_sha = sha_res.stdout.strip()

        # Get remote upstream tracking commit SHA (only if tracking branch exists)
        remote_res = subprocess.run(["git", "--no-optional-locks", "rev-parse", "@{u}"], capture_output=True, text=True, cwd=BASE_DIR, timeout=5.0)
        if remote_res.returncode == 0 and remote_res.stdout.strip():
            remote_sha = remote_res.stdout.strip()
            if ec2_sha:
                in_sync = (ec2_sha == remote_sha)
        else:
            remote_sha = None
            in_sync = None

        # Get latest commit message, author, date
        log_res = subprocess.run(["git", "--no-optional-locks", "log", "-1", "--format=%an|%ad|%s"], capture_output=True, text=True, cwd=BASE_DIR, timeout=5.0)
        if log_res.returncode == 0 and "|" in log_res.stdout:
            parts = log_res.stdout.strip().split("|", 2)
            author = parts[0]
            date_str = parts[1]
            msg = parts[2]

        # Check working tree cleanliness
        st_res = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain"], capture_output=True, text=True, cwd=BASE_DIR, timeout=5.0)
        if st_res.returncode == 0:
            lines = [l for l in st_res.stdout.strip().split("\n") if l.strip()]
            uncommitted = len(lines)
            is_clean = (uncommitted == 0)
    except Exception as exc:
        logger.warning("Git check error: %s", exc)

    return GitStatusResponse(
        branch=branch,
        ec2_commit_sha=ec2_sha,
        remote_commit_sha=remote_sha,
        in_sync=in_sync,
        is_clean=is_clean,
        sync_upstream_reference="@{u} (cached remote tracking ref; requires network fetch to update)",
        latest_commit_message=msg,
        latest_commit_author=author,
        latest_commit_date=date_str,
        uncommitted_files_count=uncommitted,
    )


@app.get("/api/git/commits")
def get_git_commits(
    limit: int = Query(10, ge=1, le=50),
    token: str = Depends(verify_monitoring_auth),
) -> List[Dict[str, Any]]:
    """Return recent git commit history."""
    commits = []
    try:
        res = subprocess.run(
            ["git", "--no-optional-locks", "log", f"-{limit}", "--format=%H|%h|%an|%ad|%s"],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=5.0,
        )
        if res.returncode == 0:
            for line in res.stdout.strip().split("\n"):
                if "|" in line:
                    full_sha, short_sha, author, dt, msg = line.split("|", 4)
                    commits.append({
                        "commit_sha": full_sha,
                        "short_sha": short_sha,
                        "author": author,
                        "date": dt,
                        "message": redact_sensitive_info(msg),
                    })
    except Exception as exc:
        logger.warning("Git log error: %s", exc)

    return commits


@app.get("/api/git/changes")
def get_git_changes(token: str = Depends(verify_monitoring_auth)) -> Dict[str, Any]:
    """Return changed files and sanitized diff for the latest commit or working tree."""
    changed_files = []
    diff_text = ""
    commit_sha: Optional[str] = None
    try:
        sha_res = subprocess.run(
            ["git", "--no-optional-locks", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=5.0,
        )
        if sha_res.returncode == 0 and sha_res.stdout.strip():
            commit_sha = sha_res.stdout.strip()

        res_files = subprocess.run(
            ["git", "--no-optional-locks", "show", "--stat", "--name-status", "HEAD"],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=5.0,
        )
        if res_files.returncode == 0:
            for line in res_files.stdout.split("\n"):
                if line.startswith(("M\t", "A\t", "D\t")):
                    parts = line.split("\t", 1)
                    changed_files.append({"status": parts[0], "file": parts[1]})

        res_diff = subprocess.run(
            ["git", "--no-optional-locks", "show", "-p", "--no-color", "HEAD"],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=10.0,
        )
        if res_diff.returncode == 0:
            diff_text = redact_sensitive_info(res_diff.stdout[:15000])
    except Exception as exc:
        diff_text = f"Error reading git diff: {exc}"

    return {
        "commit_sha": commit_sha,
        "changed_files": changed_files,
        "diff_preview": diff_text,
        "notice": "All sensitive credentials and private keys are strictly redacted.",
    }


@app.get("/api/deployments")
def get_deployments(token: str = Depends(verify_monitoring_auth)) -> List[DeploymentRecord]:
    """Return deployment history tracking records."""
    if DEPLOYMENTS_FILE.exists():
        try:
            with open(DEPLOYMENTS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    return [DeploymentRecord(**sanitize_dict(item)) for item in raw]
        except Exception as exc:
            logger.warning("Could not parse %s: %s", DEPLOYMENTS_FILE, exc)

    # Return empty list when deployment tracking file is missing or invalid
    return []


@app.get("/api/logs")
def get_service_logs(
    service: str = Query("trading", pattern="^(trading|daemon|observer|monitoring|delta-trading-daemon|delta-shadow-observer|delta-monitoring-api|dashboard)$"),
    level: str = Query("ALL", pattern="^(ALL|INFO|WARNING|ERROR)$"),
    limit: int = Query(50, ge=1, le=200),
    token: str = Depends(verify_monitoring_auth),
) -> Dict[str, Any]:
    """Return recent logs filtered by service and severity, with sensitive data redacted."""
    raw_lines: List[str] = []

    service_lower = service.lower()
    if service_lower in ("trading", "daemon", "delta-trading-daemon", "delta-trading-daemon.service"):
        svc_name = "delta-trading-daemon.service"
        primary_log = SERVICE_LOG_FILE
        fallback_log = FALLBACK_LOG_FILE
    elif service_lower in ("observer", "shadow", "delta-shadow-observer", "delta-shadow-observer.service"):
        svc_name = "delta-shadow-observer.service"
        primary_log = DATA_DIR / "shadow_observer.log"
        fallback_log = None
    elif service_lower in ("monitoring", "api", "delta-monitoring-api", "delta-monitoring-api.service"):
        svc_name = "delta-monitoring-api.service"
        primary_log = DATA_DIR / "monitoring_api.log"
        fallback_log = None
    else:
        # Backward compatibility fallback
        svc_name = "delta-trading-daemon.service"
        primary_log = SERVICE_LOG_FILE
        fallback_log = FALLBACK_LOG_FILE

    active_log = None
    if primary_log and primary_log.exists():
        active_log = primary_log
    elif fallback_log and fallback_log.exists():
        active_log = fallback_log

    if active_log:
        try:
            # Stream only the tail needed — avoid loading the entire file into memory
            with open(active_log, "r", encoding="utf-8") as f:
                f.seek(0, 2)
                file_size = f.tell()
                read_bytes = min(file_size, limit * 300)
                f.seek(max(0, file_size - read_bytes))
                raw_lines = f.readlines()
        except Exception as exc:
            raw_lines = [f"Error reading {active_log.name}: {exc}"]
    else:
        # Fallback to systemd journal
        try:
            res = subprocess.run(
                ["journalctl", "-u", svc_name, "-n", str(limit), "--no-pager"],  # -n and value as separate args
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if res.returncode == 0:
                raw_lines = res.stdout.split("\n")
            else:
                raw_lines = [f"No journal output available for {svc_name}"]
        except Exception:
            raw_lines = [f"No journal output available for {svc_name}"]

    # Filter by level
    filtered = []
    for line in raw_lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        if level != "ALL" and level not in line_clean.upper():
            continue
        filtered.append(redact_sensitive_info(line_clean))

    return {
        "service": service,
        "level_filter": level,
        "total_lines": len(filtered),
        "lines": filtered[-limit:],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
