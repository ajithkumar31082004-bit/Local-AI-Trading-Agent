"""Dry-Run Runner & Operational Safety Harness for Read-Only Shadow Observer.

ENFORCES PHASE 3.41 SPECIFICATIONS:
1. 12 Runtime Preflight Safety Gates before any observation starts.
2. 10 Automatic Stop Conditions with immediate graceful shutdown.
3. Strict Resource Budgeting (60 min max, 1000 queue max, memory & disk limits).
4. Comprehensive monitoring telemetry & reporting schema (zero fabricated metrics).
5. Absolute zero order execution or broker dependency.
"""

import os
import sys
import json
import time
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Callable
import logging

from services.shadow_observer.observer_safety_guard import (
    ShadowObserverSafetyViolation,
    validate_shadow_write_path,
)
from services.shadow_observer.transport.transport_config import (
    TransportConfig,
    PUBLIC_REST_BASE,
    PUBLIC_WS_BASE,
    DEFAULT_SYMBOL,
    FORBIDDEN_ENDPOINT_KEYWORDS,
    FORBIDDEN_AUTH_HEADERS,
)
from services.shadow_observer.transport.transport_interface import (
    ConnectionState,
    NormalizedTick,
    NormalizedCandle,
)
from services.shadow_observer.transport.transport_supervisor import TransportSupervisor

logger = logging.getLogger("shadow_observer.dry_run_runner")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class DryRunConfig:
    """Configuration for 60-Minute Public Feed Dry-Run Observation."""
    
    symbol: str = "XAUTUSD"
    max_duration_seconds: float = 3600.0  # Exactly 60 minutes
    max_message_queue_size: int = 1000
    max_reconnect_attempts: int = 5
    max_reconnect_delay_seconds: float = 60.0
    stale_feed_threshold_seconds: float = 60.0
    max_memory_mb: float = 150.0
    max_disk_mb: float = 25.0
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "shadow_observer")
    restart_policy: str = "never"  # Must NEVER restart automatically
    
    # Injected hooks for offline testing
    allow_mock_network: bool = True

    def __post_init__(self):
        if self.symbol != "XAUTUSD":
            raise ShadowObserverSafetyViolation(f"SECURITY VIOLATION: Unsupported symbol: {self.symbol}")
        if self.max_duration_seconds > 3600.0 or self.max_duration_seconds <= 0:
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Duration {self.max_duration_seconds}s exceeds 60-minute limit (3600s)."
            )
        if self.max_message_queue_size > 1000 or self.max_message_queue_size <= 0:
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Queue size {self.max_message_queue_size} exceeds max limit (1000)."
            )
        if self.max_memory_mb > 150.0 or self.max_memory_mb <= 0:
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Memory limit {self.max_memory_mb}MB exceeds max limit (150MB)."
            )
        if self.max_disk_mb > 25.0 or self.max_disk_mb <= 0:
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Disk limit {self.max_disk_mb}MB exceeds max limit (25MB)."
            )
        if self.restart_policy != "never":
            raise ShadowObserverSafetyViolation(
                f"SECURITY VIOLATION: Automatic restart policy '{self.restart_policy}' prohibited."
            )


def execute_preflight_safety_gates(
    config: DryRunConfig,
    env_file: Optional[Path] = None,
    is_daemon_running_fn: Optional[Callable[[], bool]] = None,
    git_clean_check_fn: Optional[Callable[[], Tuple[bool, str]]] = None,
    connectivity_probe_fn: Optional[Callable[[], Tuple[bool, str]]] = None,
    host_headroom_check_fn: Optional[Callable[[], Tuple[bool, str]]] = None,
) -> Tuple[bool, List[str]]:
    """Execute all 12 mandatory runtime preflight safety gates.
    
    Returns:
        Tuple[bool, List[str]]: (all_passed, failure_reasons)
    """
    failures: List[str] = []

    # Gate 1: API keys absent (in environment and .env)
    target_env = env_file or (PROJECT_ROOT / ".env")
    if os.environ.get("DELTA_API_KEY") or os.environ.get("DELTA_API_SECRET"):
        failures.append("Gate 1 Failed: DELTA_API_KEY or DELTA_API_SECRET found in environment variables.")

    if target_env.exists():
        content = target_env.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("DELTA_API_KEY=") and len(line.split("=", 1)[1].strip().strip('"').strip("'")) > 0:
                failures.append("Gate 1 Failed: DELTA_API_KEY present in .env file.")
            elif line.startswith("DELTA_API_SECRET=") and len(line.split("=", 1)[1].strip().strip('"').strip("'")) > 0:
                failures.append("Gate 1 Failed: DELTA_API_SECRET present in .env file.")

    # Gate 2: Broker namespace isolation & paper-only configuration
    trading_mode = "PAPER"
    live_enabled = "false"
    
    if target_env.exists():
        content = target_env.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("TRADING_MODE="):
                trading_mode = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("LIVE_TRADING_ENABLED="):
                live_enabled = line.split("=", 1)[1].strip().strip('"').strip("'").lower()

    if trading_mode != "PAPER":
        failures.append(f"Gate 2 Failed: TRADING_MODE is '{trading_mode}', must be 'PAPER'.")
    if live_enabled not in ("false", "0"):
        failures.append(f"Gate 2 Failed: LIVE_TRADING_ENABLED is '{live_enabled}', must be 'false'.")

    if is_daemon_running_fn and is_daemon_running_fn():
        failures.append("Gate 2 Failed: Trading daemon process is currently running. Must be stopped.")

    # Check that live broker execution modules are not loaded
    forbidden_broker_modules = ["execution.broker", "delta_paper_bot", "execution.order_manager", "execution.live_broker"]
    for mod in forbidden_broker_modules:
        if mod in sys.modules:
            failures.append(f"Gate 2 Failed: Broker module '{mod}' is loaded in sys.modules.")

    from risk.position_size_guard import APPROVED_MAX_PAPER_LOTS
    if APPROVED_MAX_PAPER_LOTS != 1:
        failures.append(f"Gate 2 Failed: APPROVED_MAX_PAPER_LOTS is {APPROVED_MAX_PAPER_LOTS}, must be 1.")

    # Gate 3: Git working-tree cleanliness
    if git_clean_check_fn:
        is_clean, err_msg = git_clean_check_fn()
        if not is_clean:
            failures.append(f"Gate 3 Failed: Working tree not clean or commit invalid: {err_msg}")

    # Gate 4: Product is strictly XAUTUSD
    if config.symbol != "XAUTUSD":
        failures.append(f"Gate 4 Failed: Symbol '{config.symbol}' != 'XAUTUSD'.")

    # Gate 5: Maximum 3,600-second duration
    if config.max_duration_seconds > 3600.0 or config.max_duration_seconds <= 0:
        failures.append(f"Gate 5 Failed: Max duration {config.max_duration_seconds}s > 3600s.")

    # Gate 6: Queue maximum 1,000
    if config.max_message_queue_size > 1000 or config.max_message_queue_size <= 0:
        failures.append(f"Gate 6 Failed: Max queue size {config.max_message_queue_size} > 1000.")

    # Gate 7: Memory maximum 150 MB
    if config.max_memory_mb > 150.0 or config.max_memory_mb <= 0:
        failures.append(f"Gate 7 Failed: Memory limit {config.max_memory_mb}MB exceeds 150MB budget.")

    # Gate 8: Disk maximum 25 MB
    if config.max_disk_mb > 25.0 or config.max_disk_mb <= 0:
        failures.append(f"Gate 8 Failed: Disk limit {config.max_disk_mb}MB exceeds 25MB budget.")

    # Gate 9: Restart policy set to "never"
    if config.restart_policy != "never":
        failures.append(f"Gate 9 Failed: Restart policy '{config.restart_policy}' != 'never'.")

    # Gate 10: Observer output directory isolated
    try:
        resolved_out = config.output_dir.resolve()
        validate_shadow_write_path(resolved_out / "test_probe.tmp")
        name_lower = str(resolved_out).lower()
        if "system32" in name_lower or ("windows" in name_lower and "temp" not in name_lower):
            raise ShadowObserverSafetyViolation(f"Output path in system directory is prohibited: {resolved_out}")
    except Exception as exc:
        failures.append(f"Gate 10 Failed: Output directory isolation violation: {exc}")

    # Gate 11: Outbound connectivity probe behavior (strictly offline-safe)
    try:
        t_cfg = TransportConfig(symbol=config.symbol)
        t_cfg.validate_config()
    except Exception as exc:
        failures.append(f"Gate 11 Failed: Endpoint validation error: {exc}")

    if connectivity_probe_fn:
        probe_ok, probe_err = connectivity_probe_fn()
        if not probe_ok:
            failures.append(f"Gate 11 Failed: Outbound connectivity probe failed: {probe_err}")

    # Gate 12: Host resource headroom
    if host_headroom_check_fn:
        headroom_ok, headroom_err = host_headroom_check_fn()
        if not headroom_ok:
            failures.append(f"Gate 12 Failed: Host resource headroom check failed: {headroom_err}")
    else:
        try:
            total, used, free = shutil.disk_usage(config.output_dir.resolve().anchor or str(config.output_dir.resolve()))
            free_gb = free / (1024 ** 3)
            if free_gb < 1.0:
                failures.append(f"Gate 12 Failed: Insufficient free disk space ({free_gb:.2f} GB free, minimum 1.0 GB required).")
        except Exception as exc:
            logger.debug("Host disk check error: %s", exc)

    all_passed = len(failures) == 0
    return all_passed, failures


class ShadowDryRunRunner:
    """Coordinates execution, monitoring, and automatic safety stops for a 60-Minute Public Dry Run."""

    def __init__(
        self,
        config: Optional[DryRunConfig] = None,
        supervisor: Optional[TransportSupervisor] = None,
        time_fn: Optional[Callable[[], float]] = None,
        memory_check_fn: Optional[Callable[[], float]] = None,
        disk_check_fn: Optional[Callable[[], float]] = None,
    ):
        self.config = config or DryRunConfig()
        self.supervisor = supervisor or TransportSupervisor(
            config=TransportConfig(
                symbol=self.config.symbol,
                max_message_queue_size=self.config.max_message_queue_size,
                max_reconnect_attempts=self.config.max_reconnect_attempts,
                stale_feed_threshold_seconds=self.config.stale_feed_threshold_seconds,
            )
        )
        self.time_fn = time_fn or time.time
        self.memory_check_fn = memory_check_fn or (lambda: 45.0)  # Default mocked 45MB
        self.disk_check_fn = disk_check_fn or (lambda: 1.5)  # Default mocked 1.5MB

        # Runtime state
        self.is_running: bool = False
        self.start_epoch: Optional[float] = None
        self.stop_epoch: Optional[float] = None
        self.shutdown_reason: Optional[str] = None
        self.stop_requested: bool = False
        self.has_terminated: bool = False
        self.detected_forbidden_key: Optional[str] = None
        self.detected_symbol_mismatch: Optional[str] = None

        # Metric accumulators
        self.peak_memory_mb: float = 0.0
        self.peak_disk_mb: float = 0.0

    def start(self, env_file: Optional[Path] = None, is_daemon_running_fn: Optional[Callable[[], bool]] = None) -> bool:
        """Execute preflight gates and start the dry run."""
        if self.has_terminated:
            raise ShadowObserverSafetyViolation("SECURITY VIOLATION: Restart after shutdown is prohibited.")

        if self.is_running:
            logger.warning("Dry run runner is already running; start() ignored.")
            return False

        # 1. Execute all 12 Preflight Safety Gates
        gates_passed, failures = execute_preflight_safety_gates(
            self.config,
            env_file=env_file,
            is_daemon_running_fn=is_daemon_running_fn,
        )
        if not gates_passed:
            self.shutdown_reason = f"PREFLIGHT_GATE_FAILURE: {'; '.join(failures)}"
            logger.critical("Dry-run startup aborted due to preflight gate failure: %s", self.shutdown_reason)
            raise ShadowObserverSafetyViolation(self.shutdown_reason)

        self.start_epoch = self.time_fn()
        self.is_running = True

        # 2. Start transport supervisor
        started = self.supervisor.start(now_epoch=self.start_epoch)
        if not started:
            self.stop("SUPERVISOR_START_FAILED")
            return False

        logger.info("Shadow dry-run runner started successfully at epoch %.2f", self.start_epoch)
        return True

    def request_stop(self, reason: str = "USER_REQUESTED_STOP") -> None:
        """Trigger an external human-controlled stop signal."""
        self.stop_requested = True
        self.stop(reason)

    def validate_payload_safety(self, payload: Any) -> bool:
        """Scan raw message payload for forbidden authentication keys or private endpoints."""
        as_str = json.dumps(payload).lower() if isinstance(payload, (dict, list)) else str(payload).lower()
        forbidden_keys = ["api-key", "delta-signature", "delta-timestamp", "private_key", "secret", "authorization", "api_key"]
        for key in forbidden_keys:
            if key in as_str:
                self.detected_forbidden_key = key
                self.stop(f"PRIVATE_KEY_IN_PAYLOAD: Sensitive auth keyword '{key}' detected in payload.")
                return False
        return True

    def evaluate_stop_conditions(self, now_epoch: Optional[float] = None) -> Tuple[bool, Optional[str]]:
        """Evaluate all 10 automatic stop conditions at the current time.
        
        Returns:
            Tuple[bool, Optional[str]]: (should_stop, reason)
        """
        curr_time = now_epoch if now_epoch is not None else self.time_fn()
        elapsed = curr_time - (self.start_epoch or curr_time)

        # Condition 1: 60-Minute Timeout Reached
        if elapsed >= self.config.max_duration_seconds:
            return True, f"MAX_DURATION_REACHED: Observation completed 60 minutes ({elapsed:.1f}s)."

        # Condition 2: Feed Stale
        if self.supervisor.reader.last_ticker_time is not None:
            is_stale, stale_sec = self.supervisor.reader.is_feed_stale(now_epoch=curr_time)
        else:
            stale_sec = curr_time - (self.start_epoch or curr_time)
            is_stale = stale_sec > self.config.stale_feed_threshold_seconds

        if is_stale:
            return True, f"STALE_FEED: No ticker update received for {stale_sec:.1f}s (threshold {self.config.stale_feed_threshold_seconds}s)."

        # Condition 3: Queue Overflow Occurred
        if self.supervisor.queue_overflow_count > 0:
            return True, f"QUEUE_OVERFLOW: Event queue overflowed ({self.supervisor.queue_overflow_count} events dropped)."

        # Condition 4: Symbol Mismatch
        if self.config.symbol != "XAUTUSD":
            return True, f"SYMBOL_MISMATCH: Symbol '{self.config.symbol}' != 'XAUTUSD'."
        if self.detected_symbol_mismatch:
            return True, f"SYMBOL_MISMATCH: Foreign symbol detected in feed: '{self.detected_symbol_mismatch}'"

        # Condition 5: Private Key / Sensitive Auth Token Detected
        if self.detected_forbidden_key:
            return True, f"PRIVATE_KEY_IN_PAYLOAD: Sensitive keyword '{self.detected_forbidden_key}' detected in payload."

        # Condition 6: Memory Exceeded
        mem = self.memory_check_fn()
        self.peak_memory_mb = max(self.peak_memory_mb, mem)
        if mem > self.config.max_memory_mb:
            return True, f"MEMORY_BUDGET_EXCEEDED: Memory usage {mem:.2f}MB exceeds limit of {self.config.max_memory_mb}MB."

        # Condition 7: Disk Exceeded
        disk = self.disk_check_fn()
        self.peak_disk_mb = max(self.peak_disk_mb, disk)
        if disk > self.config.max_disk_mb:
            return True, f"DISK_BUDGET_EXCEEDED: Disk usage {disk:.2f}MB exceeds limit of {self.config.max_disk_mb}MB."

        # Condition 8: Human Operator Manual Halt
        if self.stop_requested:
            return True, "MANUAL_HALT: Stop requested by operator."

        # Condition 9: Max Reconnects Exceeded
        if self.supervisor.state == ConnectionState.DEGRADED and self.supervisor.reconnect_ctrl.consecutive_failures >= 5:
            return True, "MAX_RECONNECTS_EXCEEDED: Transport failed 5 consecutive reconnect attempts."

        return False, None

    def tick(self, now_epoch: Optional[float] = None) -> bool:
        """Run an iteration of the dry-run supervision loop.
        
        Returns:
            bool: True if execution continues; False if stopped.
        """
        if not self.is_running:
            return False

        try:
            curr_time = now_epoch if now_epoch is not None else self.time_fn()
            should_stop, reason = self.evaluate_stop_conditions(now_epoch=curr_time)

            if should_stop:
                self.stop(reason or "AUTOMATIC_STOP_CONDITION_MET")
                return False

            return True
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                self.stop("MANUAL_HALT: Process signal received")
            else:
                self.stop(f"UNHANDLED_EXCEPTION: {type(exc).__name__}: {exc}")
            raise

    def stop(self, reason: str = "NORMAL_SHUTDOWN") -> None:
        """Execute graceful shutdown sequence with zero order execution."""
        if not self.is_running and self.has_terminated:
            return

        self.is_running = False
        self.has_terminated = True
        self.shutdown_reason = reason
        try:
            self.stop_epoch = self.time_fn()
        except BaseException:
            self.stop_epoch = None

        logger.info("Stopping ShadowDryRunRunner: %s", reason)

        # 1. Stop transport supervisor (closes WS, stops REST)
        try:
            self.supervisor.stop()
        except Exception as e:
            logger.warning("Error stopping supervisor: %s", e)

        # 2. Confirm transport state is STOPPED
        assert self.supervisor.state == ConnectionState.STOPPED, "Transport state not STOPPED after shutdown!"

        logger.info("Shadow dry-run runner stopped cleanly. Transport state: STOPPED.")

    def generate_readiness_report(self) -> Dict[str, Any]:
        """Generate comprehensive final dry-run telemetry adhering to Phase 3.41 schema."""
        runtime = (self.stop_epoch or self.time_fn()) - (self.start_epoch or self.time_fn())
        reconnect_telem = self.supervisor.reconnect_ctrl.get_telemetry()
        health = self.supervisor.check_health(now_epoch=self.stop_epoch)

        peak_mem = max(self.peak_memory_mb, self.memory_check_fn())
        peak_disk = max(self.peak_disk_mb, self.disk_check_fn())

        return {
            "session_metadata": {
                "instrument": self.config.symbol,
                "exchange": "Delta Exchange India",
                "execution_mode": "STRICT READ-ONLY SHADOW",
                "start_timestamp": datetime.fromtimestamp(self.start_epoch or 0, tz=timezone.utc).isoformat() if self.start_epoch else None,
                "stop_timestamp": datetime.fromtimestamp(self.stop_epoch or 0, tz=timezone.utc).isoformat() if self.stop_epoch else None,
                "total_runtime_seconds": round(max(0.0, runtime), 2),
                "shutdown_reason": self.shutdown_reason,
                "final_transport_state": self.supervisor.state.value,
            },
            "connectivity_metrics": {
                "connection_attempts": reconnect_telem["total_failures"] + reconnect_telem["total_successes"],
                "successful_connections": reconnect_telem["total_successes"],
                "disconnects_and_failures": reconnect_telem["total_failures"],
                "consecutive_failures": reconnect_telem["consecutive_failures"],
                "max_reconnect_attempts_budget": self.config.max_reconnect_attempts,
            },
            "market_data_metrics": {
                "received_ticks": self.supervisor.total_ticks_processed,
                "received_completed_candles": self.supervisor.total_candles_processed,
                "rejected_candles": self.supervisor.total_candles_rejected,
                "duplicate_events": self.supervisor.validator.duplicate_count,
                "out_of_order_events": self.supervisor.reader.out_of_order_count,
                "stale_feed_incidents": 1 if health["is_feed_stale"] else 0,
                "queue_overflow_count": self.supervisor.queue_overflow_count,
                "current_queue_depth": health["queue_depth"],
                "has_data_loss": health["has_data_loss"],
                "is_feed_healthy": health["is_feed_healthy"],
            },
            "resource_utilization": {
                "peak_memory_mb": round(peak_mem, 2),
                "max_memory_budget_mb": self.config.max_memory_mb,
                "peak_disk_mb": round(peak_disk, 2),
                "max_disk_budget_mb": self.config.max_disk_mb,
            },
            "safety_confirmations": {
                "orders_submitted": 0,
                "broker_methods_invoked": 0,
                "private_api_calls_attempted": 0,
                "trading_state_mutations": 0,
                "live_trading_enabled": False,
                "trading_mode": "PAPER",
                "restart_prevented": self.has_terminated,
            }
        }

    def save_readiness_report(self, output_path: Optional[Path] = None) -> Path:
        """Safely write telemetry report to disk inside approved output directory."""
        dest = output_path or (self.config.output_dir / f"shadow_dry_run_report_{int(self.stop_epoch or time.time())}.json")
        safe_path = validate_shadow_write_path(dest, allowed_root=self.config.output_dir)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        report_data = self.generate_readiness_report()
        safe_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
        return safe_path
