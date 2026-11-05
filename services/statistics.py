"""Statistical validation and inference engine for trading systems."""
import math
from typing import Dict, Any, List, Tuple, Optional


def wilson_score_interval(
    wins: int,
    total: int,
    confidence: float = 0.95,
) -> Tuple[float, float, float]:
    """Calculate the Wilson score continuity-adjusted confidence interval for a binomial proportion.
    
    Formula:
      p_hat = wins / total
      centre = (p_hat + z^2 / (2 * total)) / (1 + z^2 / total)
      margin = z * sqrt(p_hat * (1 - p_hat) / total + z^2 / (4 * total^2)) / (1 + z^2 / total)
      CI = [centre - margin, centre + margin]
    
    Returns (p_hat, ci_lower, ci_upper).
    For total == 0, returns (0.0, 0.0, 0.0).
    """
    if total <= 0:
        return 0.0, 0.0, 0.0

    # Two-tailed normal quantile z
    # 0.95 -> 1.95996, 0.99 -> 2.57583, 0.90 -> 1.64485
    if abs(confidence - 0.95) < 0.01:
        z = 1.95996
    elif abs(confidence - 0.99) < 0.01:
        z = 2.57583
    elif abs(confidence - 0.90) < 0.01:
        z = 1.64485
    else:
        # Standard approximation for other confidence levels
        alpha = 1.0 - confidence
        # Approximation for standard normal quantile
        z = 1.95996

    p_hat = wins / total
    denom = 1.0 + (z**2 / total)
    centre = (p_hat + (z**2 / (2.0 * total))) / denom
    margin = (z * math.sqrt((p_hat * (1.0 - p_hat) / total) + (z**2 / (4.0 * (total**2))))) / denom

    ci_lower = max(0.0, centre - margin)
    ci_upper = min(1.0, centre + margin)
    return round(p_hat, 4), round(ci_lower, 4), round(ci_upper, 4)


def required_sample_size(
    p: float = 0.35,
    margin_of_error: float = 0.05,
    confidence: float = 0.95,
) -> int:
    """Calculate sample size required to estimate win rate within specified margin of error.
    
    N = ceil(z^2 * p * (1 - p) / E^2)
    """
    z = 1.95996 if abs(confidence - 0.95) < 0.01 else 1.96
    return math.ceil((z**2 * p * (1.0 - p)) / (margin_of_error**2))


def calculate_trade_statistics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate comprehensive statistical performance and friction breakdown across closed trades."""
    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate_pct": 0.0,
            "wilson_ci_95": (0.0, 0.0),
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "gross_pnl": 0.0,
            "brokerage_fees": 0.0,
            "gst": 0.0,
            "spread_cost": 0.0,
            "slippage_cost": 0.0,
            "total_friction": 0.0,
            "net_pnl": 0.0,
            "profit_factor": 0.0,
            "average_net_r": 0.0,
            "expectancy_r": 0.0,
            "max_drawdown_cash": 0.0,
            "consecutive_losses": 0,
            "long_trades": 0,
            "short_trades": 0,
            "regime_attribution": {},
        }

    wins = [t for t in trades if t.get("net_pnl", 0.0) > 0]
    losses = [t for t in trades if t.get("net_pnl", 0.0) <= 0]
    n_wins = len(wins)
    n_losses = len(losses)
    win_rate = (n_wins / total) * 100.0

    p_hat, ci_lower, ci_upper = wilson_score_interval(n_wins, total, 0.95)

    gross_gains = sum(t.get("gross_pnl", 0.0) for t in wins if t.get("gross_pnl", 0.0) > 0)
    gross_losses = abs(sum(t.get("gross_pnl", 0.0) for t in losses if t.get("gross_pnl", 0.0) < 0))
    gross_pnl = sum(t.get("gross_pnl", 0.0) for t in trades)

    net_gains = sum(t.get("net_pnl", 0.0) for t in wins)
    net_losses = abs(sum(t.get("net_pnl", 0.0) for t in losses))
    net_pnl = sum(t.get("net_pnl", 0.0) for t in trades)

    profit_factor = (net_gains / net_losses) if net_losses > 0 else (999.0 if net_gains > 0 else 0.0)

    total_fees = sum(t.get("fees", t.get("brokerage_fee", 0.0)) for t in trades)
    total_gst = sum(t.get("gst", 0.0) for t in trades)
    total_spread = sum(t.get("spread_cost", 0.0) for t in trades)
    total_slip = sum(t.get("slippage_cost", 0.0) for t in trades)
    total_friction = sum(t.get("total_friction", 0.0) for t in trades)

    r_multiples = [t.get("r_multiple", t.get("trade_r_multiple", 0.0)) for t in trades]
    avg_r = (sum(r_multiples) / total) if total > 0 else 0.0

    # Expectancy in R: (Win_Rate * Avg_Win_R) - (Loss_Rate * Avg_Loss_R)
    win_r_list = [t.get("trade_r_multiple", t.get("r_multiple", 0.0)) for t in wins]
    loss_r_list = [abs(t.get("trade_r_multiple", t.get("r_multiple", 0.0))) for t in losses]
    avg_win_r = (sum(win_r_list) / len(win_r_list)) if win_r_list else 0.0
    avg_loss_r = (sum(loss_r_list) / len(loss_r_list)) if loss_r_list else 0.0
    expectancy_r = ((n_wins / total) * avg_win_r) - ((n_losses / total) * avg_loss_r)

    # Consecutive losses & Drawdown
    curr_losses = 0
    max_consec_losses = 0
    running_equity = 10000.0
    peak_equity = 10000.0
    max_dd = 0.0

    for t in trades:
        pnl = t.get("net_pnl", 0.0)
        running_equity += pnl
        if running_equity > peak_equity:
            peak_equity = running_equity
        dd = peak_equity - running_equity
        if dd > max_dd:
            max_dd = dd

        if pnl <= 0:
            curr_losses += 1
            max_consec_losses = max(max_consec_losses, curr_losses)
        else:
            curr_losses = 0

    # Direction breakdown
    long_trades = [t for t in trades if t.get("direction", "").upper() == "LONG"]
    short_trades = [t for t in trades if t.get("direction", "").upper() == "SHORT"]

    # Regime attribution
    regimes: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        reg = t.get("market_regime", "UNKNOWN")
        if reg not in regimes:
            regimes[reg] = {"trades": 0, "wins": 0, "losses": 0, "gross_pnl": 0.0, "net_pnl": 0.0}
        regimes[reg]["trades"] += 1
        pnl = t.get("net_pnl", 0.0)
        if pnl > 0:
            regimes[reg]["wins"] += 1
        else:
            regimes[reg]["losses"] += 1
        regimes[reg]["gross_pnl"] = round(regimes[reg]["gross_pnl"] + t.get("gross_pnl", 0.0), 4)
        regimes[reg]["net_pnl"] = round(regimes[reg]["net_pnl"] + pnl, 4)

    return {
        "total_trades": total,
        "winning_trades": n_wins,
        "losing_trades": n_losses,
        "win_rate_pct": round(win_rate, 2),
        "wilson_ci_95": (round(ci_lower * 100.0, 2), round(ci_upper * 100.0, 2)),
        "gross_profit": round(gross_gains, 4),
        "gross_loss": round(gross_losses, 4),
        "gross_pnl": round(gross_pnl, 4),
        "brokerage_fees": round(total_fees, 4),
        "gst": round(total_gst, 4),
        "spread_cost": round(total_spread, 4),
        "slippage_cost": round(total_slip, 4),
        "total_friction": round(total_friction, 4),
        "net_pnl": round(net_pnl, 4),
        "profit_factor": round(profit_factor, 2),
        "average_net_r": round(avg_r, 4),
        "expectancy_r": round(expectancy_r, 4),
        "max_drawdown_cash": round(max_dd, 4),
        "consecutive_losses": max_consec_losses,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "regime_attribution": regimes,
    }

