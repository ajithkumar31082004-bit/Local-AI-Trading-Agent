"""14-Day Walk-Forward Paper Trading Engine for XAUTUSD on Delta Exchange India.

Evaluates 14 consecutive days (4,032 5-minute bars) using real public market data from
Delta Exchange India, strictly simulating local paper execution without placing real orders.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.orb_settings import ORBSettings
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from strategy.arbitrator import arbitrate_strategies
from regime.market_regime import detect_market_regime
from indicators.orb_indicators import resample_to_15m
from data.delta_candles import detect_missing_candles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ForwardTest14D")


def fetch_full_14d_candles(symbol: str = "XAUTUSD", total_days: int = 14, bootstrap_bars: int = 450) -> pd.DataFrame:
    """Fetch consecutive completed 5m candles from Delta Exchange India public REST API."""
    url = "https://api.india.delta.exchange/v2/history/candles"
    required_bars = (total_days * 288) + bootstrap_bars + 50
    logger.info("Fetching %d completed 5m candles for %d-day study + %d bootstrap bars...", required_bars, total_days, bootstrap_bars)
    
    end_epoch = int(datetime.now(timezone.utc).timestamp())
    start_epoch = end_epoch - (required_bars * 300)
    
    all_raw = []
    cur_end = end_epoch
    
    while cur_end > start_epoch:
        chunk_start = max(start_epoch, cur_end - (500 * 300))
        params = {"symbol": symbol, "resolution": "5m", "start": chunk_start, "end": cur_end}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} fetching candles: {resp.text}")
        data = resp.json().get("result", [])
        if not data:
            break
        all_raw.extend(data)
        oldest = min(b["time"] for b in data)
        if oldest <= start_epoch or oldest >= cur_end:
            break
        cur_end = oldest - 300
        
    df = pd.DataFrame(all_raw).rename(columns={
        "time": "Timestamp", "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"
    })
    df["Datetime"] = pd.to_datetime(df["Timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.drop_duplicates(subset=["Datetime"]).sort_values("Datetime").set_index("Datetime")
    
    # Exclude currently forming uncompleted bar
    now_ts = datetime.now(timezone.utc).timestamp()
    if now_ts - df["Timestamp"].iloc[-1] < 300:
        df = df.iloc[:-1]
        
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        
    logger.info("Successfully fetched %d clean chronological 5m bars. Span: %s to %s", len(df), df.index[0], df.index[-1])
    return df


def execute_14d_forward_test():
    """Execute the full 14-day forward testing simulation."""
    cfg = ORBSettings(symbol="XAUTUSD", mode="PAPER")
    assert cfg.mode == "PAPER", "Safety violation: mode must be PAPER"
    assert cfg.live_trading_enabled is False, "Safety violation: live trading enabled"

    df_all = fetch_full_14d_candles(symbol=cfg.symbol, total_days=14, bootstrap_bars=450)
    
    # Slice exactly 450 bootstrap + 4032 forward test bars (14 days * 288)
    total_study_bars = 450 + (14 * 288)
    assert len(df_all) >= total_study_bars, f"Insufficient bars: got {len(df_all)}, needed {total_study_bars}"
    
    df_study = df_all.iloc[-total_study_bars:]
    bootstrap_df = df_study.iloc[:450]
    forward_df = df_study.iloc[450:]
    
    logger.info("Bootstrap window: %s to %s (%d bars)", bootstrap_df.index[0], bootstrap_df.index[-1], len(bootstrap_df))
    logger.info("Forward study window: %s to %s (%d bars = 14 days)", forward_df.index[0], forward_df.index[-1], len(forward_df))

    # Initialize isolated paper broker
    test_state_file = PROJECT_ROOT / "data" / "paper_forward_14d_state.json"
    if test_state_file.exists():
        test_state_file.unlink()
        
    broker = DeltaPaperBroker(cfg=cfg, state_file=test_state_file, initial_equity=10000.0)
    
    reports_dir = PROJECT_ROOT / "reports" / "forward_test_14day"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    daily_results: List[Dict[str, Any]] = []
    all_trade_records: List[Dict[str, Any]] = []
    all_signals_recorded: List[Dict[str, Any]] = []
    
    # Process 14 consecutive 24-hour days
    for day_idx in range(14):
        day_num = day_idx + 1
        day_slice = forward_df.iloc[day_idx * 288 : (day_idx + 1) * 288]
        day_start = day_slice.index[0]
        day_end = day_slice.index[-1]
        
        # Reset daily risk usage
        daily_trades_count = 0
        day_start_equity = broker.get_account_summary()["equity"]
        day_start_cash = broker.cash_balance
        
        regime_counts = {}
        daily_evaluations = 0
        daily_candidates = 0
        daily_rejections = {}
        daily_accepted_signals = []
        
        logger.info(">>> Processing Day %02d/14: %s to %s (%d bars) <<<", day_num, day_start, day_end, len(day_slice))
        
        for bar_idx in range(len(day_slice)):
            global_idx = 450 + (day_idx * 288) + bar_idx
            cur_bar = day_slice.iloc[bar_idx]
            bar_time = day_slice.index[bar_idx]
            window = df_study.iloc[:global_idx + 1]
            
            c = float(cur_bar["Close"])
            h = float(cur_bar["High"])
            l = float(cur_bar["Low"])
            
            # 1. Evaluate open position SL / TP
            if broker.open_position:
                closed = broker.evaluate_open_position_sl_tp(c, high=h, low=l, timestamp=str(bar_time))
                if closed:
                    all_trade_records.append(closed)
                    logger.info("  [TRADE CLOSED] Day %d (%s): %s via %s @ $%.2f (Net: $%.4f, Trade R: %+.2fR)",
                                day_num, bar_time, closed["direction"], closed["exit_reason"], closed["exit_price"], closed["net_pnl"], closed["trade_r_multiple"])
            
            # 2. Market Regime Detection
            regime_res = detect_market_regime(window, cfg)
            reg_name = regime_res.regime.value
            regime_counts[reg_name] = regime_counts.get(reg_name, 0) + 1
            daily_evaluations += 1
            
            # 3. Strategy Arbitration
            arb_res = arbitrate_strategies(window, cfg)
            winning_sig = arb_res.winning_signal
            
            # Record rejections
            for strat_name, rej_reason in arb_res.rejected_strategies:
                daily_rejections[rej_reason] = daily_rejections.get(rej_reason, 0) + 1
                
            if winning_sig and winning_sig.direction in ("LONG", "SHORT"):
                daily_candidates += 1
                sig_record = {
                    "day": day_num,
                    "bar_time": str(bar_time),
                    "strategy": winning_sig.strategy_name,
                    "direction": winning_sig.direction,
                    "entry": winning_sig.entry,
                    "sl": winning_sig.sl,
                    "tp": winning_sig.tp,
                    "r_multiple": winning_sig.r_multiple,
                    "regime": reg_name,
                    "reason": winning_sig.reason,
                    "accepted": False,
                    "rejection_reason": None,
                }
                
                # Check broker execution guards
                if broker.open_position is not None:
                    sig_record["rejection_reason"] = "Position already open"
                elif daily_trades_count >= cfg.max_trades_per_day:
                    sig_record["rejection_reason"] = f"Daily trade limit reached ({cfg.max_trades_per_day}/day)"
                else:
                    # Execute simulated trade
                    ok, msg, pos = broker.open_simulated_trade(
                        symbol=cfg.symbol,
                        direction=winning_sig.direction,
                        entry_price=winning_sig.entry,
                        stop_loss=winning_sig.sl,
                        take_profit=winning_sig.tp,
                        lots=1,
                        strategy_name=winning_sig.strategy_name,
                        signal_reason=winning_sig.reason,
                        candle_timestamp=str(bar_time),
                    )
                    if ok:
                        sig_record["accepted"] = True
                        sig_record["execution_id"] = pos["trade_id"]
                        daily_trades_count += 1
                        daily_accepted_signals.append(sig_record)
                        logger.info("  [TRADE OPENED] Day %d (%s): %s via %s @ $%.2f (SL: $%.2f, TP: $%.2f, Lots: 1)",
                                    day_num, bar_time, winning_sig.direction, winning_sig.strategy_name, pos["entry_price"], winning_sig.sl, winning_sig.tp)
                    else:
                        sig_record["rejection_reason"] = msg
                        
                all_signals_recorded.append(sig_record)
                
        # Day closing metrics
        day_end_summary = broker.get_account_summary()
        day_net_pnl = round(day_end_summary["equity"] - day_start_equity, 4)
        day_closed_trades = [t for t in broker.closed_trades if t.get("exit_time", "").startswith(str(day_start)[:10])]
        
        day_wins = sum(1 for t in day_closed_trades if t["net_pnl"] > 0)
        day_losses = sum(1 for t in day_closed_trades if t["net_pnl"] <= 0)
        
        day_result = {
            "day": day_num,
            "date": str(day_start)[:10],
            "start_ist": str(day_start),
            "end_ist": str(day_end),
            "start_equity": day_start_equity,
            "end_equity": day_end_summary["equity"],
            "net_pnl": day_net_pnl,
            "trades_count": len(day_closed_trades),
            "wins": day_wins,
            "losses": day_losses,
            "evaluations": daily_evaluations,
            "candidates": daily_candidates,
            "accepted_signals": len(daily_accepted_signals),
            "regime_distribution": regime_counts,
            "top_rejections": sorted(daily_rejections.items(), key=lambda x: x[1], reverse=True)[:5],
            "closed_trades": day_closed_trades,
            "open_position_at_eod": bool(broker.open_position),
        }
        daily_results.append(day_result)
        
        # Write individual daily markdown report
        daily_report_path = reports_dir / f"day_{day_num:02d}_report.md"
        write_daily_markdown_report(daily_report_path, day_result, cfg)

    # Final overall metrics
    final_summary = broker.get_account_summary()
    trades_list = broker.closed_trades
    total_trades = len(trades_list)
    winning_trades = [t for t in trades_list if t["net_pnl"] > 0]
    losing_trades = [t for t in trades_list if t["net_pnl"] <= 0]
    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
    gross_gains = sum(t["net_pnl"] for t in winning_trades)
    gross_losses = abs(sum(t["net_pnl"] for t in losing_trades))
    profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else (999.0 if gross_gains > 0 else 0.0)
    
    # R-multiple stats
    r_multiples = [t.get("trade_r_multiple", 0.0) for t in trades_list]
    avg_r = (sum(r_multiples) / total_trades) if total_trades > 0 else 0.0
    
    overall_results = {
        "study_metadata": {
            "symbol": cfg.symbol,
            "total_days": 14,
            "total_bars_evaluated": len(forward_df),
            "bootstrap_bars": len(bootstrap_df),
            "start_date": str(forward_df.index[0]),
            "end_date": str(forward_df.index[-1]),
        },
        "performance_summary": {
            "initial_equity": 10000.0,
            "final_equity": final_summary["equity"],
            "net_pnl": final_summary["realized_pnl"],
            "total_trades": total_trades,
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "average_r_multiple": round(avg_r, 2),
            "total_fees": final_summary["total_fees"],
            "total_slippage": final_summary["total_slippage"],
            "max_drawdown_pct": final_summary["max_drawdown_pct"],
            "max_drawdown_cash": final_summary["max_drawdown_cash"],
        },
        "daily_breakdown": daily_results,
        "closed_trades": trades_list,
        "signals": all_signals_recorded,
    }
    
    # Save full summary JSON
    summary_json_file = PROJECT_ROOT / "data" / "forward_test_14d_summary.json"
    with open(summary_json_file, "w", encoding="utf-8") as f:
        json.dump(overall_results, f, indent=2)
        
    # Save trades CSV
    if trades_list:
        trades_df = pd.DataFrame(trades_list)
        trades_csv_file = PROJECT_ROOT / "data" / "forward_test_14d_trades.csv"
        trades_df.to_csv(trades_csv_file, index=False)
        logger.info("Saved %d trades to %s", len(trades_df), trades_csv_file)
        
    logger.info("14-Day Forward Test complete. Total trades: %d, Net P&L: $%.4f, Win Rate: %.1f%%",
                total_trades, final_summary["realized_pnl"], win_rate)
    return overall_results


def write_daily_markdown_report(path: Path, result: Dict[str, Any], cfg: ORBSettings):
    """Write cleanly structured daily report markdown file."""
    lines = [
        f"# Forward Test Day {result['day']:02d} Daily Report ({result['date']})",
        "",
        f"- **Coverage**: `{result['start_ist']}` to `{result['end_ist']}` (288 completed 5m bars)",
        f"- **Start Equity**: `${result['start_equity']:,.4f}`",
        f"- **End Equity**: `${result['end_equity']:,.4f}`",
        f"- **Day Net P&L**: `${result['net_pnl']:+,.4f}`",
        f"- **Closed Trades Today**: `{result['trades_count']}` ({result['wins']}W / {result['losses']}L)",
        f"- **Candidates Evaluated**: `{result['candidates']}` | **Accepted**: `{result['accepted_signals']}`",
        "",
        "### Market Regime Distribution",
        "",
        "| Market Regime | Bar Count | Percentage |",
        "| :--- | :---: | :---: |",
    ]
    total_bars = sum(result["regime_distribution"].values())
    for reg, cnt in result["regime_distribution"].items():
        pct = (cnt / total_bars * 100.0) if total_bars > 0 else 0.0
        lines.append(f"| `{reg}` | {cnt} | {pct:.1f}% |")
        
    lines.extend([
        "",
        "### Top Rejection Reasons",
        "",
    ])
    for reason, cnt in result["top_rejections"]:
        lines.append(f"- **({cnt}x)** `{reason}`")
        
    lines.extend([
        "",
        "### Trades Executed Today",
        "",
    ])
    if result["closed_trades"]:
        lines.extend([
            "| Trade ID | Strategy | Side | Entry Fill | Exit Fill | Gross P&L | Fees | Slip | Net P&L | Trade R | Exit |",
            "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
        ])
        for t in result["closed_trades"]:
            lines.append(
                f"| `{t['trade_id']}` | `{t['strategy_name']}` | {t['direction']} | ${t['entry_price']:.2f} | "
                f"${t['exit_price']:.2f} | ${t['gross_pnl']:+.4f} | ${t['fees']:.4f} | ${t['slippage']:.4f} | "
                f"**${t['net_pnl']:+.4f}** | `{t.get('trade_r_multiple', 0.0):+.2f}R` | `{t['exit_reason']}` |"
            )
    else:
        lines.append("*Zero closed trades today. Strategy entry filters preserved capital against unconfirmed setups.*")
        
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    execute_14d_forward_test()

