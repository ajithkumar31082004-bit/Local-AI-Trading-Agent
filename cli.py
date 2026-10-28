"""CLI entry point for XAUTUSD ORB + EMA20 + Volume. Alert-only; never places live orders."""
import argparse
import json
import time
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from data.delta_candles import fetch_5m_candles, DeltaCandleError
from data.csv_loader import load_candles_csv
from strategy.orb_ema_volume import evaluate_orb
from risk.position_sizing import size_position, DailyRiskState
from journal.orb_journal import ORBJournal
from backtest.orb_backtest import run_backtest
from config.orb_settings import settings

DISCLAIMER = "Educational research tool, not financial advice. PAPER / ALERT-ONLY; no live orders are placed."


def show_signal(frame: pd.DataFrame, equity: float = 10000.0, reserve_alert: bool = False):
    sig = evaluate_orb(frame)
    now = frame.index[-1].to_pydatetime() if len(frame) else datetime.now(timezone.utc)
    risk_state = DailyRiskState()
    risk_result = None

    if sig.direction in {"LONG", "SHORT"}:
        allowed, reason, guard_state = risk_state.can_trade(now)
        if not allowed:
            sig.direction, sig.entry, sig.sl, sig.tp, sig.r_multiple = "NO TRADE", None, None, None, 0.0
            sig.reason = f"NO TRADE: {reason}"
            sig.checklist["risk_ok"] = False
        else:
            risk_result = size_position(sig.entry, sig.sl, equity)
            sig.checklist["risk_ok"] = risk_result.approved
            if not risk_result.approved:
                sig.direction, sig.entry, sig.sl, sig.tp, sig.r_multiple = "NO TRADE", None, None, None, 0.0
                sig.reason = risk_result.reason
            elif reserve_alert:
                try:
                    risk_state.reserve_trade(now)
                except RuntimeError as exc:
                    sig.direction, sig.entry, sig.sl, sig.tp, sig.r_multiple = "NO TRADE", None, None, None, 0.0
                    sig.reason = f"NO TRADE: {exc}"
                    sig.checklist["risk_ok"] = False

    last_row = frame.iloc[-1] if not frame.empty else None
    ema_val = f"{last_row['EMA20']:.2f}" if (last_row is not None and "EMA20" in last_row and pd.notna(last_row["EMA20"])) else "N/A"
    ema_slope_val = f"{last_row['EMA20_Slope']:+.4f}" if (last_row is not None and "EMA20_Slope" in last_row and pd.notna(last_row["EMA20_Slope"])) else "N/A"
    vol_val = f"{last_row['Volume']:,.0f}" if (last_row is not None and "Volume" in last_row) else "N/A"
    vol_avg_val = f"{last_row['VolumeAvg20Prior']:,.0f}" if (last_row is not None and "VolumeAvg20Prior" in last_row and pd.notna(last_row["VolumeAvg20Prior"])) else "N/A"

    print("\n" + "=" * 70)
    print("XAUTUSD (TETHER GOLD) ORB + EMA20 + VOLUME | PAPER / ALERT-ONLY")
    print("=" * 70)
    print(f"Symbol: {settings.symbol} | Timezone: {settings.timezone}")
    print(f"Last Completed Candle: {sig.timestamp or 'N/A'}")
    print(f"ORB High: {sig.orb_high if sig.orb_high is not None else 'Pending'} | ORB Low: {sig.orb_low if sig.orb_low is not None else 'Pending'}")
    print(f"EMA20: {ema_val} | EMA Slope: {ema_slope_val}")
    print(f"Candle Volume: {vol_val} | Prior 20-Bar Avg Volume: {vol_avg_val}")
    print("-" * 70)
    print("Strategy Checklist:")
    for key, val in sig.checklist.items():
        mark = "?" if val is None else ("PASS" if val else "FAIL")
        print(f"  [{mark:4s}] {key.replace('_', ' ').title()}")
    print("-" * 70)
    print(f"Status: {sig.direction}")
    print(f"Engine Decision: {sig.reason}")

    if sig.direction in {"LONG", "SHORT"} and risk_result:
        print("\nPaper Sizing & Risk Parameters:")
        print(f"  Entry: {sig.entry:.2f} | Stop Loss: {sig.sl:.2f} | Take Profit: {sig.tp:.2f}")
        print(f"  Reward-to-Risk: {sig.r_multiple:.2f}R (net of fees, spread and slippage)")
        print(f"  Approved Lots: {risk_result.lots} (Contract Size: {settings.contract_size_xaut} XAUT/lot)")
        print(f"  Estimated SL Loss: ${risk_result.estimated_loss_at_sl:.2f} (Risk Budget: ${risk_result.max_risk_cash:.2f})")
        print(f"  Estimated Margin Required: ${risk_result.estimated_margin:.2f}")
        print("  NOTICE: This is an alert/paper plan only. No order was sent to the exchange.")

    # Guard state summary
    can_trade_now, guard_msg, state_data = risk_state.can_trade(now)
    print(f"\nDaily Risk Status: {guard_msg}")
    print(f"  Trades Today: {state_data.get('trades', 0)}/{settings.max_trades_per_day} | Realized P&L: {state_data.get('realized_pnl', 0.0) * 100:.2f}%")

    ORBJournal().log(
        "signal" if sig.direction != "NO TRADE" else "skipped_setup",
        sig,
        extra={"risk": risk_result.to_dict() if risk_result else None},
    )

    print("\n" + DISCLAIMER)
    print("=" * 70)
    return sig


def main():
    p = argparse.ArgumentParser(description=DISCLAIMER)
    sub = p.add_subparsers(dest="command", required=True)

    # 1. Once evaluation
    live = sub.add_parser("once", help="Fetch current completed 5m candles and evaluate once")
    live.add_argument("--candles", type=int, default=500, help="Number of lookback candles to fetch")
    live.add_argument("--symbol", default=settings.symbol, help="Exchange product symbol")
    live.add_argument("--equity", type=float, default=10000.0, help="Paper account equity in USD")

    # 2. Continuous watch mode
    watch = sub.add_parser("watch", help="Poll completed 5m candles and alert on each new completed bar")
    watch.add_argument("--candles", type=int, default=500)
    watch.add_argument("--symbol", default=settings.symbol)
    watch.add_argument("--equity", type=float, default=10000.0)
    watch.add_argument("--poll-seconds", type=int, default=60, help="Polling interval in seconds (minimum 10s)")

    # 3. CSV evaluation
    csv = sub.add_parser("csv", help="Evaluate the latest completed candle from a local CSV")
    csv.add_argument("path", help="Path to local OHLCV CSV")
    csv.add_argument("--equity", type=float, default=10000.0)

    # 4. Historical Backtest
    bt = sub.add_parser("backtest", help="Replay a CSV of historical 5m candles with cost and risk rules")
    bt.add_argument("path", help="Path to local OHLCV CSV")
    bt.add_argument("--equity", type=float, default=10000.0)
    bt.add_argument("--chart", default="data/orb_equity_curve.html", help="Path to save Plotly equity curve HTML")

    # 5. Record result
    result_cmd = sub.add_parser("record-result", help="Record P&L for a completed paper trade")
    result_cmd.add_argument("--pnl", type=float, required=True, help="Realized paper P&L in USD")
    result_cmd.add_argument("--equity", type=float, required=True, help="Equity at start of day in USD")

    # 6. Journal export
    j_cmd = sub.add_parser("journal-export", help="Export SQLite journal records to CSV")
    j_cmd.add_argument("--out", default="data/orb_journal_export.csv", help="Output CSV path")

    args = p.parse_args()

    if args.command == "once":
        try:
            frame = fetch_5m_candles(args.symbol, args.candles)
        except DeltaCandleError as exc:
            raise SystemExit(f"Market Data Error: {exc}")
        show_signal(frame, args.equity, reserve_alert=True)

    elif args.command == "watch":
        if args.poll_seconds < 10:
            raise SystemExit("--poll-seconds must be at least 10 to avoid excessive API requests.")
        print(f"Starting continuous alert polling for {args.symbol} (interval: {args.poll_seconds}s). Press Ctrl+C to stop.\n{DISCLAIMER}")
        last_timestamp = None
        last_direction = None
        try:
            while True:
                try:
                    frame = fetch_5m_candles(args.symbol, args.candles)
                    cur_ts = str(frame.index[-1])
                    if cur_ts != last_timestamp:
                        last_timestamp = cur_ts
                        sig = show_signal(frame, args.equity, reserve_alert=True)
                        if sig.direction in {"LONG", "SHORT"} and sig.direction != last_direction:
                            last_direction = sig.direction
                            print(f"\n*** ALERT: New {sig.direction} signal confirmed at {cur_ts}! (Paper Alert Only) ***\n")
                except DeltaCandleError as exc:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Market data temporarily unavailable: {exc}")
                time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("\nContinuous polling stopped by user.")

    elif args.command == "csv":
        frame = load_candles_csv(args.path, settings.timezone)
        show_signal(frame, args.equity, reserve_alert=False)

    elif args.command == "record-result":
        state = DailyRiskState().record_result(args.pnl, args.equity)
        ORBJournal().log("paper_result", {
            "direction": "PAPER_RESULT",
            "reason": "Recorded paper trade outcome; no live exchange order placed.",
            "pnl": args.pnl,
            "equity_at_start": args.equity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        print("Paper trade outcome recorded successfully:")
        print(json.dumps(state, indent=2))

    elif args.command == "journal-export":
        out_path = ORBJournal().export_csv(args.out)
        print(f"Journal successfully exported to: {out_path}")

    elif args.command == "backtest":
        frame = load_candles_csv(args.path, settings.timezone)
        result = run_backtest(frame, args.equity, chart_output_path=args.chart)
        print("\n" + "=" * 60)
        print("XAUTUSD ORB STRATEGY BACKTEST RESULTS")
        print("=" * 60)
        print(f"Total Days Replayed:       {result.days}")
        print(f"Days Traded:               {result.trades}")
        print(f"Skipped Days:              {result.skipped_days}")
        print(f"Win Rate:                  {result.win_rate_pct:.2f}%")
        print(f"Average R-Multiple:        {result.average_r:+.4f}R")
        print(f"Profit Factor:             {result.profit_factor:.2f}")
        print(f"Maximum Drawdown:          {result.max_drawdown_pct:.2f}%")
        print(f"Longest Losing Streak:     {result.longest_losing_streak} trade(s)")
        print(f"Total Cumulative R:        {result.total_r:+.4f}R")
        print(f"Net P&L:                   ${result.net_pnl:+,.2f}")
        print(f"Interactive Equity Curve:  {args.chart}")
        print("=" * 60)
        print(DISCLAIMER)


if __name__ == "__main__":
    main()
