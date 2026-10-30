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
from indicators.orb_indicators import add_orb_indicators
from risk.position_sizing import size_position, DailyRiskState
from journal.orb_journal import ORBJournal
from backtest.orb_backtest import run_backtest
from zoneinfo import ZoneInfo
from datetime import time as time_of_day
from config.orb_settings import settings
from services.delta_account_service import DeltaAccountService
from services.delta_live_executor import DeltaLiveExecutor

DISCLAIMER = "Educational research tool, not financial advice. Mode determines execution pathway (PAPER / ALERT_ONLY / LIVE)."


def get_mode_banner(mode: str, kill_switch: bool = False) -> str:
    """Return explicit, unambiguous operational mode and disclaimer banner."""
    ks_str = " | ⚠️ EMERGENCY KILL SWITCH ACTIVE (All order entry blocked)" if kill_switch else ""
    if mode == "LIVE":
        return f"⚠️  LIVE TRADING MODE ACTIVE: Real capital at risk. Live orders are sent to Delta Exchange India upon confirmed signal.{ks_str}"
    elif mode == "ALERT_ONLY":
        return f"ℹ️  ALERT-ONLY MODE: Real market data & signals evaluated. No orders are sent to the exchange.{ks_str}"
    return f"🛡️  PAPER TRADING MODE: Educational simulation. All orders, balances, and P&L tracking are virtual.{ks_str}"



def show_signal(frame: pd.DataFrame, equity: float = 10000.0, reserve_alert: bool = False):
    sig = evaluate_orb(frame)
    now = frame.index[-1].to_pydatetime() if len(frame) else datetime.now(timezone.utc)
    risk_state = DailyRiskState()
    risk_result = None

    # In LIVE mode, strictly query verified exchange equity; never use hardcoded paper equity
    effective_equity = equity
    if settings.mode == "LIVE":
        account_state = DeltaAccountService().get_account_state()
        if not account_state.is_valid:
            print(f"\n[CRITICAL ERROR] LIVE MODE ACTIVE BUT ACCOUNT LOOKUP FAILED: {account_state.error}")
            print("Refusing to proceed with trade evaluation without verified live equity.\n")
            sig.direction, sig.entry, sig.sl, sig.tp, sig.r_multiple = "NO TRADE", None, None, None, 0.0
            sig.reason = f"NO TRADE: Real account verification failed: {account_state.error}"
            return sig
        effective_equity = float(account_state.equity)

    if sig.direction in {"LONG", "SHORT"}:
        allowed, reason, guard_state = risk_state.can_trade(now)
        if not allowed:
            sig.direction, sig.entry, sig.sl, sig.tp, sig.r_multiple = "NO TRADE", None, None, None, 0.0
            sig.reason = f"NO TRADE: {reason}"
            sig.checklist["risk_ok"] = False
        else:
            risk_result = size_position(sig.entry, sig.sl, effective_equity)
            sig.checklist["risk_ok"] = risk_result.approved
            if not risk_result.approved:
                sig.direction, sig.entry, sig.sl, sig.tp, sig.r_multiple = "NO TRADE", None, None, None, 0.0
                sig.reason = risk_result.reason
            elif reserve_alert:
                if settings.mode == "LIVE":
                    executor = DeltaLiveExecutor()
                    exec_res = executor.execute_trade(
                        symbol=settings.symbol,
                        direction=sig.direction,
                        entry=sig.entry,
                        stop_loss=sig.sl,
                        take_profit=sig.tp,
                        candle_timestamp=sig.timestamp,
                    )
                    print("\n" + "#" * 70)
                    print(f"LIVE EXECUTION STATUS: {exec_res.get('status')}")
                    print(f"RESULT: {exec_res.get('message', exec_res.get('error'))}")
                    print("#" * 70)
                else:
                    try:
                        risk_state.reserve_trade(now)
                    except RuntimeError as exc:
                        sig.direction, sig.entry, sig.sl, sig.tp, sig.r_multiple = "NO TRADE", None, None, None, 0.0
                        sig.reason = f"NO TRADE: {exc}"
                        sig.checklist["risk_ok"] = False

    enriched_frame = frame
    if not frame.empty:
        try:
            enriched_frame = add_orb_indicators(
                frame.copy(),
                ema_period=settings.ema_period,
                volume_period=settings.volume_period,
                atr_period=settings.atr_period,
            )
        except Exception:
            enriched_frame = frame

    last_row = enriched_frame.iloc[-1] if not enriched_frame.empty else None
    ema_val = f"{last_row['EMA20']:.2f}" if (last_row is not None and "EMA20" in last_row and pd.notna(last_row["EMA20"])) else "N/A"
    ema_slope_val = f"{last_row['EMA20_Slope']:+.4f}" if (last_row is not None and "EMA20_Slope" in last_row and pd.notna(last_row["EMA20_Slope"])) else "N/A"
    vol_val = f"{last_row['Volume']:,.0f}" if (last_row is not None and "Volume" in last_row) else "N/A"
    vol_avg_val = f"{last_row['VolumeAvg20Prior']:,.0f}" if (last_row is not None and "VolumeAvg20Prior" in last_row and pd.notna(last_row["VolumeAvg20Prior"])) else "N/A"

    mode_tag = f"MODE: {settings.mode}"
    if settings.kill_switch_active:
        mode_tag += " | ⚠️ KILL SWITCH ACTIVE"
    print("\n" + "=" * 70)
    print(f"XAUTUSD (TETHER GOLD) ORB + EMA20 + VOLUME | {mode_tag}")
    print("=" * 70)
    print(f"Symbol: {settings.symbol} | Timezone: {settings.timezone}")
    print(f"Last Completed Candle: {sig.timestamp or 'N/A'}")
    print(f"ORB High: {sig.orb_high if sig.orb_high is not None else 'Pending'} | ORB Low: {sig.orb_low if sig.orb_low is not None else 'Pending'}")
    print(f"EMA20: {ema_val} | EMA Slope: {ema_slope_val}")
    print(f"Candle Volume: {vol_val} | Prior 20-Bar Avg Volume: {vol_avg_val}")
    print("-" * 70)
    print("Strategy Checklist:")
    checklist_reasons = getattr(sig, "checklist_reasons", {}) or {}
    for key, val in sig.checklist.items():
        mark = "?" if val is None else ("PASS" if val else "FAIL")
        reason_note = f" ({checklist_reasons.get(key)})" if key in checklist_reasons and checklist_reasons.get(key) != "PASSED" else ""
        print(f"  [{mark:4s}] {key.replace('_', ' ').title()}{reason_note}")
    print("-" * 70)
    print(f"Status: {sig.direction}")
    diag_code = getattr(sig, "diagnostic_code", None)
    if diag_code:
        print(f"Diagnostic Code: {diag_code}")
    print(f"Engine Decision: {sig.reason}")

    if sig.direction in {"LONG", "SHORT"} and risk_result:
        print("\nSizing & Risk Parameters:")
        print(f"  Entry: {sig.entry:.2f} | Stop Loss: {sig.sl:.2f} | Take Profit: {sig.tp:.2f}")
        print(f"  Reward-to-Risk: {sig.r_multiple:.2f}R (net of fees, spread and slippage)")
        print(f"  Approved Lots: {risk_result.lots} (Contract Size: {settings.contract_size_xaut} XAUT/lot)")
        print(f"  Estimated SL Loss: ${risk_result.estimated_loss_at_sl:.2f} (Risk Budget: ${risk_result.max_risk_cash:.2f})")
        print(f"  Estimated Margin Required: ${risk_result.estimated_margin:.2f}")
        if settings.mode == "LIVE":
            print("  NOTICE: LIVE MODE ACTIVE. Real order submission pathway enabled.")
        else:
            print("  NOTICE: This is an alert/paper plan only. No real order was sent to the exchange.")

    # Guard state summary
    can_trade_now, guard_msg, state_data = risk_state.can_trade(now)
    print(f"\nDaily Risk Status: {guard_msg}")
    print(f"  Trades Today: {state_data.get('trades', 0)}/{settings.max_trades_per_day} | Realized P&L: {state_data.get('realized_pnl', 0.0) * 100:.2f}%")

    ORBJournal().log(
        "signal" if sig.direction != "NO TRADE" else "skipped_setup",
        sig,
        extra={"risk": risk_result.to_dict() if risk_result else None},
    )

    print("\n" + get_mode_banner(settings.mode, settings.kill_switch_active))
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

    # 7. Account verification (read-only)
    acc_cmd = sub.add_parser("account", help="Inspect verified real Delta Exchange equity and margin (read-only)")
    acc_cmd.add_argument("--asset", default="USD", help="Target settling asset symbol (e.g. USD, USDT)")

    # 8. Order reconciliation
    rec_cmd = sub.add_parser("reconcile", help="Reconcile local order states with Delta Exchange")
    rec_cmd.add_argument("--order-id", default=None, help="Specific client_order_id to reconcile")

    # 9. Emergency kill switch cancellation
    ks_cmd = sub.add_parser("emergency-cancel", help="Emergency kill-switch: cancel all open orders on Delta Exchange")
    ks_cmd.add_argument("--confirm", action="store_true", help="Explicit confirmation to dispatch emergency cancellation")

    args = p.parse_args()

    if args.command == "reconcile":
        from services.delta_reconciliation_service import DeltaReconciliationService
        from market_data.delta_exchange import DeltaExchangeClient
        rec_svc = DeltaReconciliationService()
        client = DeltaExchangeClient()
        if args.order_id:
            res = rec_svc.reconcile_with_exchange(args.order_id, client)
            print(f"Reconciliation result for {args.order_id}:")
            print(json.dumps(res.to_dict() if res else {}, indent=2))
        else:
            updated = rec_svc.reconcile_all_pending(client)
            active = rec_svc.get_active_orders()
            print(f"\nReconciled {len(updated)} pending order(s). Active orders in local store: {len(active)}")
            for o in active:
                print(f"  [{o.status}] {o.client_order_id} | {o.side.upper()} {o.size} @ {o.limit_price} | Exch ID: {o.exchange_order_id or 'Pending'}")

    elif args.command == "emergency-cancel":
        if not args.confirm:
            print("Action aborted. Pass --confirm to dispatch emergency cancellation of all orders.")
        else:
            executor = DeltaLiveExecutor()
            res = executor.cancel_all_product_orders()
            print("\n" + "#" * 70)
            print("EMERGENCY KILL-SWITCH DISPATCHED")
            print(f"Exchange response: {res.get('exchange_response')}")
            print(f"Local orders marked cancelled: {res.get('orders_marked_cancelled')}")
            print("#" * 70)

    elif args.command == "account":
        svc = DeltaAccountService()
        acc = svc.get_account_state(settling_asset=args.asset)
        print("\n" + "=" * 70)
        print("DELTA EXCHANGE REAL ACCOUNT TELEMETRY (READ-ONLY)")
        print("=" * 70)
        if not acc.is_valid:
            print(f"FAILED TO RETRIEVE ACCOUNT TELEMETRY:\n  {acc.error}")
        else:
            print("Status:                AUTHENTICATED & VERIFIED")
            print(f"Verified Total Equity: ${float(acc.equity):,.2f} {acc.currency}")
            print(f"Free / Available Margin: ${float(acc.available_margin):,.2f} {acc.currency}")
            print(f"Used Margin:           ${float(acc.used_margin):,.2f} {acc.currency}")
            print(f"Unrealized P&L:        ${float(acc.unrealized_pnl):+,.2f} {acc.currency}")
            print(f"Open Positions:        {len(acc.open_positions)}")
            print(f"Pending Orders:        {len(acc.open_orders)}")
            print(f"Last Synced:           {acc.last_synced_at.strftime('%Y-%m-%d %H:%M:%S UTC') if acc.last_synced_at else 'N/A'}")
            if acc.wallets:
                print("\nWallet Breakdown:")
                for sym, w in acc.wallets.items():
                    print(f"  [{sym}] Balance: {float(w.wallet_balance):,.4f} | Avail: {float(w.available_balance):,.4f} | Margin: {float(w.position_margin):,.4f}")
        print("=" * 70)

    if args.command == "once":
        try:
            frame = fetch_5m_candles(args.symbol, args.candles)
        except DeltaCandleError as exc:
            raise SystemExit(f"Market Data Error: {exc}")
        show_signal(frame, args.equity, reserve_alert=True)

    elif args.command == "watch":
        if args.poll_seconds < 10:
            raise SystemExit("--poll-seconds must be at least 10 to avoid excessive API requests.")
        print(f"Starting continuous alert polling for {args.symbol} (interval: {args.poll_seconds}s). Press Ctrl+C to stop.\n{get_mode_banner(settings.mode, settings.kill_switch_active)}")
        last_timestamp = None
        last_direction = None
        executor = DeltaLiveExecutor() if settings.mode == "LIVE" else None
        try:
            while True:
                now_dt = datetime.now()
                sec_to_next_bar = (5 - (now_dt.minute % 5) - 1) * 60 + (60 - now_dt.second)
                mins_left = sec_to_next_bar // 60
                secs_left = sec_to_next_bar % 60

                try:
                    frame = fetch_5m_candles(args.symbol, args.candles)
                    cur_ts = str(frame.index[-1]) if not frame.empty else None
                    cur_price = float(frame["Close"].iloc[-1]) if not frame.empty else 0.0

                    if cur_ts and cur_ts != last_timestamp:
                        last_timestamp = cur_ts
                        print("\n" + "-" * 70)
                        print(f"[{now_dt.strftime('%H:%M:%S')}] New completed 5m candle detected: {cur_ts} (Total completed bars: {len(frame)})")
                        sig = show_signal(frame, args.equity, reserve_alert=True)
                        if sig.direction in {"LONG", "SHORT"}:
                            if sig.direction != last_direction:
                                last_direction = sig.direction
                                alert_mode = "LIVE ORDER SUBMITTED" if settings.mode == "LIVE" else "Paper Alert Only"
                                print(f"\n*** ALERT: New {sig.direction} signal confirmed at {cur_ts}! ({alert_mode}) ***\n")
                            else:
                                print(f"\n[INFO] {sig.direction} signal active at {cur_ts} (already alerted for this sequence).\n")
                    else:
                        ts_str = now_dt.strftime("%H:%M:%S")
                        print(f"[{ts_str}] 💓 Heartbeat: Price ${cur_price:.2f} | Next 5m bar in ~{mins_left:02d}m {secs_left:02d}s | Watching {args.symbol} ({settings.mode})   ", end="\r", flush=True)

                    if executor and settings.mode == "LIVE":
                        stale = executor.cancel_stale_orders(max_age_minutes=15)
                        if stale:
                            print(f"\n[{now_dt.strftime('%H:%M:%S')}] Auto-cancelled {len(stale)} stale unfilled limit order(s).")

                except DeltaCandleError as exc:
                    print(f"\n[{now_dt.strftime('%H:%M:%S')}] Market data temporarily unavailable: {exc}")
                except Exception as exc:
                    print(f"\n[{now_dt.strftime('%H:%M:%S')}] Polling glitch: {exc}. Reconnecting in {args.poll_seconds}s...")

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
