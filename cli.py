"""CLI entry point for XAUTUSD ORB + EMA20 + Volume. Alert-only; never places orders."""
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from data.delta_candles import fetch_5m_candles, DeltaCandleError
from data.csv_loader import load_candles_csv
from strategy.orb_ema_volume import evaluate_orb
from risk.position_sizing import size_position, DailyRiskState
from journal.orb_journal import ORBJournal
from backtest.orb_backtest import run_backtest
from config.orb_settings import settings

DISCLAIMER="Educational tool, not financial advice. PAPER/ALERT-ONLY; no order placement is implemented."

def show_signal(frame):
    sig=evaluate_orb(frame)
    print("\n"+"="*64+"\nXAUTUSD ORB + EMA20 + VOLUME | PAPER / ALERT-ONLY")
    print(f"Time: {sig.timestamp or 'n/a'} | ORB High: {sig.orb_high} | ORB Low: {sig.orb_low}")
    for key,val in sig.checklist.items(): print(f"[{'x' if val else ' '}] {key.replace('_',' ').title()}")
    print(f"\n{sig.direction}: {sig.reason}")
    if sig.direction in {"LONG","SHORT"}:
        equity=float(input("Paper account equity for risk calculation (number): ").strip())
        risk=size_position(sig.entry,sig.sl,equity)
        print(json.dumps(risk.to_dict(),indent=2))
        if risk.approved: print(f"Paper-only plan: entry={sig.entry}, SL={sig.sl}, TP={sig.tp}; no order sent.")
    ORBJournal().log("signal" if sig.direction!="NO TRADE" else "skipped_setup",sig)
    print("\n"+DISCLAIMER)
    return sig

def main():
    p=argparse.ArgumentParser(description=DISCLAIMER)
    sub=p.add_subparsers(dest="command",required=True)
    live=sub.add_parser("once",help="Fetch current completed 5m candles and evaluate once")
    live.add_argument("--candles",type=int,default=500)
    live.add_argument("--symbol",default=settings.symbol)
    csv=sub.add_parser("csv",help="Evaluate the latest candle from CSV")
    csv.add_argument("path")
    bt=sub.add_parser("backtest",help="Replay a CSV of historical 5m candles")
    bt.add_argument("path"); bt.add_argument("--equity",type=float,default=10000.0)
    args=p.parse_args()
    if args.command=="once":
        try: frame=fetch_5m_candles(args.symbol,args.candles)
        except DeltaCandleError as e: raise SystemExit(f"Data error: {e}")
        show_signal(frame)
    elif args.command=="csv":
        show_signal(load_candles_csv(args.path,settings.timezone))
    else:
        result=run_backtest(load_candles_csv(args.path,settings.timezone),args.equity)
        print(json.dumps(result.to_dict(),indent=2,default=str))
        try:
            import plotly.graph_objects as go
            fig=go.Figure(go.Scatter(x=[p["date"] for p in result.equity_curve],y=[p["equity"] for p in result.equity_curve],mode="lines",name="Equity"))
            fig.update_layout(title="XAUTUSD ORB Backtest Equity Curve",xaxis_title="Day",yaxis_title="Equity")
            out=Path("data/orb_equity_curve.html"); out.parent.mkdir(exist_ok=True)
            fig.write_html(out); print(f"Equity curve: {out}")
        except Exception as exc: print(f"Could not render equity chart: {exc}")
        print("\n"+DISCLAIMER)

if __name__=="__main__": main()
