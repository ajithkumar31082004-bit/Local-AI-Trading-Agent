#!/usr/bin/env python3
"""Dedicated production runner for Read-Only Shadow Observer (services.shadow_observer).

CRITICAL SAFETY GUARANTEES:
1. Strictly READ-ONLY shadow observation with ZERO broker execution.
2. 12 Runtime Preflight Safety Gates verified before starting.
3. 10 Automatic Stop Conditions actively evaluated.
4. Comprehensive reporting saved to data/shadow_observer/.
5. Graceful SIGINT/SIGTERM shutdown with resource cleanup.
"""

import sys
import time
import signal
import logging
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.shadow_observer.dry_run_runner import DryRunConfig, ShadowDryRunRunner
from services.shadow_observer.observer_safety_guard import verify_observer_isolation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("delta_shadow_observer")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Delta Exchange India Read-Only Shadow Observer Daemon"
    )
    parser.add_argument("--symbol", default="XAUTUSD", help="Target instrument symbol (default: XAUTUSD)")
    parser.add_argument("--duration", type=float, default=3600.0, help="Observation duration in seconds (default: 3600.0, max: 3600.0)")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Supervision tick interval in seconds (default: 1.0)")
    args = parser.parse_args()

    # Preflight isolation check
    verify_observer_isolation()

    cfg = DryRunConfig(symbol=args.symbol, max_duration_seconds=args.duration)
    runner = ShadowDryRunRunner(config=cfg)

    def handle_signal(sig, frame):
        logger.info("Signal %s received; gracefully stopping shadow observer...", sig)
        runner.request_stop(f"SIGNAL_{sig}_RECEIVED")
        try:
            report_file = runner.save_readiness_report()
            logger.info("Readiness report saved to %s", report_file)
        except Exception as exc:
            logger.warning("Could not save readiness report during signal shutdown: %s", exc)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("Starting Shadow Observer daemon for %s (duration: %.0fs)...", args.symbol, args.duration)
    if not runner.start():
        logger.error("ShadowDryRunRunner failed preflight gates.")
        sys.exit(1)

    try:
        while runner.is_running:
            runner.tick()
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        runner.stop("SHUTDOWN_LOOP_EXIT")
        try:
            report_file = runner.save_readiness_report()
            logger.info("Final readiness report saved to: %s", report_file)
        except Exception as exc:
            logger.warning("Could not save final readiness report: %s", exc)


if __name__ == "__main__":
    main()
