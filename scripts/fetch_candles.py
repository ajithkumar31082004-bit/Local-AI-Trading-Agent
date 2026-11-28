"""CLI script to fetch historical 5m candles from Delta Exchange India and save to CSV."""
import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
import sys

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.delta_candles import fetch_5m_candles, detect_missing_candles, DeltaCandleError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_candles")


def main():
    parser = argparse.ArgumentParser(description="Fetch historical 5m candles from Delta Exchange India public API.")
    parser.add_argument("--symbol", type=str, default="XAUTUSD", help="Contract symbol (default: XAUTUSD)")
    parser.add_argument("--days", type=float, default=5.0, help="Number of calendar days to fetch (default: 5.0)")
    parser.add_argument("--out", type=str, default="", help="Output CSV path (default: data/<symbol>_5m.csv)")
    args = parser.parse_args()

    symbol = args.symbol.upper().strip()
    total_candles = int(args.days * 24 * 12)  # 12 5m candles per hour
    out_path = Path(args.out) if args.out else ROOT / "data" / f"{symbol.lower()}_5m.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching %d days (~%d 5m candles) for %s from Delta India API...", args.days, total_candles, symbol)

    try:
        df = fetch_5m_candles(symbol=symbol, lookback_candles=total_candles)
    except DeltaCandleError as err:
        logger.error("Failed to fetch candles: %s", err)
        sys.exit(1)
    except Exception as err:
        logger.error("Unexpected error fetching candles: %s", err)
        sys.exit(1)

    # Save to CSV
    df.to_csv(out_path, index=True)
    logger.info("Successfully fetched %d candles (%s to %s).", len(df), df.index[0], df.index[-1])
    logger.info("Saved candle dataset to %s", out_path)

    # Report gaps if any
    gaps = detect_missing_candles(df)
    if gaps:
        logger.warning("Detected %d missing 5m sequence gap(s):", len(gaps))
        for start, end in gaps[:5]:
            logger.warning("  - Gap from %s to %s", start, end)
        if len(gaps) > 5:
            logger.warning("  ... and %d more gaps", len(gaps) - 5)
    else:
        logger.info("Data verification passed: No gaps detected in 5m sequence.")


if __name__ == "__main__":
    main()

