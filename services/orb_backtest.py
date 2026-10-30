"""ORB Backtest Service: Chronological replay with deterministic strategy and risk rules."""
from backtest.orb_backtest import (
    ORBBacktestResult,
    run_backtest,
    save_equity_curve_html,
)

# Alias backtest_orb to run_backtest for compatibility with user instructions
backtest_orb = run_backtest

__all__ = [
    "ORBBacktestResult",
    "run_backtest",
    "backtest_orb",
    "save_equity_curve_html",
]

