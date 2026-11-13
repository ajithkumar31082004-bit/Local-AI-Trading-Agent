"""Signal Recorder for Read-Only Shadow Observer.

RESPONSIBILITIES:
1. Records all strategy evaluation events into an append-only JSONL stream.
2. Updates structured summary metrics in JSON format.
3. Strictly confines write output to authorized shadow directories.
4. Asserts that every record is marked 'HYPOTHETICAL — NOT AN ORDER' and 'is_order: false'.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import json
import logging
import threading

from services.shadow_observer.strategy_evaluator import ShadowEvaluationEvent
from services.shadow_observer.observer_safety_guard import validate_shadow_write_path

logger = logging.getLogger("shadow_observer.signal_recorder")


class ShadowSignalRecorder:
    """Safely records shadow events into structured output files."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = validate_shadow_write_path(self.output_dir / "shadow_signals.jsonl", self.output_dir)
        self.summary_path = validate_shadow_write_path(self.output_dir / "shadow_signals_summary.json", self.output_dir)
        self._lock = threading.Lock()

        # Cumulative metrics
        self.total_events: int = 0
        self.signals_by_type: Dict[str, int] = {"BUY": 0, "SELL": 0, "NO_SIGNAL": 0}
        self.signals_by_strategy: Dict[str, int] = {}
        self.signals_by_regime: Dict[str, int] = {}
        self.warmup_events: int = 0

    def record_event(self, event: ShadowEvaluationEvent) -> None:
        """Write a single shadow evaluation event and update summary."""
        event_dict = event.to_dict()

        # Invariant checks
        assert event_dict.get("label") == "HYPOTHETICAL — NOT AN ORDER", "Must be labeled hypothetical"
        assert event_dict.get("is_order") is False, "Must not be flagged as an order"

        with self._lock:
            # 1. Append to JSONL stream
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_dict) + "\n")

            # 2. Update metrics
            self.total_events += 1
            sig_type = event.winning_signal
            self.signals_by_type[sig_type] = self.signals_by_type.get(sig_type, 0) + 1

            strat = event.winning_strategy
            self.signals_by_strategy[strat] = self.signals_by_strategy.get(strat, 0) + 1

            regime = event.market_regime
            self.signals_by_regime[regime] = self.signals_by_regime.get(regime, 0) + 1

            if event.is_warming_up:
                self.warmup_events += 1

            # 3. Save atomic summary
            self._save_summary()

    def _save_summary(self) -> None:
        """Atomically persist current aggregate summary."""
        summary_data = {
            "label": "HYPOTHETICAL — NOT AN ORDER",
            "is_order": False,
            "total_events_recorded": self.total_events,
            "signals_by_type": self.signals_by_type,
            "signals_by_strategy": self.signals_by_strategy,
            "signals_by_regime": self.signals_by_regime,
            "warmup_events": self.warmup_events,
            "last_event_path": str(self.jsonl_path),
        }
        tmp_summary = self.summary_path.with_suffix(".tmp")
        with open(tmp_summary, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)
        tmp_summary.replace(self.summary_path)

    def load_summary(self) -> Dict[str, Any]:
        """Read current summary data from disk."""
        if not self.summary_path.exists():
            return {}
        with open(self.summary_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_events(self) -> List[Dict[str, Any]]:
        """Read all recorded events from the JSONL stream."""
        if not self.jsonl_path.exists():
            return []
        events = []
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

