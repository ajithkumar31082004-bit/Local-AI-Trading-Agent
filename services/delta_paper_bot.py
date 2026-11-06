"""Live Paper Trading Orchestration Engine for XAUTUSD on Delta Exchange India.

Connects real-time public WebSocket market data with Strategy Intelligence V2
and local simulated execution. NEVER PLACES REAL ORDERS.
"""
import logging
import threading
import time
from datetime import datetime, time as time_of_day, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo
import pandas as pd

from config.orb_settings import ORBSettings, settings as default_orb_settings
from market_data.delta_live_feed import DeltaLiveMarketDataFeed
from services.delta_paper_broker import DeltaPaperBroker, PaperSafetyViolation
from strategy.arbitrator import arbitrate_strategies, ArbitrationResult
from strategy.orb_ema_volume import evaluate_orb, Signal
from strategy.trend_following import evaluate_trend_following
from strategy.vwap_ema import evaluate_vwap_ema
from risk.position_sizing import size_position, DailyRiskState, RiskDecision
from risk.position_size_guard import (
    validate_paper_order_lots,
    PositionSizeViolation,
    APPROVED_MAX_PAPER_LOTS,
)
from journal.orb_journal import ORBJournal

logger = logging.getLogger(__name__)


class DeltaPaperBot:
    """Orchestrates real-time market data feed, Strategy Intelligence V2, and paper broker."""

    _instance: Optional["DeltaPaperBot"] = None
    _singleton_lock = threading.Lock()

    @classmethod
    def get_instance(cls, cfg: Optional[ORBSettings] = None) -> "DeltaPaperBot":
        """Get or create singleton instance of the paper trading bot."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(cfg=cfg)
            return cls._instance

    def __init__(
        self,
        cfg: Optional[ORBSettings] = None,
        feed: Optional[DeltaLiveMarketDataFeed] = None,
        broker: Optional[DeltaPaperBroker] = None,
    ):
        self.cfg = cfg or default_orb_settings

        # Hard safety validation
        if self.cfg.mode != "PAPER":
            raise PaperSafetyViolation(f"DeltaPaperBot requires mode='PAPER', got '{self.cfg.mode}'. Refusing to run.")
        if getattr(self.cfg, "live_trading_enabled", False):
            raise PaperSafetyViolation("CRITICAL: LIVE_TRADING_ENABLED is true. DeltaPaperBot enforces simulated trading only.")

        self.feed = feed or DeltaLiveMarketDataFeed(cfg=self.cfg)
        self.broker = broker or DeltaPaperBroker(cfg=self.cfg)
        self.risk_state = DailyRiskState(path=self.cfg.state_path, cfg=self.cfg)
        self.journal = ORBJournal(path=self.cfg.journal_path)

        self._lock = threading.RLock()
        self._is_running = False
        self._strategy_type = getattr(self.cfg, "strategy_type", "PORTFOLIO").upper()

        # Telemetry cache
        self._last_signal: Optional[Signal] = None
        self._last_arbitration: Optional[ArbitrationResult] = None
        self._last_evaluation_time: Optional[datetime] = None
        self._last_rejection_reason: str = "Awaiting first completed candle"
        self._bot_log: List[Dict[str, Any]] = []

        # Session boundaries in configured timezone
        self.tz = ZoneInfo(self.cfg.timezone)
        self.session_start_t = time_of_day.fromisoformat(self.cfg.session_start)
        min_offset = self.cfg.no_entry_minutes_after_open
        earliest_min = self.session_start_t.hour * 60 + self.session_start_t.minute + min_offset
        self.earliest_entry_t = time_of_day(hour=min(23, earliest_min // 60), minute=earliest_min % 60)
        self.last_entry_t = time_of_day.fromisoformat(self.cfg.last_entry_time)
        self.session_end_t = time_of_day.fromisoformat(self.cfg.session_end)

    # -------------------------------------------------------------------------
    # Lifecycle Controls
    # -------------------------------------------------------------------------

    def start(self, bootstrap_lookback: Optional[int] = None) -> bool:
        """Start live market data feed and register event callbacks."""
        target_lookback = bootstrap_lookback or getattr(self.cfg, "bootstrap_lookback_candles", 450)
        with self._lock:
            if self._is_running:
                logger.info("DeltaPaperBot is already running.")
                return True

            logger.info("Starting DeltaPaperBot for %s [Mode: %s, Strategy: %s, Bootstrap: %d bars]...", self.cfg.symbol, self.cfg.mode, self._strategy_type, target_lookback)
            self._log_event("SYSTEM", f"Starting DeltaPaperBot for {self.cfg.symbol} in PAPER mode.")

            # Register callbacks with feed
            self.feed.register_candle_callback(self._on_completed_candle)
            self.feed.register_tick_callback(self._on_tick)

            # Start real-time feed
            self.feed.start(bootstrap_lookback=target_lookback)
            self._is_running = True

            # Evaluate initial historical batch to initialize signal state
            initial_5m = self.feed.get_completed_5m_candles()
            if not initial_5m.empty:
                logger.info("Evaluating initial completed candle batch (%d bars)...", len(initial_5m))
                self._evaluate_strategy(initial_5m)

            return True

    def stop(self) -> None:
        """Stop paper bot and disconnect live feeds."""
        with self._lock:
            self._is_running = False
            self.feed.stop()
            self._log_event("SYSTEM", "DeltaPaperBot stopped.")
            logger.info("DeltaPaperBot stopped.")

    # -------------------------------------------------------------------------
    # Event Handlers (Ticks & Completed Candles)
    # -------------------------------------------------------------------------

    def _on_tick(self, price: float, ticker: Dict[str, Any]) -> None:
        """Check active position SL/TP on price tick."""
        if not self._is_running:
            return

        now_tz = datetime.now(self.tz)

        # Check session end (23:30 IST) liquidation
        if now_tz.time() >= self.session_end_t and self.broker.open_position is not None:
            logger.info("Session end cutoff reached (%s). Liquidating open simulated position.", self.session_end_t)
            _, _, closed_trade = self.broker.close_simulated_trade(
                exit_price=price,
                exit_reason="SESSION_END",
                exit_timestamp=now_tz.isoformat(),
            )
            if closed_trade:
                self._log_event("EXIT", f"Session End: Liquidated {closed_trade['direction']} @ ${closed_trade['exit_price']:.2f} (Net: ${closed_trade['net_pnl']:+.2f})")
            return

        # Check Stop Loss / Take Profit collision
        closed_trade = self.broker.evaluate_open_position_sl_tp(
            current_price=price,
            timestamp=now_tz.isoformat(),
        )
        if closed_trade:
            self._log_event(
                "EXIT",
                f"Position Closed via {closed_trade['exit_reason']} @ ${closed_trade['exit_price']:.2f} "
                f"[Net P&L: ${closed_trade['net_pnl']:+.2f} ({closed_trade['r_multiple']:+.2f}R)]",
            )
            # Record result in daily risk state
            self.risk_state.record_result(closed_trade["net_pnl"], self.broker.initial_equity)

    def _on_completed_candle(self, df_5m: pd.DataFrame, df_15m: pd.DataFrame, latest_bar: Dict[str, Any]) -> None:
        """Invoked exclusively when a 5-minute candle has completed."""
        if not self._is_running:
            return

        bar_close = float(latest_bar.get("Close", 0.0))
        bar_high = float(latest_bar.get("High", bar_close))
        bar_low = float(latest_bar.get("Low", bar_close))
        bar_ts = str(latest_bar.get("timestamp", ""))

        logger.info("Candle completed event: %s Close=$%.2f High=$%.2f Low=$%.2f", bar_ts, bar_close, bar_high, bar_low)

        # 1. First check if open position hit SL or TP on completed bar extreme
        closed_trade = self.broker.evaluate_open_position_sl_tp(
            current_price=bar_close,
            high=bar_high,
            low=bar_low,
            timestamp=bar_ts,
        )
        if closed_trade:
            self._log_event(
                "EXIT",
                f"Candle Extreme Exit: {closed_trade['direction']} hit {closed_trade['exit_reason']} @ ${closed_trade['exit_price']:.2f} "
                f"(Net: ${closed_trade['net_pnl']:+.2f})",
            )
            self.risk_state.record_result(closed_trade["net_pnl"], self.broker.initial_equity)

        # 2. Strategy Evaluation on completed bars
        self._evaluate_strategy(df_5m)

    # -------------------------------------------------------------------------
    # Strategy Evaluation & Order Simulation
    # -------------------------------------------------------------------------

    def _evaluate_strategy(self, df_candles: pd.DataFrame) -> Optional[Signal]:
        """Evaluate Strategy Intelligence V2 and execute paper trade if conditions align."""
        # 1. Check Emergency Kill Switch FIRST (Priority safety override)
        if getattr(self.cfg, "kill_switch_active", False):
            self._last_rejection_reason = "Emergency kill switch engaged: all trading halted."
            logger.critical("[KILL_SWITCH_BLOCKED] Evaluation aborted because kill_switch_active=True.")
            return None

        if df_candles.empty or len(df_candles) < 30:
            self._last_rejection_reason = "Insufficient completed candle history (< 30 bars)."
            return None

        # Check system & indicator readiness
        readiness = self.feed.get_readiness()
        if not readiness.is_ready:
            missing_str = "; ".join(readiness.missing_requirements) if readiness.missing_requirements else "Unknown requirement"
            self._last_rejection_reason = f"Readiness {readiness.state.value}: {missing_str}"
            logger.info("Evaluation skipped due to system readiness: %s (%s)", readiness.state.value, missing_str)
            return None

        now_tz = datetime.now(self.tz)
        cur_t = now_tz.time()
        self._last_evaluation_time = now_tz

        # Check session time window
        if cur_t < self.earliest_entry_t:
            self._last_rejection_reason = f"Session open buffer: entries blocked until {self.earliest_entry_t.strftime('%H:%M')} IST."
            return None
        if cur_t > self.last_entry_t:
            self._last_rejection_reason = f"Session entry cutoff passed: no entries after {self.last_entry_t.strftime('%H:%M')} IST."
            return None

        # Check existing open position
        if self.broker.open_position is not None:
            self._last_rejection_reason = f"Position already active ({self.broker.open_position['direction']} {self.broker.open_position['lots']} lots). Max 1 position enforced."
            return None

        # Check Daily Risk State
        allowed, risk_msg, guard_data = self.risk_state.can_trade(now_tz)
        if not allowed:
            self._last_rejection_reason = f"Risk guard blocked: {risk_msg}"
            return None

        # Strategy Dispatch
        strat = self._strategy_type
        if strat in ("TREND", "TREND_FOLLOWING"):
            sig = evaluate_trend_following(df_candles, self.cfg)
        elif strat in ("VWAP", "VWAP_EMA"):
            sig = evaluate_vwap_ema(df_candles, self.cfg)
        elif strat == "ORB":
            sig = evaluate_orb(df_candles, self.cfg)
        else:  # PORTFOLIO / ARBITRATED
            arb_res = arbitrate_strategies(df_candles, self.cfg)
            self._last_arbitration = arb_res
            sig = arb_res.winning_signal

        self._last_signal = sig

        if sig.direction not in {"LONG", "SHORT"}:
            self._last_rejection_reason = sig.reason or "No trade setup detected on completed candle."
            return sig

        # Confirmed Entry Setup! Size position with risk engine
        equity = self.broker.get_account_summary()["equity"]
        sizing: RiskDecision = size_position(sig.entry, sig.sl, equity, cfg=self.cfg)

        if not sizing.approved:
            self._last_rejection_reason = f"Sizing rejected: {sizing.reason}"
            logger.warning("Trade signal rejected by sizing engine: %s", sizing.reason)
            return sig

        # PRE-FLIGHT CENTRAL SIZING GUARD CHECK (Phase 3.23)
        try:
            validated_lots = validate_paper_order_lots(sizing.lots)
        except PositionSizeViolation as e:
            self._last_rejection_reason = f"Central position size guard rejected order: {e}"
            logger.error("[BOT_SIZING_VIOLATION] Sizing engine returned invalid paper lots: %s", e)
            return sig

        # Open Simulated Trade
        ok, msg, pos = self.broker.open_simulated_trade(
            symbol=self.cfg.symbol,
            direction=sig.direction,
            entry_price=sig.entry,
            stop_loss=sig.sl,
            take_profit=sig.tp,
            lots=validated_lots,
            strategy_name=getattr(sig, "strategy_name", strat),
            signal_reason=sig.reason,
            candle_timestamp=sig.timestamp,
        )

        if ok:
            # Reserve trade in daily risk state
            try:
                self.risk_state.reserve_trade(now_tz)
            except Exception as exc:
                logger.warning("Could not reserve trade in risk state: %s", exc)

            self._log_event(
                "ENTRY",
                f"Simulated {sig.direction} Opened @ ${pos['entry_price']:.2f} ({sizing.lots} lots) "
                f"[SL: ${sig.sl:.2f}, TP: ${sig.tp:.2f}, Strategy: {pos['strategy_name']}]",
            )
            self._last_rejection_reason = f"Active Position: {sig.direction} {sizing.lots} lots opened."
        else:
            self._last_rejection_reason = f"Broker rejected entry: {msg}"

        return sig

    def _log_event(self, category: str, message: str) -> None:
        """Append to in-memory audit log (capped at 100 entries)."""
        entry = {
            "timestamp": datetime.now(self.tz).strftime("%H:%M:%S"),
            "category": category,
            "message": message,
        }
        with self._lock:
            self._bot_log.append(entry)
            if len(self._bot_log) > 100:
                self._bot_log = self._bot_log[-100:]

    # -------------------------------------------------------------------------
    # Telemetry View Data
    # -------------------------------------------------------------------------

    def get_dashboard_telemetry(self) -> Dict[str, Any]:
        """Aggregate telemetry from market data feed, paper broker, and strategy engine."""
        feed_status = self.feed.get_status()
        account_summary = self.broker.get_account_summary()
        now_tz = datetime.now(self.tz)

        # Higher-timeframe indicators for dashboard
        df_5m = self.feed.get_completed_5m_candles()
        df_15m = self.feed.get_completed_15m_candles()

        ema20_5m = 0.0
        ema20_15m = 0.0
        ema50_15m = 0.0
        adx_15m = 0.0
        vwap_val = 0.0
        regime_val = "UNKNOWN"

        if not df_5m.empty and len(df_5m) >= 20:
            from indicators.orb_indicators import ema, vwap
            c_5m = df_5m["Close"]
            ema20_5m = float(ema(c_5m, 20).iloc[-1])
            vwap_series = vwap(df_5m["High"], df_5m["Low"], df_5m["Close"], df_5m["Volume"])
            vwap_val = float(vwap_series.iloc[-1]) if pd.notna(vwap_series.iloc[-1]) else float(c_5m.iloc[-1])

        if not df_15m.empty and len(df_15m) >= 50:
            from indicators.orb_indicators import ema, adx
            c_15m = df_15m["Close"]
            ema20_15m = float(ema(c_15m, 20).iloc[-1])
            ema50_15m = float(ema(c_15m, 50).iloc[-1])
            adx_series = adx(df_15m["High"], df_15m["Low"], df_15m["Close"], 14)
            adx_15m = float(adx_series.iloc[-1]) if pd.notna(adx_series.iloc[-1]) else 0.0

        if self._last_arbitration:
            regime_val = self._last_arbitration.regime_result.regime.value
        elif not df_5m.empty:
            from regime.market_regime import detect_market_regime
            r_res = detect_market_regime(df_5m, self.cfg)
            regime_val = r_res.regime.value

        readiness = self.feed.get_readiness()

        return {
            "is_running": self._is_running,
            "mode": "PAPER",
            "symbol": self.cfg.symbol,
            "active_strategy": self._strategy_type,
            "timezone": self.cfg.timezone,
            "feed": feed_status,
            "account": account_summary,
            "market_regime": regime_val,
            "readiness_state": readiness.state.value,
            "is_ready": readiness.is_ready,
            "readiness_details": readiness.to_dict(),
            "ema20_5m": round(ema20_5m, 2),
            "ema20_15m": round(ema20_15m, 2),
            "ema50_15m": round(ema50_15m, 2),
            "adx_15m": round(adx_15m, 2),
            "vwap": round(vwap_val, 2),
            "last_signal": self._last_signal.to_dict() if self._last_signal else None,
            "last_rejection_reason": self._last_rejection_reason,
            "last_evaluation_time": self._last_evaluation_time.strftime("%H:%M:%S") if self._last_evaluation_time else "None",
            "bot_logs": list(reversed(self._bot_log)),
        }
