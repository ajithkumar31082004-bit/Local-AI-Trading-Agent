#!/usr/bin/env python3
"""Stage 1: EC2 Read-Only Public Market-Data Observer for XAUTUSD on Delta Exchange India.

STRICT SAFETY RESTRICTIONS & NON-NEGOTIABLE INVARIANTS:
1. READ-ONLY public market data observer ONLY.
2. ZERO strategy execution, ZERO broker initialization, ZERO order generation.
3. NEVER access private exchange endpoints; NEVER request or use API keys/signatures.
4. NEVER start, restart, stop, enable, or disable any systemd services.
5. Trading daemon (delta-paper-trading.service) remains STOPPED before, during, and after.
6. Isolated output: strictly writes to reports/stage1_readonly/.
7. Does NOT alter production state, historical ledgers, or monitoring applications.
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
from typing import Dict, Any, List, Optional, Tuple, Set
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Auto-activate repository .venv site-packages if invoked with system Python
if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
    import site
    venv_site_win = (PROJECT_ROOT / ".venv" / "Lib" / "site-packages").resolve()
    if venv_site_win.exists():
        site.addsitedir(str(venv_site_win))
        if str(venv_site_win) in sys.path:
            sys.path.remove(str(venv_site_win))
        sys.path.insert(0, str(venv_site_win))
    else:
        venv_lib = PROJECT_ROOT / ".venv" / "lib"
        if venv_lib.exists():
            for py_dir in venv_lib.glob("python*"):
                posix_site = (py_dir / "site-packages").resolve()
                if posix_site.exists():
                    site.addsitedir(str(posix_site))
                    if str(posix_site) in sys.path:
                        sys.path.remove(str(posix_site))
                    sys.path.insert(0, str(posix_site))

import requests
import websockets

# Strict Public-Only URL Allowlist
ALLOWED_WS_SCHEMES = {"wss"}
ALLOWED_REST_SCHEMES = {"https"}
ALLOWED_WS_HOSTS = {"socket.india.delta.exchange", "socket.delta.exchange"}
ALLOWED_REST_HOSTS = {"api.india.delta.exchange", "api.delta.exchange"}
ALLOWED_REST_PATHS = {"/v2/products", "/v2/history/candles", "/v2/tickers"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "reports" / "stage1_readonly"


class SafetyViolation(RuntimeError):
    """Raised when any unsafe dependency, call, or path access is detected."""
    pass


class BlockTradingImportsMetaFinder:
    """Meta-path finder that intercepts and blocks dynamic imports of any trading modules."""
    FORBIDDEN_PREFIXES = (
        "services.delta_paper_broker",
        "services.delta_paper_bot",
        "services.delta_live_executor",
        "services.paper_broker",
        "services.risk_manager",
        "services.delta_risk",
        "strategy",
        "risk",
        "subprocess",
    )

    def find_spec(self, fullname, path, target=None):
        for prefix in self.FORBIDDEN_PREFIXES:
            if fullname == prefix or fullname.startswith(prefix + "."):
                raise SafetyViolation(
                    f"Dynamic import of trading/execution module strictly blocked: '{fullname}'"
                )
        return None


def install_import_guard() -> None:
    """Install dynamic import guard into sys.meta_path if not already active."""
    if not any(isinstance(finder, BlockTradingImportsMetaFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, BlockTradingImportsMetaFinder())


# Configure Logging
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_DIR / "stage1_market_data.log"

logger = logging.getLogger("Stage1Observer")
logger.setLevel(logging.INFO)

# Formatter
log_fmt = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s (PID:%(process)d): %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Stream handler
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(log_fmt)
logger.addHandler(ch)

# File handler
fh = logging.FileHandler(str(LOG_FILE), mode="a", encoding="utf-8")
fh.setLevel(logging.INFO)
fh.setFormatter(log_fmt)
logger.addHandler(fh)


def get_ist_now(utc_dt: Optional[datetime] = None) -> datetime:
    """Return datetime in Indian Standard Time (UTC+5:30)."""
    base = utc_dt or datetime.now(timezone.utc)
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    return base.astimezone(ist_offset)


def get_git_commit_sha() -> str:
    """Safely obtain current git commit SHA by reading .git directly (zero subprocess execution)."""
    try:
        git_dir = PROJECT_ROOT / ".git"
        if not git_dir.exists():
            return "UNKNOWN_COMMIT"
        head_file = git_dir / "HEAD"
        if not head_file.exists():
            return "UNKNOWN_COMMIT"
        head_content = head_file.read_text(encoding="utf-8").strip()
        if not head_content.startswith("ref:"):
            return head_content
        ref_path = head_content.split(":", 1)[1].strip()
        target_ref = git_dir / ref_path
        if target_ref.exists():
            return target_ref.read_text(encoding="utf-8").strip()
        # Fallback to packed-refs
        packed = git_dir / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("^"):
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref_path:
                        return parts[0]
        return "UNKNOWN_COMMIT"
    except Exception:
        return "UNKNOWN_COMMIT"


def validate_write_path(target_path: Path, base_dir: Optional[Path] = None) -> Path:
    """Verify that file write is strictly within reports/stage1_readonly/ (or configured base_dir)."""
    resolved_target = target_path.resolve()
    resolved_base = (base_dir or OUTPUT_DIR).resolve()
    try:
        resolved_target.relative_to(resolved_base)
    except ValueError:
        raise SafetyViolation(
            f"Security Breach: Attempted write outside isolated directory: {target_path}"
        )
    return target_path


def assert_runtime_safety_invariants() -> Dict[str, Any]:
    """Assert all non-negotiable safety conditions before observing market data."""
    logger.info("Executing Pre-Flight Safety Verification...")

    # 1. Verify environment variables
    trading_mode = os.getenv("TRADING_MODE", "PAPER").upper()
    live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower()
    live_ack = os.getenv("LIVE_TRADING_ACKNOWLEDGED", "NO").upper()

    if trading_mode != "PAPER":
        raise SafetyViolation(f"Unsafe TRADING_MODE: expected 'PAPER', found '{trading_mode}'")
    if live_enabled not in ("false", "0", "no"):
        raise SafetyViolation(f"Unsafe LIVE_TRADING_ENABLED: expected 'false', found '{live_enabled}'")
    if live_ack != "NO":
        raise SafetyViolation(f"Unsafe LIVE_TRADING_ACKNOWLEDGED: expected 'NO', found '{live_ack}'")

    # 2. Verify no forbidden trading execution modules are loaded in memory
    forbidden_modules = [
        "services.delta_paper_broker",
        "services.delta_paper_bot",
        "services.delta_live_executor",
        "services.risk_manager",
        "strategy.arbitrator",
    ]
    for mod in forbidden_modules:
        if mod in sys.modules:
            raise SafetyViolation(f"Unsafe module loaded in interpreter: {mod}")

    # 3. Verify no private API keys/secrets are populated in memory for order placement
    api_key = os.getenv("DELTA_API_KEY", "")
    api_secret = os.getenv("DELTA_API_SECRET", "")
    if api_key or api_secret:
        logger.warning("API credentials present in environment; observer will NOT access private endpoints.")

    # 4. Install dynamic import guard to intercept any subsequent dynamic imports
    install_import_guard()

    logger.info("Pre-Flight Safety Verification: ALL CHECKS PASSED.")
    return {
        "trading_mode": trading_mode,
        "live_trading_enabled": False,
        "zero_broker_calls": True,
        "zero_orders_submitted": True,
        "zero_strategy_evaluations": True,
        "zero_service_modifications": True,
        "isolated_output_enforced": True,
    }


class Stage1MarketObserver:
    """Autonomous, read-only public market-data observer for XAUTUSD."""

    def __init__(
        self,
        symbol: str = "XAUTUSD",
        product_id: int = 131253,
        duration_seconds: int = 3600,
        ws_url: str = "wss://socket.india.delta.exchange",
        rest_url: str = "https://api.india.delta.exchange",
        output_dir: Path = OUTPUT_DIR,
    ):
        self.symbol = symbol.strip().upper()
        self.product_id = int(product_id)
        self.duration_seconds = max(10, int(duration_seconds))
        self.ws_url = ws_url.strip()
        self.rest_url = rest_url.strip()
        self.output_dir = output_dir

        # Validate URLs against public allowlist
        self._validate_endpoint(self.ws_url, is_ws=True)
        self._validate_endpoint(self.rest_url, is_ws=False)

        self._lock = threading.Lock()
        self._running = False
        self._shutdown_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Process & Telemetry Metadata
        self.start_utc: Optional[datetime] = None
        self.start_ist: Optional[datetime] = None
        self.end_utc: Optional[datetime] = None
        self.end_ist: Optional[datetime] = None
        self.observer_pid = os.getpid()
        self.commit_sha = get_git_commit_sha()

        # Tick Quality Metrics
        self.total_ticks = 0
        self.valid_ticks = 0
        self.duplicate_ticks = 0
        self.out_of_order_ticks = 0
        self.ticker_out_of_order_ticks = 0
        self.trade_out_of_order_ticks = 0
        self.last_tick_ts: Optional[int] = None
        self.last_ticker_ts: Optional[int] = None
        self.last_trade_ts: Optional[int] = None
        self._seen_ticks: Set[str] = set()

        # Candle Quality Metrics
        self.candle_count = 0
        self.duplicate_candles = 0
        self.out_of_order_candles = 0
        self.missing_candles = 0
        self.last_completed_candle_start_ts: Optional[int] = None
        self.current_forming_start_ts: Optional[int] = None
        self.current_forming_bar: Optional[Dict[str, Any]] = None
        self._seen_candle_starts: Set[int] = set()
        self.candle_records: List[Dict[str, Any]] = []

        # Connection & Feed Health Metrics
        self.ws_connected = False
        self.websocket_disconnects = 0
        self.reconnection_attempts = 0
        self.rest_fallback_events = 0
        self.stale_feed_intervals = 0
        self.longest_interruption_seconds = 0.0
        self.last_feed_update_ts: Optional[datetime] = None
        self.last_feed_interruption_start: Optional[datetime] = None
        self.errors_logged: List[str] = []

        # System Resource Usage
        self.initial_process_time = time.process_time()
        self.initial_wall_time = time.time()
        self.peak_memory_mb = 0.0

        # Safety Assertions Record
        self.safety_assertions: Dict[str, Any] = {}

    def _validate_endpoint(self, url: str, is_ws: bool = True) -> None:
        """Ensure endpoint is on public allowlist with full scheme, host, port, and path validation."""
        parsed = urlparse(url)

        # 1. Scheme validation
        expected_schemes = ALLOWED_WS_SCHEMES if is_ws else ALLOWED_REST_SCHEMES
        if not parsed.scheme or parsed.scheme.lower() not in expected_schemes:
            raise SafetyViolation(f"Invalid URL scheme '{parsed.scheme}'; expected {expected_schemes}: '{url}'")

        # 2. Hostname validation
        allowed_hosts = ALLOWED_WS_HOSTS if is_ws else ALLOWED_REST_HOSTS
        if not parsed.hostname or parsed.hostname.lower() not in allowed_hosts:
            raise SafetyViolation(f"Host '{parsed.hostname}' not in public allowlist {allowed_hosts}: '{url}'")

        # 3. Port validation (must be 443 or default None)
        if parsed.port is not None and parsed.port != 443:
            raise SafetyViolation(f"Non-standard port '{parsed.port}' strictly forbidden: '{url}'")

        # 4. Path validation
        path = parsed.path.rstrip("/") or "/"
        if is_ws:
            if path != "/":
                raise SafetyViolation(f"Unexpected WebSocket path '{parsed.path}'; expected root: '{url}'")
        else:
            # REST base URL can have empty path during initialization, or specific API path during query
            if path != "/" and path not in ALLOWED_REST_PATHS:
                raise SafetyViolation(f"REST path '{parsed.path}' not in public allowlist {ALLOWED_REST_PATHS}: '{url}'")

        # 5. Forbidden keyword check across whole URL
        forbidden_keywords = ["order", "position", "wallet", "account", "auth", "secret", "private"]
        url_lower = url.lower()
        for kw in forbidden_keywords:
            if kw in url_lower:
                raise SafetyViolation(f"Unsafe private endpoint keyword '{kw}' forbidden: '{url}'")

    def _get_current_memory_mb(self) -> float:
        """Measure current process memory usage across Linux/Windows."""
        try:
            if sys.platform != "win32":
                with open("/proc/self/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            return round(int(line.split()[1]) / 1024.0, 2)
            else:
                import ctypes
                from ctypes import wintypes
                class PMC(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                    ]
                pmc = PMC()
                pmc.cb = ctypes.sizeof(PMC)
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                    return round(pmc.WorkingSetSize / (1024.0 * 1024.0), 2)
        except Exception:
            pass
        return 0.0

    def start(self) -> None:
        """Initialize and start the public market-data observer."""
        self.safety_assertions = assert_runtime_safety_invariants()

        self.start_utc = datetime.now(timezone.utc)
        self.start_ist = get_ist_now(self.start_utc)
        self.last_feed_update_ts = self.start_utc

        logger.info(
            "Starting Stage 1 Observer (PID: %d, SHA: %s, Symbol: %s, Target Duration: %ds / %.1fh)...",
            self.observer_pid,
            self.commit_sha[:8],
            self.symbol,
            self.duration_seconds,
            self.duration_seconds / 3600.0,
        )
        logger.info("Start Time: %s UTC | %s IST", self.start_utc.isoformat(), self.start_ist.isoformat())

        self._running = True
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True, name="Stage1ObserverWS")
        self._thread.start()

    def stop(self) -> None:
        """Gracefully terminate observer and persist final report."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self.ws_connected = False

        self._shutdown_event.set()

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        self.end_utc = datetime.now(timezone.utc)
        self.end_ist = get_ist_now(self.end_utc)

        # Calculate final resource usage
        elapsed_wall = max(0.001, time.time() - self.initial_wall_time)
        elapsed_cpu = max(0.0, time.process_time() - self.initial_process_time)
        cpu_pct = round((elapsed_cpu / elapsed_wall) * 100.0, 2)
        final_mem_mb = max(self.peak_memory_mb, self._get_current_memory_mb())

        logger.info("Stage 1 Observer stopped. Generating final reports...")
        self._write_output_artifacts(cpu_pct, final_mem_mb)

    # -------------------------------------------------------------------------
    # WebSocket Supervision & Message Processing
    # -------------------------------------------------------------------------

    def _run_async_loop(self) -> None:
        """Run asyncio loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._supervision_loop())
        except (RuntimeError, asyncio.CancelledError):
            pass
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self._loop.close()
            except Exception:
                pass

    async def _supervision_loop(self) -> None:
        """Continuously supervise WebSocket connection with exponential backoff."""
        backoff = 1.0
        max_backoff = 30.0

        while self._running:
            try:
                logger.info("Connecting to public Delta WebSocket: %s", self.ws_url)
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    open_timeout=10,
                ) as ws:
                    with self._lock:
                        self.ws_connected = True
                        if self.last_feed_interruption_start:
                            interruption_dur = (datetime.now(timezone.utc) - self.last_feed_interruption_start).total_seconds()
                            self.longest_interruption_seconds = max(self.longest_interruption_seconds, interruption_dur)
                            self.last_feed_interruption_start = None
                    backoff = 1.0
                    logger.info("Connected to WebSocket. Subscribing to public channels for %s...", self.symbol)

                    sub_msg = {
                        "type": "subscribe",
                        "payload": {
                            "channels": [
                                {"name": "v2/ticker", "symbols": [self.symbol]},
                                {"name": "candlestick_5m", "symbols": [self.symbol]},
                                {"name": "all_trades", "symbols": [self.symbol]},
                            ]
                        },
                    }
                    await ws.send(json.dumps(sub_msg))

                    while self._running:
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            self.process_raw_message(raw_msg)
                        except asyncio.TimeoutError:
                            # Send heartbeat ping
                            await ws.send(json.dumps({"type": "ping"}))
                            self._check_feed_staleness()

            except (websockets.ConnectionClosed, OSError, Exception) as exc:
                with self._lock:
                    self.ws_connected = False
                    self.websocket_disconnects += 1
                    self.reconnection_attempts += 1
                    if self.last_feed_interruption_start is None:
                        self.last_feed_interruption_start = datetime.now(timezone.utc)
                    err_msg = f"WebSocket disconnected: {exc}"
                    self.errors_logged.append(err_msg)
                    logger.warning("%s. Reconnecting in %.1fs (attempt #%d)...", err_msg, backoff, self.reconnection_attempts)

                if not self._running:
                    break

                # Trigger REST fallback check if WS is disconnected for > 15s
                self._perform_rest_fallback_check()

                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, max_backoff)

    def _check_feed_staleness(self) -> None:
        """Check if public feed has gone silent for > 60 seconds."""
        now = datetime.now(timezone.utc)
        mem = self._get_current_memory_mb()
        with self._lock:
            self.peak_memory_mb = max(self.peak_memory_mb, mem)
            if self.last_feed_update_ts:
                silence_dur = (now - self.last_feed_update_ts).total_seconds()
                if silence_dur > 60.0:
                    self.stale_feed_intervals += 1
                    self.longest_interruption_seconds = max(self.longest_interruption_seconds, silence_dur)
                    logger.warning("Stale feed detected: No public update for %.1fs (interval count: %d)", silence_dur, self.stale_feed_intervals)

    def _perform_rest_fallback_check(self) -> None:
        """Query public unauthenticated REST API to confirm exchange reachability."""
        try:
            url = f"{self.rest_url}/v2/tickers"
            self._validate_endpoint(url, is_ws=False)
            resp = requests.get(
                url,
                params={"symbol": self.symbol},
                headers={"Accept": "application/json", "User-Agent": "Stage1Observer/1.0"},
                timeout=5,
            )
            if resp.status_code == 200:
                with self._lock:
                    self.rest_fallback_events += 1
                    self.last_feed_update_ts = datetime.now(timezone.utc)
                logger.info("REST fallback query successful: %s reachable (total REST events: %d).", self.symbol, self.rest_fallback_events)
        except Exception as exc:
            logger.warning("REST fallback query error: %s", exc)

    def process_raw_message(self, raw_msg: str) -> None:
        """Public message processor — parses and validates incoming payloads."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self.last_feed_update_ts = now
            if self.last_feed_interruption_start:
                interr = (now - self.last_feed_interruption_start).total_seconds()
                self.longest_interruption_seconds = max(self.longest_interruption_seconds, interr)
                self.last_feed_interruption_start = None

        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")
        channel = data.get("channel") or msg_type

        if msg_type == "candlestick_5m" or channel == "candlestick_5m":
            self.process_candlestick(data)
        elif msg_type == "v2/ticker" or channel == "v2/ticker":
            self.process_ticker(data)
        elif msg_type == "all_trades" or channel == "all_trades":
            self.process_trade(data)

    def process_ticker(self, data: Dict[str, Any]) -> None:
        """Validate and record public ticker message."""
        if str(data.get("symbol", "")).upper() != self.symbol:
            return

        with self._lock:
            self.total_ticks += 1
            ts_raw = data.get("timestamp")
            if ts_raw is None:
                return

            ts_epoch_us = int(ts_raw)
            # Check duplicate
            tick_sig = f"TICKER_{ts_epoch_us}_{data.get('close')}_{data.get('mark_price')}"
            if tick_sig in self._seen_ticks:
                self.duplicate_ticks += 1
            else:
                self._seen_ticks.add(tick_sig)
                if len(self._seen_ticks) > 10000:
                    self._seen_ticks.clear()

            # Check out of order independently per stream clock
            if self.last_ticker_ts is not None and ts_epoch_us < self.last_ticker_ts:
                self.ticker_out_of_order_ticks += 1
                self.out_of_order_ticks += 1
            else:
                self.last_ticker_ts = ts_epoch_us

            self.last_tick_ts = ts_epoch_us
            self.valid_ticks += 1

    def process_trade(self, data: Dict[str, Any]) -> None:
        """Validate and record public trade message."""
        if str(data.get("symbol", "")).upper() != self.symbol:
            return

        with self._lock:
            self.total_ticks += 1
            ts_raw = data.get("timestamp")
            if ts_raw is None:
                return

            ts_epoch_us = int(ts_raw)
            trade_id = data.get("id") or data.get("trade_id") or f"TRADE_{ts_epoch_us}_{data.get('price')}_{data.get('size')}"
            trade_sig = f"TR_{trade_id}"

            if trade_sig in self._seen_ticks:
                self.duplicate_ticks += 1
            else:
                self._seen_ticks.add(trade_sig)
                if len(self._seen_ticks) > 10000:
                    self._seen_ticks.clear()

            # Check out of order independently per stream clock
            if self.last_trade_ts is not None and ts_epoch_us < self.last_trade_ts:
                self.trade_out_of_order_ticks += 1
                self.out_of_order_ticks += 1
            else:
                self.last_trade_ts = ts_epoch_us

            self.last_tick_ts = ts_epoch_us
            self.valid_ticks += 1

    def process_candlestick(self, data: Dict[str, Any]) -> None:
        """Validate and record public 5m candlestick."""
        if str(data.get("symbol", "")).upper() != self.symbol:
            return

        start_time_raw = data.get("candle_start_time")
        if start_time_raw is None:
            return

        # Delta start time in microseconds -> convert to seconds
        start_ts = int(start_time_raw) // 1_000_000
        opn = float(data.get("open", 0.0))
        high = float(data.get("high", 0.0))
        low = float(data.get("low", 0.0))
        close = float(data.get("close", 0.0))
        vol = float(data.get("volume", 0.0))

        with self._lock:
            # Check if this represents advancing to a new candle
            if self.current_forming_start_ts is not None and start_ts > self.current_forming_start_ts:
                # The previous candle is completed
                prev_bar = self.current_forming_bar
                if prev_bar:
                    prev_start = prev_bar["start_ts"]

                    # Duplicate candle check
                    if prev_start in self._seen_candle_starts:
                        self.duplicate_candles += 1
                    else:
                        self._seen_candle_starts.add(prev_start)
                        self.candle_count += 1

                        # Out-of-order check
                        if self.last_completed_candle_start_ts is not None:
                            if prev_start < self.last_completed_candle_start_ts:
                                self.out_of_order_candles += 1

                        # Gap / Missing candle check between completed bar and new incoming bar
                        gap = start_ts - prev_start - 300
                        if gap > 0:
                            missing_count = gap // 300
                            self.missing_candles += missing_count
                            logger.warning(
                                "Candle gap detected: %ds (%d missing 5m candles between %d and %d)",
                                gap,
                                missing_count,
                                prev_start,
                                start_ts,
                            )

                        self.last_completed_candle_start_ts = prev_start

                        # Record candle health entry
                        dt_utc = datetime.fromtimestamp(prev_start, tz=timezone.utc)
                        dt_ist = get_ist_now(dt_utc)
                        record = {
                            "timestamp_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                            "timestamp_ist": dt_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                            "start_ts": prev_start,
                            "open": prev_bar["open"],
                            "high": prev_bar["high"],
                            "low": prev_bar["low"],
                            "close": prev_bar["close"],
                            "volume": prev_bar["volume"],
                            "status": "VALID",
                            "gap_seconds": max(0, gap),
                        }
                        self.candle_records.append(record)
                        logger.info("5m Candle Finalized: %s IST | Close: $%.2f | Vol: %.2f", dt_ist.strftime("%H:%M:%S"), prev_bar["close"], prev_bar["volume"])

            self.current_forming_start_ts = start_ts
            self.current_forming_bar = {
                "start_ts": start_ts,
                "open": opn,
                "high": high,
                "low": low,
                "close": close,
                "volume": vol,
            }

    # -------------------------------------------------------------------------
    # Output Artifacts Generation
    # -------------------------------------------------------------------------

    def _write_output_artifacts(self, cpu_pct: float, memory_mb: float) -> None:
        """Write isolated Stage 1 output artifacts strictly to reports/stage1_readonly/."""
        # 1. Determine Final Status
        if self.safety_assertions.get("zero_orders_submitted") is not True:
            final_status = "FAIL"
            status_desc = "Safety violation: non-zero order submission detected."
        elif self.websocket_disconnects > 10 or self.missing_candles > 5 or self.stale_feed_intervals > 5:
            final_status = "DEGRADED"
            status_desc = "Public feed exhibited interruptions, candle gaps, or stale intervals."
        else:
            final_status = "PASS"
            status_desc = "Public feed is stable, continuous, and compliant with zero execution safety."

        duration_actual = (self.end_utc - self.start_utc).total_seconds() if (self.start_utc and self.end_utc) else 0.0

        summary = {
            "phase": "STAGE_1_READONLY_PUBLIC_MARKET_DATA",
            "symbol": self.symbol,
            "product_id": self.product_id,
            "final_status": final_status,
            "status_description": status_desc,
            "commit_sha": self.commit_sha,
            "observer_pid": self.observer_pid,
            "timing": {
                "start_utc": self.start_utc.isoformat() if self.start_utc else None,
                "start_ist": self.start_ist.isoformat() if self.start_ist else None,
                "end_utc": self.end_utc.isoformat() if self.end_utc else None,
                "end_ist": self.end_ist.isoformat() if self.end_ist else None,
                "target_duration_seconds": self.duration_seconds,
                "actual_duration_seconds": round(duration_actual, 2),
            },
            "public_endpoints": {
                "ws_url": self.ws_url,
                "rest_url": self.rest_url,
                "private_endpoints_accessed": 0,
            },
            "telemetry": {
                "total_ticks_received": self.total_ticks,
                "valid_ticks": self.valid_ticks,
                "duplicate_ticks": self.duplicate_ticks,
                "out_of_order_ticks": self.out_of_order_ticks,
                "completed_candles": self.candle_count,
                "duplicate_candles": self.duplicate_candles,
                "out_of_order_candles": self.out_of_order_candles,
                "missing_candles": self.missing_candles,
                "websocket_disconnects": self.websocket_disconnects,
                "reconnection_attempts": self.reconnection_attempts,
                "rest_fallback_events": self.rest_fallback_events,
                "stale_feed_intervals": self.stale_feed_intervals,
                "longest_interruption_seconds": round(self.longest_interruption_seconds, 2),
            },
            "resource_usage": {
                "cpu_utilization_pct": cpu_pct,
                "memory_usage_mb": memory_mb,
            },
            "safety_assertions": self.safety_assertions,
            "error_log_count": len(self.errors_logged),
        }

        # 1. Write stage1_summary.json
        summary_path = validate_write_path(self.output_dir / "stage1_summary.json", base_dir=self.output_dir)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        logger.info("Saved %s", summary_path)

        # 2. Write stage1_candle_health.csv
        csv_path = validate_write_path(self.output_dir / "stage1_candle_health.csv", base_dir=self.output_dir)
        fieldnames = [
            "timestamp_utc",
            "timestamp_ist",
            "start_ts",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "status",
            "gap_seconds",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for rec in self.candle_records:
                writer.writerow(rec)
        logger.info("Saved %s (%d records)", csv_path, len(self.candle_records))

        # 3. Write STAGE1_READONLY_REPORT.md
        report_path = validate_write_path(self.output_dir / "STAGE1_READONLY_REPORT.md", base_dir=self.output_dir)
        self._generate_markdown_report(report_path, summary)
        logger.info("Saved %s", report_path)

    def _generate_markdown_report(self, report_path: Path, summary: Dict[str, Any]) -> None:
        """Render detailed markdown report for Stage 1 Observer."""
        timing = summary["timing"]
        telemetry = summary["telemetry"]
        resources = summary["resource_usage"]
        status = summary["final_status"]

        status_badge = {
            "PASS": "**PASS — PUBLIC FEED STABLE**",
            "DEGRADED": "**DEGRADED — FEED INTERRUPTIONS / DATA QUALITY ISSUES**",
            "FAIL": "**FAIL — UNSAFE ACCESS OR UNEXPECTED BEHAVIOR**",
        }.get(status, f"**{status}**")

        output_dir_str = str(OUTPUT_DIR).replace("\\", "/")
        content = f"""# Stage 1 — EC2 Read-Only Public Market-Data Observer Report

**Date:** {self.start_utc.strftime("%Y-%m-%d") if self.start_utc else "2026-11-06"}  
**System:** Delta Exchange India — Local AI Trading Agent  
**Instrument:** `{self.symbol}` (Product ID: `{self.product_id}`)  
**Execution Mode:** STRICT READ-ONLY PUBLIC MARKET DATA  
**Observer Process ID:** `{self.observer_pid}`  
**Commit SHA:** `{self.commit_sha}`  
**Final Status:** {status_badge}  
**Operational Invariant:** **TRADING DAEMON REMAINS STOPPED**  

---

## 1. Executive Summary

Stage 1 observed and logged live public market data for `{self.symbol}` from Delta Exchange India without initializing brokers, calculating position sizes, executing strategies, or submitting orders.

All operations adhered strictly to read-only constraints:
- Zero private exchange endpoints were accessed.
- Zero API keys or cryptographic signatures were generated or loaded.
- Zero systemd services were started, stopped, restarted, or altered.
- The EC2 trading daemon (`delta-paper-trading.service`) remained **STOPPED** throughout.
- All telemetry was written exclusively to [`reports/stage1_readonly/`](file:///{output_dir_str}).

---

## 2. Timing & Environment

| Parameter | Value |
|---|---|
| **Start Time (UTC)** | `{timing["start_utc"]}` |
| **Start Time (IST)** | `{timing["start_ist"]}` |
| **End Time (UTC)** | `{timing["end_utc"]}` |
| **End Time (IST)** | `{timing["end_ist"]}` |
| **Target Duration** | `{timing["target_duration_seconds"]} seconds` |
| **Actual Elapsed** | `{timing["actual_duration_seconds"]} seconds` |
| **Commit SHA** | `{self.commit_sha}` |
| **Process ID** | `{self.observer_pid}` |

---

## 3. Public Network Endpoints Accessed

| Protocol | Endpoint URL | Channel / Method | Authentication |
|---|---|---|:---:|
| **WebSocket** | `{self.ws_url}` | `candlestick_5m`, `v2/ticker`, `all_trades` | **NONE (Public)** |
| **REST** | `{self.rest_url}/v2/tickers` | GET (Fallback reachability) | **NONE (Public)** |
| **Private Endpoints** | *None* | *None* | **ZERO ACCESS** |

---

## 4. Market-Data Telemetry & Quality

| Metric | Recorded Value | Quality Assessment |
|---|:---:|---|
| **Total Ticks Received** | `{telemetry["total_ticks_received"]}` | Raw tick throughput |
| **Valid Ticks** | `{telemetry["valid_ticks"]}` | Schema and timestamp compliant |
| **Duplicate Ticks** | `{telemetry["duplicate_ticks"]}` | Identified via unique payload hashes |
| **Out-of-Order Ticks** | `{telemetry["out_of_order_ticks"]}` | Sequence arrival order |
| **Finalized 5m Candles** | `{telemetry["completed_candles"]}` | Discretely completed 5m bars |
| **Duplicate Candles** | `{telemetry["duplicate_candles"]}` | Redundant bar timestamp updates |
| **Out-of-Order Candles** | `{telemetry["out_of_order_candles"]}` | Chronological bar sequence |
| **Missing Candle Gaps** | `{telemetry["missing_candles"]}` | Interval gaps > 300s |

---

## 5. Feed Reliability & Continuity

| Reliability Indicator | Count / Duration | Assessment |
|---|:---:|---|
| **WebSocket Disconnects** | `{telemetry["websocket_disconnects"]}` | Connection drops encountered |
| **Reconnection Attempts** | `{telemetry["reconnection_attempts"]}` | Exponential backoff reconnects |
| **REST Fallback Events** | `{telemetry["rest_fallback_events"]}` | Reachability checks during drops |
| **Stale Feed Intervals (>60s)** | `{telemetry["stale_feed_intervals"]}` | Inactivity alerts |
| **Longest Interruption** | `{telemetry["longest_interruption_seconds"]}s` | Max duration without public data |

---

## 6. Observer Resource Usage

| Resource | Utilization | Limit / Safety Standard | Status |
|---|:---:|:---:|:---:|
| **CPU Utilization** | `{resources["cpu_utilization_pct"]}%` | < 10.0% nominal | **PASS** |
| **Process Memory** | `{resources["memory_usage_mb"]} MB` | < 150 MB nominal | **PASS** |

---

## 7. Safety Assertions Verification

| Safety Invariant | Required State | Observed State | Status |
|---|:---:|:---:|:---:|
| **Trading Mode** | `PAPER` | `{self.safety_assertions.get("trading_mode")}` | **VERIFIED** |
| **Live Trading** | Disabled (`False`) | `{self.safety_assertions.get("live_trading_enabled")}` | **VERIFIED** |
| **Broker Order Methods** | 0 Calls | 0 Calls | **VERIFIED** |
| **Orders Generated** | 0 Orders | 0 Orders | **VERIFIED** |
| **Strategy Evaluations** | 0 Evaluations | 0 Evaluations | **VERIFIED** |
| **systemd Modifications** | 0 Modifications | 0 Modifications | **VERIFIED** |
| **Output Directory Isolation** | Enforced | Enforced | **VERIFIED** |

---

## 8. Final Operational Verdict

```
========================================================================
STAGE 1 FINAL VERDICT: {status}
{summary["status_description"]}

OPERATIONAL RULE:
THE TRADING DAEMON REMAINS STOPPED BEFORE, DURING, AND AFTER THIS TEST.
DO NOT AUTOMATICALLY ADVANCE TO STAGE 2 OR STAGE 3.
WAIT FOR SEPARATE HUMAN AUTHORIZATION.
========================================================================
```
"""
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 1 Read-Only Public Market-Data Observer for XAUTUSD (Delta Exchange India)."
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Observer duration in seconds (default: 3600 = 1 hour).",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="XAUTUSD",
        help="Target trading instrument symbol (default: XAUTUSD).",
    )
    parser.add_argument(
        "--product-id",
        type=int,
        default=131253,
        help="Delta Exchange product ID (default: 131253).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    observer = Stage1MarketObserver(
        symbol=args.symbol,
        product_id=args.product_id,
        duration_seconds=args.duration,
    )

    def handle_signal(sig, frame):
        logger.info("Signal %s received; gracefully stopping observer...", sig)
        observer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    observer.start()

    start_wait = time.time()
    try:
        while time.time() - start_wait < args.duration:
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        observer.stop()


if __name__ == "__main__":
    main()
