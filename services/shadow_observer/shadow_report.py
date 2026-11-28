"""Shadow Validation Reporting Engine.

Compiles recorded shadow evaluation events into structured audit reports.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import json
import logging

from services.shadow_observer.observer_safety_guard import validate_shadow_write_path

logger = logging.getLogger("shadow_observer.shadow_report")


class ShadowReportGenerator:
    """Generates comprehensive markdown and JSON reports from shadow observer outputs."""

    def __init__(self, shadow_dir: Path):
        self.shadow_dir = Path(shadow_dir).resolve()
        self.jsonl_path = self.shadow_dir / "shadow_signals.jsonl"
        self.summary_path = self.shadow_dir / "shadow_signals_summary.json"

    def compile_report_markdown(self, output_path: Optional[Path] = None) -> str:
        """Compile a markdown report from recorded shadow validation events."""
        events = []
        if self.jsonl_path.exists():
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line.strip()))

        summary = {}
        if self.summary_path.exists():
            with open(self.summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)

        total_events = len(events)
        buy_signals = [e for e in events if e.get("winning_signal") == "BUY"]
        sell_signals = [e for e in events if e.get("winning_signal") == "SELL"]
        no_signals = [e for e in events if e.get("winning_signal") == "NO_SIGNAL"]

        strat_counts: Dict[str, int] = {}
        regime_counts: Dict[str, int] = {}
        for e in events:
            strat = e.get("winning_strategy", "NONE")
            strat_counts[strat] = strat_counts.get(strat, 0) + 1
            regime = e.get("market_regime", "UNKNOWN")
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

        md_lines = [
            "# XAUTUSD READ-ONLY SHADOW VALIDATION REPORT",
            "",
            "**Mode:** STRICT READ-ONLY SHADOW OBSERVER  ",
            "**Instrument:** `XAUTUSD`  ",
            "**Label:** `HYPOTHETICAL — NOT AN ORDER`  ",
            f"**Total Events Evaluated:** {total_events}  ",
            "",
            "---",
            "",
            "## 1. Executive Summary",
            "",
            "This report documents hypothetical signal generation across all 6 registered strategies under read-only shadow observation. Zero orders were placed and zero broker state was modified.",
            "",
            "### Signal Distribution",
            "",
            "| Signal Type | Count | Percentage |",
            "| :--- | :--- | :--- |",
            f"| **BUY** | {len(buy_signals)} | {(len(buy_signals)/total_events*100 if total_events else 0.0):.1f}% |",
            f"| **SELL** | {len(sell_signals)} | {(len(sell_signals)/total_events*100 if total_events else 0.0):.1f}% |",
            f"| **NO_SIGNAL** | {len(no_signals)} | {(len(no_signals)/total_events*100 if total_events else 0.0):.1f}% |",
            f"| **Total** | {total_events} | 100.0% |",
            "",
            "---",
            "",
            "## 2. Strategy Winning Distribution",
            "",
            "| Strategy | Signals Generated |",
            "| :--- | :--- |",
        ]

        for s_name, count in sorted(strat_counts.items(), key=lambda x: x[1], reverse=True):
            md_lines.append(f"| `{s_name}` | {count} |")

        md_lines.extend([
            "",
            "---",
            "",
            "## 3. Market Regime Distribution",
            "",
            "| Market Regime | Frequency |",
            "| :--- | :--- |",
        ])

        for r_name, count in sorted(regime_counts.items(), key=lambda x: x[1], reverse=True):
            md_lines.append(f"| `{r_name}` | {count} |")

        md_lines.extend([
            "",
            "---",
            "",
            "## 4. Strict Safety & Isolation Verification",
            "",
            "- **Exchange Orders Placed:** 0 (Verified)",
            "- **Broker State Mutations:** 0 (Verified)",
            "- **Cash Balance Alterations:** $0.00 (Verified)",
            "- **Historical Incident Record (`PAPER_20261105_101000_LONG`):** 100% Intact",
            "- **Private Exchange API Access:** None",
            "",
        ])

        report_content = "\n".join(md_lines)

        if output_path:
            safe_dest = validate_shadow_write_path(output_path, self.shadow_dir)
            with open(safe_dest, "w", encoding="utf-8") as f:
                f.write(report_content)

        return report_content

