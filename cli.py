"""CLI entry point for XAUTUSD ORB + EMA20 + Volume. Alert-only; never places orders."""
import argparse, json
from pathlib import Path
from datetime import datetime, timezone
from data.delta_candles import fetch_5m_candles, DeltaCandleError
from data.csv_loader import load_candles_csv
from strategy.orb_ema_volume import evaluate_orb
from risk.position_sizing import size_position, DailyRiskState
from journal.orb_journal import ORBJournal
from backtest.orb_backtest import run_backtest
from config.orb_settings import settings

DISCLAIMER = "Educational tool, not financial advice. PAPER/ALERT-ONLY; no order placement is implemented."

def show_signal(frame, equity: float = 10000.0, reserve_alert: bool = False):
    sig = evaluate_orb(frame)
    now = frame.index[-1].to_pydatetime() if len(frame) else datetime.now(timezone.utc)
    risk_state = DailyRiskState()
    risk_result = None
    if sig.direction in {"LONG", "SHORT"}:
        allowed, reason, _ = risk_state.can_trade(now)
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
    print("\n" + "="*64 + "\nXAUTUSD ORB + EMA20 + VOLUME | PAPER / ALERT-ONLY")
    print(f"Time: {sig.timestamp or 'n/a'} | ORB High: {sig.orb_high} | ORB Low: {sig.orb_low}")
    for key, val in sig.checklist.items():
        mark = "?" if val is None else ("x" if val else " ")
        print(f"[{mark}] {key.replace('_',' ').title()}")
    print(f"\n{sig.direction}: {sig.reason}")
    if sig.direction in {"LONG", "SHORT"}:
        print(json.dumps(risk_result.to_dict(), indent=2))
        print(f"Paper plan only: entry={sig.entry}, SL={sig.sl}, TP={sig.tp}; lots={risk_result.lots}; no order sent.")
    ORBJournal().log("signal" if sig.direction != "NO TRADE" else "skipped_setup", sig,
                      extra={"risk": risk_result.to_dict() if risk_result else None})
    print("\n" + DISCLAIMER)
    return sig

def main():
    p = argparse.ArgumentParser(description=DISCLAIMER)
    sub = p.add_subparsers(dest="command", required=True)
    live = sub.add_parser("once", help="Fetch current completed 5m candles and evaluate once")
    live.add_argument("--candles", type=int, default=500)
    live.add_argument("--symbol", default=settings.symbol)
    live.add_argument("--equity", type=float, default=10000.0, help="Paper equity in USD for risk sizing")
    watch = sub.add_parser("watch", help="Poll completed 5m candles and alert on each new candle")
    watch.add_argument("--candles", type=int, default=500)
    watch.add_argument("--symbol", default=settings.symbol)
    watch.add_argument("--equity", type=float, default=10000.0)
    watch.add_argument("--poll-seconds", type=int, default=60)
    csv = sub.add_parser("csv", help="Evaluate the latest candle from CSV")
    csv.add_argument("path")
    csv.add_argument("--equity", type=float, default=10000.0)
    bt = sub.add_parser("backtest", help="Replay a CSV of historical 5m candles")
    bt.add_argument("path")
    bt.add_argument("--equity", type=float, default=10000.0)
    result_cmd = sub.add_parser("record-result", help="Record P&L for an already reserved paper alert")
    result_cmd.add_argument("--pnl", type=float, required=True, help="Realized paper P&L in account currency")
    result_cmd.add_argument("--equity", type=float, required=True, help="Equity at start of the day")
    if hasattr(sub, "add_parser"):
        pass
    args = p.parse_args()
    if args.command == "once":
        try:
            frame = fetch_5m_candles(args.symbol, args.candles)
        except DeltaCandleError as exc:
            raise SystemExit(f"Data error: {exc}")
        show_signal(frame, args.equity, reserve_alert=True)
    elif args.command == "watch":
        if args.poll_seconds < 10:
            raise SystemExit("--poll-seconds must be at least 10 to avoid excessive API requests.")
        print("Watching completed 5m candles. Press Ctrl+C to stop. " + DISCLAIMER)
        last_timestamp = None
        try:
            import time
            while True:
                try:
                    frame = fetch_5m_candles(args.symbol, args.candles)
                    timestamp = str(frame.index[-1])
                    if timestamp != last_timestamp:
                        last_timestamp = timestamp
                        signal = show_signal(frame, args.equity, reserve_alert=True)
                        if signal.direction in {"LONG", "SHORT"}:
                            print("\\aALERT: valid paper setup completed; no order was placed.")
                except DeltaCandleError as exc:
                    print(f"Market data unavailable: {exc}")
                time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("\\nWatch stopped.")
    elif args.command == "csv":
        show_signal(load_candles_csv(args.path, settings.timezone), args.equity, reserve_alert=False)
    elif args.command == "record-result":
        state = DailyRiskState().record_result(args.pnl, args.equity)
        print(json.dumps(state, indent=2))
    else:
        result = run_backtest(load_candles_csv(args.path, settings.timezone), args.equity)
        print(json.dumps(result.to_dict(), indent=2, default=str))
        try:
            import plotly.graph_objects as go
            fig = go.Figure(go.Scatter(x=[p["date"] for p in result.equity_curve],
                                       y=[p["equity"] for p in result.equity_curve], mode="lines", name="Equity"))
            fig.update_layout(title="XAUTUSD ORB Backtest Equity Curve", xaxis_title="Day", yaxis_title="Equity")
            out = Path("data/orb_equity_curve.html")
            out.parent.mkdir(exist_ok=True)
            fig.write_html(out)
            print(f"Equity curve: {out}")
        except Exception as exc:
            print(f"Could not render equity chart: {exc}")
        print("\n" + DISCLAIMER)

if __name__ == "__main__":
    main()
