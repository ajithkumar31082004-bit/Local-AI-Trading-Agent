"""Candle Validation Engine for Read-Only Shadow Observer.

ENFORCES DATA INTEGRITY INVARIANTS:
1. Rejects incomplete (in-progress / forming) candles.
2. Enforces strictly monotonic timestamp ordering.
3. Detects and filters duplicate candles.
4. Identifies gaps (missing intervals) in candle history.
5. Verifies OHLC structural integrity (High >= Low, High >= Open/Close, Low <= Open/Close, Volume >= 0).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Set
import pandas as pd
import logging

logger = logging.getLogger("shadow_observer.candle_validator")


@dataclass
class CandleValidationReport:
    is_valid: bool
    rejection_reason: Optional[str] = None
    is_completed: bool = True
    is_duplicate: bool = False
    is_gap: bool = False
    gap_duration_seconds: float = 0.0
    missing_intervals: int = 0
    candle_timestamp: Optional[str] = None


class CandleValidator:
    """Validates streaming or batch OHLCV candles for strategy evaluation."""

    def __init__(self, expected_timeframe_minutes: int = 5):
        self.timeframe_minutes = expected_timeframe_minutes
        self.expected_interval_seconds = expected_timeframe_minutes * 60
        self.seen_timestamps: Set[int] = set()
        self.last_candle_epoch: Optional[int] = None
        self.gap_count: int = 0
        self.duplicate_count: int = 0
        self.total_validated: int = 0

    def validate_candle_structure(self, candle: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate numerical integrity of a single OHLCV record."""
        required_keys = ["open", "high", "low", "close", "volume", "timestamp"]
        for key in required_keys:
            if key not in candle and key.capitalize() not in candle:
                return False, f"Missing required candle field: {key}"

        try:
            o = float(candle.get("open", candle.get("Open", 0.0)))
            h = float(candle.get("high", candle.get("High", 0.0)))
            l = float(candle.get("low", candle.get("Low", 0.0)))
            c = float(candle.get("close", candle.get("Close", 0.0)))
            v = float(candle.get("volume", candle.get("Volume", 0.0)))
        except (ValueError, TypeError) as exc:
            return False, f"Non-numeric OHLCV value: {exc}"

        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            return False, f"Non-positive price in candle: O={o}, H={h}, L={l}, C={c}"
        if v < 0:
            return False, f"Negative volume in candle: V={v}"
        if h < l:
            return False, f"High ({h}) is less than Low ({l})"
        if h < max(o, c):
            return False, f"High ({h}) is less than max(Open, Close) ({max(o, c)})"
        if l > min(o, c):
            return False, f"Low ({l}) is greater than min(Open, Close) ({min(o, c)})"

        return True, None

    def validate_candle_completion(
        self,
        candle: Dict[str, Any],
        now_epoch: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Ensure the candle is finalized and not currently forming.
        
        A candle is incomplete if:
        1. An explicit 'is_complete' or 'completed' flag is present and False.
        2. The candle's timestamp + interval is greater than current wall clock time.
        """
        # Check explicit flag
        for flag in ["completed", "is_complete", "is_closed"]:
            if flag in candle and not bool(candle[flag]):
                return False, "INCOMPLETE_CANDLE: Candle marked as in-progress by exchange feed."

        # Check wall-clock completion if current epoch provided
        if now_epoch is not None:
            ts = candle.get("timestamp", candle.get("Timestamp"))
            if isinstance(ts, (int, float)):
                candle_start_epoch = float(ts)
            elif isinstance(ts, datetime):
                candle_start_epoch = ts.timestamp()
            elif isinstance(ts, str):
                try:
                    candle_start_epoch = pd.to_datetime(ts).timestamp()
                except Exception:
                    candle_start_epoch = 0.0
            else:
                candle_start_epoch = 0.0

            if candle_start_epoch + self.expected_interval_seconds > now_epoch:
                return False, "INCOMPLETE_CANDLE: Candle interval has not yet closed in real time."

        return True, None

    def validate_incoming_candle(
        self,
        candle: Dict[str, Any],
        now_epoch: Optional[float] = None,
    ) -> CandleValidationReport:
        """Execute comprehensive validation on an incoming candle."""
        # 1. Structure check
        valid_struct, err = self.validate_candle_structure(candle)
        if not valid_struct:
            return CandleValidationReport(is_valid=False, rejection_reason=err)

        # 2. Completion check
        valid_comp, comp_err = self.validate_candle_completion(candle, now_epoch=now_epoch)
        if not valid_comp:
            return CandleValidationReport(
                is_valid=False,
                is_completed=False,
                rejection_reason=comp_err,
            )

        # 3. Extract epoch timestamp
        ts = candle.get("timestamp", candle.get("Timestamp"))
        if isinstance(ts, (int, float)):
            epoch = int(ts)
        elif isinstance(ts, datetime):
            epoch = int(ts.timestamp())
        else:
            epoch = int(pd.to_datetime(ts).timestamp())

        # 4. Duplicate check
        if epoch in self.seen_timestamps:
            self.duplicate_count += 1
            return CandleValidationReport(
                is_valid=False,
                is_duplicate=True,
                rejection_reason=f"DUPLICATE_CANDLE: Timestamp {epoch} already evaluated.",
                candle_timestamp=str(ts),
            )

        # 5. Monotonic ordering and Gap check
        is_gap = False
        gap_sec = 0.0
        missing = 0
        if self.last_candle_epoch is not None:
            delta = epoch - self.last_candle_epoch
            if delta <= 0:
                return CandleValidationReport(
                    is_valid=False,
                    rejection_reason=f"OUT_OF_ORDER: Candle timestamp {epoch} <= last seen {self.last_candle_epoch}.",
                    candle_timestamp=str(ts),
                )
            if delta > self.expected_interval_seconds:
                is_gap = True
                gap_sec = float(delta)
                missing = int((delta // self.expected_interval_seconds) - 1)
                self.gap_count += 1
                logger.warning(
                    "Candle gap detected: %d seconds (%d missing %dm candles) between %d and %d",
                    gap_sec, missing, self.timeframe_minutes, self.last_candle_epoch, epoch,
                )

        # Record valid candle
        self.seen_timestamps.add(epoch)
        self.last_candle_epoch = epoch
        self.total_validated += 1

        return CandleValidationReport(
            is_valid=True,
            is_completed=True,
            is_gap=is_gap,
            gap_duration_seconds=gap_sec,
            missing_intervals=missing,
            candle_timestamp=str(ts),
        )

    def validate_dataframe(self, df: pd.DataFrame) -> Tuple[bool, Optional[str], pd.DataFrame]:
        """Validate and clean an entire DataFrame of historical candles."""
        if df.empty:
            return False, "EMPTY_DATAFRAME: Candle DataFrame contains zero rows.", df

        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        normalized_df = df.copy()
        col_map = {c: c.capitalize() for c in normalized_df.columns}
        normalized_df.rename(columns=col_map, inplace=True)

        if not required_cols.issubset(set(normalized_df.columns)):
            missing = required_cols - set(normalized_df.columns)
            return False, f"MISSING_COLUMNS: DataFrame missing required columns: {missing}", df

        # Ensure DatetimeIndex
        if not isinstance(normalized_df.index, pd.DatetimeIndex):
            return False, "INVALID_INDEX: DataFrame index must be a DatetimeIndex.", df

        # Check monotonic index
        if not normalized_df.index.is_monotonic_increasing:
            normalized_df.sort_index(inplace=True)

        # Remove duplicate timestamps
        if normalized_df.index.has_duplicates:
            normalized_df = normalized_df[~normalized_df.index.duplicated(keep="first")]

        # Drop NaN or non-positive rows
        valid_mask = (
            (normalized_df["Open"] > 0)
            & (normalized_df["High"] > 0)
            & (normalized_df["Low"] > 0)
            & (normalized_df["Close"] > 0)
            & (normalized_df["High"] >= normalized_df["Low"])
            & (normalized_df["Volume"] >= 0)
        )
        cleaned_df = normalized_df[valid_mask]

        if cleaned_df.empty:
            return False, "CORRUPT_DATA: Zero valid candles after structural validation.", df

        return True, None, cleaned_df

