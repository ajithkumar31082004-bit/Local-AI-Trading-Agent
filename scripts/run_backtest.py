"""CLI runner for deterministic ORB + EMA20 + Volume strategy backtests with walk-forward analysis."""
import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
from typing import Dict, Any, Optional
import pandas as pd
import yaml

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.orb_settings import ORBSettings
from data.csv_loader import load_candles_csv
from data.delta_candles import fetch_5m_candles, DeltaCandleError
from services.orb_backtest import backtest_orb, ORBBacktestResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_backtest")


def load_orb_config(config_path: Path) -> Dict[str, Any]:
    """Load configuration dictionary from YAML file."""
    if not config_path.exists():
        logger.warning("Config file %s not found. Using default settings.", config_path)
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_orb_settings(yaml_cfg: Dict[str, Any], overrides: Dict[str, Any]) -> ORBSettings:
    """Create ORBSettings dataclass blending defaults, YAML configs, and CLI overrides."""
    merged = dict(yaml_cfg)
    merged.update({k: v for k, v in overrides.items() if v is not None})

    field_names = set(ORBSettings.__dataclass_fields__.keys())
    valid_args = {k: v for k, v in merged.items() if k in field_names}
    return ORBSettings(**valid_args)


def print_backtest_summary(title: str, res: ORBBacktestResult):
    """Print clean formatted backtest metrics."""
    print("=" * 65)
    print(f" {title.upper()} ")
    print("=" * 65)
    print(f"  Total Days Replayed      : {res.days}")
    print(f"  Total Trades Taken       : {res.trades}")
    print(f"  Days With No Setup       : {res.skipped_days} ({res.no_setup_pct:.1f}%)")
    print(f"  Win Rate                 : {res.win_rate_pct:.1f}%")
    print(f"  Average R per Trade      : {res.average_r:+.4f} R")
    print(f"  Total Realized R         : {res.total_r:+.4f} R")
    print(f"  Profit Factor            : {res.profit_factor:.2f}")
    print(f"  Max Drawdown (R)         : {res.max_drawdown_r:.4f} R")
    print(f"  Max Drawdown (%)         : {res.max_drawdown_pct:.2f}%")
    print(f"  Longest Losing Streak    : {res.longest_losing_streak} trade(s)")
    print(f"  Net Simulated P&L        : ${res.net_pnl:+,.2f}")
    print("=" * 65)


def run_walk_forward(candles: pd.DataFrame, initial_equity: float, cfg: ORBSettings, out_dir: Path):
    """Split data into 70% In-Sample (training/tune) and 30% Out-of-Sample (testing)."""
    df = candles.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is None:
        idx = idx.tz_localize(cfg.timezone, ambiguous="raise", nonexistent="raise")
    else:
        idx = idx.tz_convert(cfg.timezone)
    df.index = idx
    unique_days = sorted(list(dict.fromkeys(df.index.date)))

    split_idx = int(len(unique_days) * 0.70)
    if split_idx < 1 or (len(unique_days) - split_idx) < 1:
        logger.warning("Not enough days (%d) for meaningful 70/30 walk-forward split. Running full period.", len(unique_days))
        res = backtest_orb(candles, initial_equity=initial_equity, cfg=cfg, chart_output_path=out_dir / "equity_curve.html")
        print_backtest_summary("Full Period Replay", res)
        return

    is_days = set(unique_days[:split_idx])
    oos_days = set(unique_days[split_idx:])

    is_df = df[[ts.date() in is_days for ts in df.index]]
    oos_df = df[[ts.date() in oos_days for ts in df.index]]

    logger.info("Walk-Forward: In-Sample has %d days (%s to %s)", len(is_days), unique_days[0], unique_days[split_idx - 1])
    logger.info("Walk-Forward: Out-of-Sample has %d days (%s to %s)", len(oos_days), unique_days[split_idx], unique_days[-1])

    is_res = backtest_orb(
        is_df,
        initial_equity=initial_equity,
        cfg=cfg,
        chart_output_path=out_dir / "is_equity_curve.html",
    )
    print_backtest_summary(f"In-Sample (First 70% - {len(is_days)} Days)", is_res)

    oos_res = backtest_orb(
        oos_df,
        initial_equity=initial_equity,
        cfg=cfg,
        chart_output_path=out_dir / "oos_equity_curve.html",
    )
    print_backtest_summary(f"Out-of-Sample (Last 30% - {len(oos_days)} Days)", oos_res)

    # Overfitting Check
    if is_res.average_r > 0 and oos_res.average_r < 0:
        print("\n [!] WARNING: In-sample performance was positive but out-of-sample was negative (potential overfitting).")
    elif is_res.win_rate_pct > 0 and oos_res.win_rate_pct < (is_res.win_rate_pct * 0.7):
        print("\n [!] WARNING: Substantial drop in win rate between in-sample and out-of-sample periods.")
    else:
        print("\n [OK] Walk-forward performance maintained acceptable consistency between periods.")


def main():
    parser = argparse.ArgumentParser(description="Deterministic ORB Backtest Runner with Walk-Forward Analysis.")
    parser.add_argument("source", nargs="?", default="", help="Path to CSV file or leave blank to fetch from Delta API")
    parser.add_argument("--config", type=str, default="config/orb.yaml", help="Path to YAML config (default: config/orb.yaml)")
    parser.add_argument("--symbol", type=str, default=None, help="Symbol to fetch/replay (e.g. XAUTUSD)")
    parser.add_argument("--days", type=float, default=14.0, help="Days of live candles to fetch if no CSV supplied (default: 14)")
    parser.add_argument("--equity", type=float, default=10000.0, help="Initial account equity (default: 10000.0)")
    parser.add_argument("--walk-forward", action="store_true", help="Run 70/30 in-sample vs out-of-sample walk-forward test")
    parser.add_argument("--out-trades", type=str, default="data/orb_backtest_trades.csv", help="CSV path for trade logs")
    parser.add_argument("--out-chart", type=str, default="data/orb_equity_curve.html", help="HTML path for Plotly equity curve")
    args = parser.parse_args()

    cfg_dict = load_orb_config(ROOT / args.config)
    overrides = {}
    if args.symbol:
        overrides["symbol"] = args.symbol

    orb_cfg = build_orb_settings(cfg_dict, overrides)

    # Load candles
    if args.source and Path(args.source).exists():
        logger.info("Loading candles from local CSV: %s", args.source)
        candles = load_candles_csv(args.source, timezone=orb_cfg.timezone)
    else:
        logger.info("Fetching %d days of %s 5m candles from Delta API...", args.days, orb_cfg.symbol)
        total_bars = int(args.days * 24 * 12)
        try:
            candles = fetch_5m_candles(orb_cfg.symbol, lookback_candles=total_bars)
        except Exception as exc:
            logger.error("Failed to load candles: %s", exc)
            sys.exit(1)

    logger.info("Loaded %d candles across session.", len(candles))

    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.walk_forward:
        run_walk_forward(candles, initial_equity=args.equity, cfg=orb_cfg, out_dir=out_dir)
    else:
        chart_path = ROOT / args.out_chart
        res = backtest_orb(candles, initial_equity=args.equity, cfg=orb_cfg, chart_output_path=chart_path)
        print_backtest_summary("ORB Strategy Backtest Results", res)

        # Export trade log
        if res.trade_log:
            trades_df = pd.DataFrame(res.trade_log)
            trades_path = ROOT / args.out_trades
            trades_df.to_csv(trades_path, index=False)
            logger.info("Trade log exported to %s", trades_path)
        else:
            logger.info("No trades triggered during replay period.")

        if chart_path.exists():
            logger.info("Interactive equity curve chart saved to %s", chart_path)


if __name__ == "__main__":
    main()

