"""Deployment tracking and verification service for EC2 releases."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("deployment_tracker")

BASE_DIR = Path(__file__).resolve().parent.parent
DEPLOYMENTS_FILE = BASE_DIR / "data" / "deployments.json"


def record_deployment(
    commit_sha: str,
    branch: str = "feature/strategy-intelligence-v2",
    author: str = "ajithkumar31082004-bit",
    status: str = "SUCCESS",
    tests_passed: bool = True,
    message: str = "",
) -> Dict[str, Any]:
    """Append a new deployment record to data/deployments.json."""
    DEPLOYMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if DEPLOYMENTS_FILE.exists():
        try:
            with open(DEPLOYMENTS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    dep_id = f"DEP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    record = {
        "id": dep_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit_sha,
        "branch": branch,
        "author": author,
        "status": status,
        "tests_passed": tests_passed,
        "message": message,
    }
    existing.insert(0, record)

    # Keep last 50 deployments
    existing = existing[:50]

    with open(DEPLOYMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    logger.info("Recorded deployment %s for commit %s: %s", dep_id, commit_sha, status)
    return record


def get_latest_deployment() -> Optional[Dict[str, Any]]:
    """Get the most recent deployment record."""
    if not DEPLOYMENTS_FILE.exists():
        return None
    try:
        with open(DEPLOYMENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data[0] if data else None
    except Exception:
        return None

