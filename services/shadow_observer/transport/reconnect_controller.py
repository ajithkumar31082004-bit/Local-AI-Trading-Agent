"""Exponential Backoff and Reconnection Controller for Public Transport.

RESPONSIBILITIES:
1. Calculates exponential backoff delays with bounded maximums.
2. Applies jitter to prevent herd reconnection effects.
3. Enforces bounded retry limits to prevent infinite tight loops.
4. Maintains a strictly bounded in-memory audit log of reconnection attempts.
"""

from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import math
import random
import logging

from services.shadow_observer.transport.transport_config import TransportConfig

logger = logging.getLogger("shadow_observer.transport.reconnect")


class ReconnectController:
    """Manages reconnect pacing, exponential backoff, jitter, and retry limits."""

    def __init__(self, config: Optional[TransportConfig] = None):
        self.config = config or TransportConfig()
        self.consecutive_failures: int = 0
        self.total_failures: int = 0
        self.total_successes: int = 0
        self.last_failure_time: Optional[float] = None
        self.next_allowed_attempt: Optional[float] = None
        self.reconnect_history: deque = deque(maxlen=self.config.max_reconnect_history)

    def compute_backoff(self, attempt_number: int) -> float:
        """Compute exponential backoff delay with jitter."""
        if attempt_number <= 0:
            return 0.0

        # Base exponential calculation: initial * (factor ^ (attempt - 1))
        exponent = min(attempt_number - 1, 10)  # Bound power to prevent float overflow
        base_delay = self.config.initial_backoff_seconds * (self.config.backoff_factor ** exponent)
        capped_delay = min(base_delay, self.config.max_backoff_seconds)

        # Apply jitter (+/- jitter_ratio)
        if self.config.jitter_ratio > 0:
            jitter_range = capped_delay * self.config.jitter_ratio
            jitter = random.uniform(-jitter_range, jitter_range)
            final_delay = max(0.1, capped_delay + jitter)
        else:
            final_delay = max(0.1, capped_delay)

        return round(final_delay, 3)

    def record_failure(self, reason: str, now_epoch: Optional[float] = None) -> Tuple[bool, float]:
        """Record a connection failure and calculate next retry schedule.
        
        Returns:
            Tuple[bool, float]: (can_retry, backoff_seconds)
        """
        curr_time = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure_time = curr_time

        # Check maximum retry bound
        if self.consecutive_failures > self.config.max_reconnect_attempts:
            logger.error(
                "Max reconnect attempts (%d) exceeded. Connection aborted. Reason: %s",
                self.config.max_reconnect_attempts, reason
            )
            self.reconnect_history.append({
                "timestamp": curr_time,
                "event": "MAX_RETRIES_EXCEEDED",
                "consecutive_failures": self.consecutive_failures,
                "reason": reason,
            })
            return False, 0.0

        backoff = self.compute_backoff(self.consecutive_failures)
        self.next_allowed_attempt = curr_time + backoff

        logger.warning(
            "Connection failure #%d (%s). Backing off for %.2fs until epoch %.2f",
            self.consecutive_failures, reason, backoff, self.next_allowed_attempt
        )

        self.reconnect_history.append({
            "timestamp": curr_time,
            "event": "RECONNECT_SCHEDULED",
            "consecutive_failures": self.consecutive_failures,
            "backoff_seconds": backoff,
            "reason": reason,
        })

        return True, backoff

    def record_success(self, now_epoch: Optional[float] = None) -> None:
        """Reset consecutive failures upon successful connection."""
        curr_time = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        self.total_successes += 1
        self.consecutive_failures = 0
        self.next_allowed_attempt = None

        self.reconnect_history.append({
            "timestamp": curr_time,
            "event": "CONNECTED",
            "consecutive_failures": 0,
        })
        logger.info("Connection established successfully. Reconnect counters reset.")

    def can_attempt_now(self, now_epoch: Optional[float] = None) -> bool:
        """Determine if a connection attempt is permissible at the current epoch."""
        if self.consecutive_failures > self.config.max_reconnect_attempts:
            return False
        if self.next_allowed_attempt is None:
            return True

        curr_time = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        return curr_time >= self.next_allowed_attempt

    def reset(self) -> None:
        """Reset all counters and history."""
        self.consecutive_failures = 0
        self.next_allowed_attempt = None
        self.last_failure_time = None

    def get_telemetry(self) -> Dict[str, Any]:
        """Return structured telemetry for monitoring."""
        return {
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "max_reconnect_attempts": self.config.max_reconnect_attempts,
            "last_failure_time": self.last_failure_time,
            "next_allowed_attempt": self.next_allowed_attempt,
            "history_depth": len(self.reconnect_history),
        }
