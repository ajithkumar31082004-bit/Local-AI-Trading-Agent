"""Quantitative Performance & Statistical Acceptance Analytics for Phase 4.7 Forward PAPER Trading.

Provides rigorous, standardized performance metric computation:
- Win Rate (WR >= 50.0%)
- Profit Factor (PF >= 1.30, zero-loss division handling)
- Trade-level & Portfolio Expectancy (> +0.20 R)
- Peak-to-Trough Maximum Drawdown in Cash & Percentage (MDD <= 3.5%)
- Annualized Sharpe Ratio (Sharpe >= 1.20, 365-day basis)
- Sortino Ratio (Downside volatility penalization)
- Full breakdown by Strategy, Market Regime, and Order Direction
- Statistical Acceptance Gate enforcing minimum sample size (>= 100 trades/sessions)
"""
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
import numpy as np
import pandas as pd


# KPI Acceptance Thresholds for Phase 4.7 Forward Validation
MIN_WIN_RATE_PCT: float = 50.0
MIN_PROFIT_FACTOR: float = 1.30
MIN_EXPECTANCY_R: float = 0.20
MAX_DRAWDOWN_PCT: float = 3.50
MIN_ANNUALIZED_SHARPE: float = 1.20
MIN_REQUIRED_STATISTICAL_TRADES: int = 100


def calculate_phase4_7_metrics(
    trades: List[Dict[str, Any]],
    initial_equity: float = 10000.0,
    risk_free_rate: float = 0.0,
) -> Dict[str, Any]:
    """Calculate comprehensive performance, risk, and friction metrics from a list of closed trades."""
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "scratch_trades": 0,
            "win_rate_pct": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "profit_factor": 0.0,
            "profit_factor_display": "0.00",
            "expectancy_r": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "avg_trade_pnl": 0.0,
            "max_drawdown_cash": 0.0,
            "max_drawdown_pct": 0.0,
            "annualized_sharpe": 0.0,
            "sortino_ratio": 0.0,
            "unique_sessions_count": 0,
            "by_strategy": {},
            "by_regime": {},
            "by_direction": {},
            "fees_and_friction": {
                "brokerage_fees": 0.0,
                "gst": 0.0,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "total_friction": 0.0,
            },
            "equity_curve": [{"trade_index": 0, "equity": round(initial_equity, 4), "pnl": 0.0}],
        }

    # Partition wins, losses, scratches
    wins = [t for t in trades if float(t.get("net_pnl", 0.0)) > 0]
    losses = [t for t in trades if float(t.get("net_pnl", 0.0)) < 0]
    scratches = [t for t in trades if abs(float(t.get("net_pnl", 0.0))) < 1e-9]

    n_wins = len(wins)
    n_losses = len(losses)
    n_scratches = len(scratches)
    win_rate = (n_wins / total_trades) * 100.0

    # P&L totals
    gross_gains = sum(float(t.get("gross_pnl", t.get("net_pnl", 0.0))) for t in trades if float(t.get("gross_pnl", t.get("net_pnl", 0.0))) > 0)
    gross_losses = abs(sum(float(t.get("gross_pnl", t.get("net_pnl", 0.0))) for t in trades if float(t.get("gross_pnl", t.get("net_pnl", 0.0))) < 0))
    gross_pnl = sum(float(t.get("gross_pnl", 0.0)) for t in trades)
    net_pnl = sum(float(t.get("net_pnl", 0.0)) for t in trades)

    # Profit Factor (handling 0 losses gracefully)
    if gross_losses <= 1e-9:
        if gross_gains > 0:
            profit_factor = float("inf")
            profit_factor_display = "Infinity"
        else:
            profit_factor = 0.0
            profit_factor_display = "0.00"
    else:
        profit_factor = round(gross_gains / gross_losses, 4)
        profit_factor_display = f"{profit_factor:.2f}"

    # Expectancy in R
    win_r_list = [float(t.get("r_multiple", t.get("trade_r_multiple", 0.0))) for t in wins]
    loss_r_list = [abs(float(t.get("r_multiple", t.get("trade_r_multiple", 0.0)))) for t in losses]

    avg_win_r = (sum(win_r_list) / len(win_r_list)) if win_r_list else 0.0
    avg_loss_r = (sum(loss_r_list) / len(loss_r_list)) if loss_r_list else 0.0
    win_prob = n_wins / total_trades
    loss_prob = n_losses / total_trades
    expectancy_r = round((win_prob * avg_win_r) - (loss_prob * avg_loss_r), 4)

    # Drawdown tracking (cash and percentage of running peak)
    running_eq = float(initial_equity)
    peak_eq = running_eq
    max_dd_cash = 0.0
    max_dd_pct = 0.0
    equity_curve = [{"trade_index": 0, "equity": round(running_eq, 4), "pnl": 0.0}]

    for idx, t in enumerate(trades, 1):
        pnl = float(t.get("net_pnl", 0.0))
        running_eq += pnl
        if running_eq > peak_eq:
            peak_eq = running_eq
        dd_cash = peak_eq - running_eq
        dd_pct = (dd_cash / peak_eq) * 100.0 if peak_eq > 0 else 0.0

        if dd_cash > max_dd_cash:
            max_dd_cash = dd_cash
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        equity_curve.append({
            "trade_index": idx,
            "trade_id": t.get("trade_id", f"T_{idx}"),
            "equity": round(running_eq, 4),
            "pnl": round(pnl, 4),
            "drawdown_pct": round(dd_pct, 4),
        })

    # Group trades by UTC session/date for daily return series
    daily_pnl_map: Dict[str, float] = {}
    for t in trades:
        ts = t.get("exit_time") or t.get("entry_time") or ""
        try:
            date_key = ts[:10] if len(ts) >= 10 else "UNKNOWN_DATE"
        except Exception:
            date_key = "UNKNOWN_DATE"
        daily_pnl_map[date_key] = daily_pnl_map.get(date_key, 0.0) + float(t.get("net_pnl", 0.0))

    unique_sessions = len(daily_pnl_map)
    daily_returns: List[float] = []
    base_eq = float(initial_equity)
    for d_pnl in daily_pnl_map.values():
        ret = d_pnl / max(base_eq, 1.0)
        daily_returns.append(ret)
        base_eq += d_pnl

    # Annualized Sharpe & Sortino (365 days for 24/7 crypto perpetuals)
    annualized_sharpe = 0.0
    sortino_ratio = 0.0
    if len(daily_returns) >= 2:
        mean_ret = float(np.mean(daily_returns))
        std_ret = float(np.std(daily_returns, ddof=1))
        daily_rf = (risk_free_rate / 100.0) / 365.0

        if std_ret > 1e-9:
            annualized_sharpe = round(((mean_ret - daily_rf) / std_ret) * math.sqrt(365), 4)

        # Downside deviations (returns below target/risk-free rate)
        downside_diffs = [min(0.0, r - daily_rf) for r in daily_returns]
        downside_variance = sum(d**2 for d in downside_diffs) / len(downside_diffs)
        downside_std = math.sqrt(downside_variance)
        if downside_std > 1e-9:
            sortino_ratio = round(((mean_ret - daily_rf) / downside_std) * math.sqrt(365), 4)
        elif mean_ret > daily_rf:
            sortino_ratio = float("inf")

    # Fees and friction breakdown
    total_brokerage = sum(float(t.get("brokerage_fee", t.get("fees", 0.0))) for t in trades)
    total_gst = sum(float(t.get("gst", 0.0)) for t in trades)
    total_spread = sum(float(t.get("spread_cost", 0.0)) for t in trades)
    total_slip = sum(float(t.get("slippage_cost", 0.0)) for t in trades)
    total_friction = sum(float(t.get("total_friction", 0.0)) for t in trades)

    # Strategy breakdown
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        strat = t.get("strategy_name", "UNKNOWN")
        if strat not in by_strategy:
            by_strategy[strat] = {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "r_sum": 0.0}
        by_strategy[strat]["trades"] += 1
        pnl = float(t.get("net_pnl", 0.0))
        by_strategy[strat]["net_pnl"] += pnl
        by_strategy[strat]["r_sum"] += float(t.get("r_multiple", t.get("trade_r_multiple", 0.0)))
        if pnl > 0:
            by_strategy[strat]["wins"] += 1
        elif pnl < 0:
            by_strategy[strat]["losses"] += 1

    for s_info in by_strategy.values():
        cnt = s_info["trades"]
        s_info["win_rate_pct"] = round((s_info["wins"] / cnt) * 100.0, 2) if cnt > 0 else 0.0
        s_info["avg_r"] = round(s_info["r_sum"] / cnt, 4) if cnt > 0 else 0.0
        s_info["net_pnl"] = round(s_info["net_pnl"], 4)
        del s_info["r_sum"]

    # Market Regime breakdown
    by_regime: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        reg = t.get("market_regime", "UNKNOWN")
        if reg not in by_regime:
            by_regime[reg] = {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        by_regime[reg]["trades"] += 1
        pnl = float(t.get("net_pnl", 0.0))
        by_regime[reg]["net_pnl"] += pnl
        if pnl > 0:
            by_regime[reg]["wins"] += 1
        elif pnl < 0:
            by_regime[reg]["losses"] += 1

    for r_info in by_regime.values():
        cnt = r_info["trades"]
        r_info["win_rate_pct"] = round((r_info["wins"] / cnt) * 100.0, 2) if cnt > 0 else 0.0
        r_info["net_pnl"] = round(r_info["net_pnl"], 4)

    # Direction breakdown
    by_direction: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        d = t.get("direction") or t.get("side") or "UNKNOWN"
        d = d.upper()
        if d not in by_direction:
            by_direction[d] = {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        by_direction[d]["trades"] += 1
        pnl = float(t.get("net_pnl", 0.0))
        by_direction[d]["net_pnl"] += pnl
        if pnl > 0:
            by_direction[d]["wins"] += 1
        elif pnl < 0:
            by_direction[d]["losses"] += 1

    for d_info in by_direction.values():
        cnt = d_info["trades"]
        d_info["win_rate_pct"] = round((d_info["wins"] / cnt) * 100.0, 2) if cnt > 0 else 0.0
        d_info["net_pnl"] = round(d_info["net_pnl"], 4)

    return {
        "total_trades": total_trades,
        "winning_trades": n_wins,
        "losing_trades": n_losses,
        "scratch_trades": n_scratches,
        "win_rate_pct": round(win_rate, 2),
        "gross_profit": round(gross_gains, 4),
        "gross_loss": round(gross_losses, 4),
        "gross_pnl": round(gross_pnl, 4),
        "net_pnl": round(net_pnl, 4),
        "profit_factor": profit_factor,
        "profit_factor_display": profit_factor_display,
        "expectancy_r": expectancy_r,
        "avg_win_r": round(avg_win_r, 4),
        "avg_loss_r": round(avg_loss_r, 4),
        "avg_trade_pnl": round(net_pnl / total_trades, 4),
        "max_drawdown_cash": round(max_dd_cash, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "annualized_sharpe": annualized_sharpe,
        "sortino_ratio": sortino_ratio,
        "unique_sessions_count": unique_sessions,
        "by_strategy": by_strategy,
        "by_regime": by_regime,
        "by_direction": by_direction,
        "fees_and_friction": {
            "brokerage_fees": round(total_brokerage, 4),
            "gst": round(total_gst, 4),
            "spread_cost": round(total_spread, 4),
            "slippage_cost": round(total_slip, 4),
            "total_friction": round(total_friction, 4),
        },
        "equity_curve": equity_curve,
    }


def evaluate_statistical_acceptance_gate(
    metrics: Dict[str, Any],
    min_trades: int = MIN_REQUIRED_STATISTICAL_TRADES,
    min_sessions: int = MIN_REQUIRED_STATISTICAL_TRADES,
) -> Dict[str, Any]:
    """Evaluate whether the forward paper testing results satisfy Phase 4.7 acceptance criteria.
    
    CRITICAL STATISTICAL GATE:
    Both sample thresholds (>= min_trades closed trades AND >= min_sessions distinct UTC sessions)
    are mandatory. If either threshold is not satisfied, the gate CANNOT pass and returns
    status="INSUFFICIENT_EVIDENCE". Profitability claims without sufficient statistical sample
    size and temporal duration are strictly forbidden by governance.
    """
    total = metrics.get("total_trades", 0)
    sessions = metrics.get("unique_sessions_count", 0)

    trades_passed = total >= min_trades
    sessions_passed = sessions >= min_sessions

    # 1. Sample Size & Session Duration Check
    if not trades_passed or not sessions_passed:
        reasons = []
        if not trades_passed:
            gate_code = "GATE_INSUFFICIENT_SAMPLE_SIZE"
            reasons.append(f"Only {total} closed trades recorded (minimum {min_trades} required)")
        else:
            gate_code = "GATE_INSUFFICIENT_SESSIONS"
            reasons.append(f"Only {sessions} distinct UTC sessions recorded (minimum {min_sessions} required)")

        if not sessions_passed and not trades_passed:
            reasons.append(f"Only {sessions} distinct UTC sessions recorded (minimum {min_sessions} required)")

        return {
            "gate_passed": False,
            "status": "INSUFFICIENT_EVIDENCE",
            "gate_code": gate_code,
            "sample_size": total,
            "required_sample_size": min_trades,
            "sessions_count": sessions,
            "required_sessions": min_sessions,
            "summary_rationale": (
                f"INSUFFICIENT_EVIDENCE: {'; '.join(reasons)}. "
                "Statistical significance for forward alpha validation cannot be claimed without meeting both sample thresholds."
            ),
            "criteria_breakdown": {
                "sample_size_check": {
                    "passed": trades_passed,
                    "actual": total,
                    "required": min_trades,
                },
                "session_count_check": {
                    "passed": sessions_passed,
                    "actual": sessions,
                    "required": min_sessions,
                },
                "win_rate_check": {
                    "passed": False,
                    "actual": metrics.get("win_rate_pct", 0.0),
                    "required": MIN_WIN_RATE_PCT,
                    "status": "UNTESTED_INSUFFICIENT_SAMPLE",
                },
                "profit_factor_check": {
                    "passed": False,
                    "actual": metrics.get("profit_factor", 0.0),
                    "required": MIN_PROFIT_FACTOR,
                    "status": "UNTESTED_INSUFFICIENT_SAMPLE",
                },
                "expectancy_check": {
                    "passed": False,
                    "actual": metrics.get("expectancy_r", 0.0),
                    "required": MIN_EXPECTANCY_R,
                    "status": "UNTESTED_INSUFFICIENT_SAMPLE",
                },
                "max_drawdown_check": {
                    "passed": False,
                    "actual": metrics.get("max_drawdown_pct", 0.0),
                    "required": MAX_DRAWDOWN_PCT,
                    "status": "UNTESTED_INSUFFICIENT_SAMPLE",
                },
                "sharpe_ratio_check": {
                    "passed": False,
                    "actual": metrics.get("annualized_sharpe", 0.0),
                    "required": MIN_ANNUALIZED_SHARPE,
                    "status": "UNTESTED_INSUFFICIENT_SAMPLE",
                },
            },
        }

    # 2. Detailed Quantitative Criteria Check
    wr = float(metrics.get("win_rate_pct", 0.0))
    pf = float(metrics.get("profit_factor", 0.0))
    exp_r = float(metrics.get("expectancy_r", 0.0))
    mdd = float(metrics.get("max_drawdown_pct", 0.0))
    sharpe = float(metrics.get("annualized_sharpe", 0.0))

    wr_passed = wr >= MIN_WIN_RATE_PCT
    pf_passed = pf >= MIN_PROFIT_FACTOR or math.isinf(pf)
    exp_passed = exp_r > MIN_EXPECTANCY_R
    mdd_passed = mdd <= MAX_DRAWDOWN_PCT
    sharpe_passed = sharpe >= MIN_ANNUALIZED_SHARPE

    all_passed = all([wr_passed, pf_passed, exp_passed, mdd_passed, sharpe_passed])

    failed_criteria = []
    if not wr_passed:
        failed_criteria.append(f"Win Rate {wr:.2f}% < {MIN_WIN_RATE_PCT}%")
    if not pf_passed:
        failed_criteria.append(f"Profit Factor {pf:.2f} < {MIN_PROFIT_FACTOR}")
    if not exp_passed:
        failed_criteria.append(f"Expectancy {exp_r:+.2f}R <= {MIN_EXPECTANCY_R:+.2f}R")
    if not mdd_passed:
        failed_criteria.append(f"Max Drawdown {mdd:.2f}% > {MAX_DRAWDOWN_PCT}%")
    if not sharpe_passed:
        failed_criteria.append(f"Sharpe {sharpe:.2f} < {MIN_ANNUALIZED_SHARPE}")

    status = "ACCEPTED" if all_passed else "KPI_THRESHOLD_UNMET"
    gate_code = "GATE_PASSED" if all_passed else "GATE_FAILED_KPI"
    rationale = (
        f"All Phase 4.7 forward quantitative KPIs satisfied across {total} trades and {sessions} sessions."
        if all_passed
        else f"Statistical gate failed: {'; '.join(failed_criteria)}."
    )

    return {
        "gate_passed": all_passed,
        "status": status,
        "gate_code": gate_code,
        "sample_size": total,
        "required_sample_size": min_trades,
        "sessions_count": sessions,
        "required_sessions": min_sessions,
        "summary_rationale": rationale,
        "failed_criteria": failed_criteria,
        "criteria_breakdown": {
            "sample_size_check": {
                "passed": True,
                "actual": total,
                "required": min_trades,
            },
            "session_count_check": {
                "passed": True,
                "actual": sessions,
                "required": min_sessions,
            },
            "win_rate_check": {
                "passed": wr_passed,
                "actual": wr,
                "required": MIN_WIN_RATE_PCT,
            },
            "profit_factor_check": {
                "passed": pf_passed,
                "actual": pf,
                "required": MIN_PROFIT_FACTOR,
            },
            "expectancy_check": {
                "passed": exp_passed,
                "actual": exp_r,
                "required": MIN_EXPECTANCY_R,
            },
            "max_drawdown_check": {
                "passed": mdd_passed,
                "actual": mdd,
                "required": MAX_DRAWDOWN_PCT,
            },
            "sharpe_ratio_check": {
                "passed": sharpe_passed,
                "actual": sharpe,
                "required": MIN_ANNUALIZED_SHARPE,
            },
        },
    }


def load_paper_state_trades(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Safely load closed trades from a state JSON file."""
    path = Path(file_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("closed_trades", [])
    except Exception:
        return []


def evaluate_forward_state_file(
    file_path: Union[str, Path],
    min_trades: int = MIN_REQUIRED_STATISTICAL_TRADES,
    min_sessions: int = MIN_REQUIRED_STATISTICAL_TRADES,
    initial_equity: float = 10000.0,
) -> Dict[str, Any]:
    """Complete workflow: load trades from state file, compute metrics, and evaluate statistical gate."""
    trades = load_paper_state_trades(file_path)
    metrics = calculate_phase4_7_metrics(trades, initial_equity=initial_equity)
    gate = evaluate_statistical_acceptance_gate(metrics, min_trades=min_trades, min_sessions=min_sessions)
    return {
        "file_path": str(file_path),
        "metrics": metrics,
        "gate": gate,
    }

