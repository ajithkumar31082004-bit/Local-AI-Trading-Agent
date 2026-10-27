"""Candle-by-candle event backtesting engine with look-ahead protection and realistic slippage/fees."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

from database.models import SignalType
from strategies.base import BaseStrategy


@dataclass
class BacktestTrade:
    """Represents a simulated executed trade in the backtest."""
    entry_date: str
    exit_date: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    fee_paid: float
    pnl: float
    pnl_pct: float
    exit_reason: str


@dataclass
class BacktestResult:
    """Summary metrics and curves produced by the backtest simulation."""
    symbol: str
    strategy_name: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    benchmark_return_pct: float
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)


class BacktestEngine:
    """Strict next-bar execution event backtester."""

    def __init__(
        self,
        initial_capital: float = 100000.0,
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
        risk_per_trade_pct: float = 2.0,
        max_allocation_pct: float = 20.0,
    ):
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_allocation_pct = max_allocation_pct

    def run(
        self,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        symbol: str,
        warmup_period: int = 50,
    ) -> BacktestResult:
        """Run candle-by-candle simulation.
        
        Look-ahead protection:
        At candle i:
          - Check stop-loss / take-profit on open position using candle i Open, High, Low.
          - Evaluate strategy on slice df[0:i+1].
          - Any new order generated at candle i close is scheduled to execute at candle i+1 OPEN.
        """
        if df.empty or len(df) <= warmup_period + 5:
            return BacktestResult(
                symbol=symbol,
                strategy_name=strategy.name,
                initial_capital=self.initial_capital,
                final_equity=self.initial_capital,
                total_return_pct=0.0,
                annualized_return_pct=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                avg_win=0.0,
                avg_loss=0.0,
                benchmark_return_pct=0.0,
            )

        cash = self.initial_capital
        position_qty = 0.0
        position_entry_price = 0.0
        position_entry_date = ""
        position_sl = 0.0
        position_tp = 0.0
        pending_order = None  # (side, target_qty, sl, tp, reason)

        equity_history = []
        trades: List[BacktestTrade] = []
        dates = df.index

        # Benchmark calculation
        bench_start = float(df["Close"].iloc[warmup_period])
        bench_end = float(df["Close"].iloc[-1])
        benchmark_return_pct = round(((bench_end - bench_start) / bench_start) * 100.0, 2)

        for i in range(warmup_period, len(df)):
            curr_date_str = str(dates[i])
            candle_open = float(df["Open"].iloc[i])
            candle_high = float(df["High"].iloc[i])
            candle_low = float(df["Low"].iloc[i])
            candle_close = float(df["Close"].iloc[i])

            # 1. Execute any pending order from the PREVIOUS bar at CURRENT candle OPEN
            if pending_order:
                order_side, order_qty, order_sl, order_tp, order_reason = pending_order
                pending_order = None

                slip_factor = (self.slippage_pct / 100.0)
                if order_side == "BUY" and position_qty == 0:
                    exec_price = candle_open * (1 + slip_factor)
                    trade_val = order_qty * exec_price
                    fee = trade_val * (self.fee_pct / 100.0)
                    if cash >= trade_val + fee:
                        cash -= (trade_val + fee)
                        position_qty = order_qty
                        position_entry_price = exec_price
                        position_entry_date = curr_date_str
                        position_sl = order_sl
                        position_tp = order_tp

                elif order_side == "SELL" and position_qty > 0:
                    exec_price = candle_open * (1 - slip_factor)
                    trade_val = position_qty * exec_price
                    fee = trade_val * (self.fee_pct / 100.0)
                    cash += (trade_val - fee)

                    pnl = (exec_price - position_entry_price) * position_qty - fee
                    pnl_pct = ((exec_price - position_entry_price) / position_entry_price) * 100.0

                    trades.append(
                        BacktestTrade(
                            entry_date=position_entry_date,
                            exit_date=curr_date_str,
                            symbol=symbol,
                            side="BUY",
                            quantity=position_qty,
                            entry_price=position_entry_price,
                            exit_price=exec_price,
                            fee_paid=fee,
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl_pct, 2),
                            exit_reason=order_reason,
                        )
                    )
                    position_qty = 0.0

            # 2. Check intraday stop loss / take profit on active position
            if position_qty > 0:
                hit_sl = position_sl > 0 and candle_low <= position_sl
                hit_tp = position_tp > 0 and candle_high >= position_tp

                # Conservative tie-breaker: If both hit within the same candle, assume STOP LOSS hit first
                if hit_sl:
                    exec_price = min(candle_open, position_sl) * (1 - (self.slippage_pct / 100.0))
                    trade_val = position_qty * exec_price
                    fee = trade_val * (self.fee_pct / 100.0)
                    cash += (trade_val - fee)
                    pnl = (exec_price - position_entry_price) * position_qty - fee
                    pnl_pct = ((exec_price - position_entry_price) / position_entry_price) * 100.0
                    trades.append(
                        BacktestTrade(
                            entry_date=position_entry_date,
                            exit_date=curr_date_str,
                            symbol=symbol,
                            side="BUY",
                            quantity=position_qty,
                            entry_price=position_entry_price,
                            exit_price=exec_price,
                            fee_paid=fee,
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl_pct, 2),
                            exit_reason="Stop Loss Triggered",
                        )
                    )
                    position_qty = 0.0

                elif hit_tp:
                    exec_price = max(candle_open, position_tp) * (1 - (self.slippage_pct / 100.0))
                    trade_val = position_qty * exec_price
                    fee = trade_val * (self.fee_pct / 100.0)
                    cash += (trade_val - fee)
                    pnl = (exec_price - position_entry_price) * position_qty - fee
                    pnl_pct = ((exec_price - position_entry_price) / position_entry_price) * 100.0
                    trades.append(
                        BacktestTrade(
                            entry_date=position_entry_date,
                            exit_date=curr_date_str,
                            symbol=symbol,
                            side="BUY",
                            quantity=position_qty,
                            entry_price=position_entry_price,
                            exit_price=exec_price,
                            fee_paid=fee,
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl_pct, 2),
                            exit_reason="Take Profit Triggered",
                        )
                    )
                    position_qty = 0.0

            # 3. Mark to market at candle close
            current_mkt_val = position_qty * candle_close
            equity = cash + current_mkt_val
            equity_history.append({"Date": dates[i], "Equity": equity, "Cash": cash, "Close": candle_close})

            # 4. Strategy evaluation strictly on past candles up to candle i close
            window_df = df.iloc[: i + 1]
            signal = strategy.generate_signal(window_df, symbol)

            # Sizing and scheduling next bar execution
            if signal.signal == SignalType.BUY and position_qty == 0:
                # Sizing based on risk and max allocation
                sl_price = signal.suggested_stop_loss or (candle_close * 0.97)
                tp_price = signal.suggested_take_profit or (candle_close * 1.05)
                risk_budget = equity * (self.risk_per_trade_pct / 100.0)
                stop_dist = max(0.01, candle_close - sl_price)
                risk_qty = risk_budget / stop_dist
                alloc_qty = (equity * (self.max_allocation_pct / 100.0)) / candle_close
                cash_qty = (cash * 0.98) / candle_close

                qty = max(0.0, min(risk_qty, alloc_qty, cash_qty))
                if qty >= 1:
                    pending_order = ("BUY", int(qty), sl_price, tp_price, signal.rationale)

            elif signal.signal == SignalType.SELL and position_qty > 0:
                pending_order = ("SELL", position_qty, 0.0, 0.0, signal.rationale)

        # Build equity curve DataFrame
        equity_df = pd.DataFrame(equity_history).set_index("Date")
        if equity_df.empty:
            final_equity = self.initial_capital
        else:
            final_equity = float(equity_df["Equity"].iloc[-1])

        # Compute drawdown series
        equity_df["Peak"] = equity_df["Equity"].cummax()
        equity_df["Drawdown"] = (equity_df["Equity"] - equity_df["Peak"]) / equity_df["Peak"]
        max_drawdown_pct = abs(float(equity_df["Drawdown"].min())) * 100.0 if not equity_df.empty else 0.0

        # Returns and Sharpe
        total_return_pct = round(((final_equity - self.initial_capital) / self.initial_capital) * 100.0, 2)
        n_days = max(1, len(equity_df))
        annualized_return_pct = (
            round((((final_equity / self.initial_capital) ** (252.0 / n_days)) - 1.0) * 100.0, 2)
            if final_equity > 0 and n_days > 10
            else total_return_pct
        )

        daily_returns = equity_df["Equity"].pct_change().dropna()
        if len(daily_returns) > 10 and daily_returns.std() > 0:
            sharpe_ratio = round(float((daily_returns.mean() / daily_returns.std()) * np.sqrt(252)), 2)
        else:
            sharpe_ratio = 0.0

        # Trade metrics
        total_trades = len(trades)
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate_pct = round((winning_trades / total_trades * 100.0), 1) if total_trades > 0 else 0.0

        sum_win = sum(t.pnl for t in wins)
        sum_loss = abs(sum(t.pnl for t in losses))
        profit_factor = round(sum_win / sum_loss, 2) if sum_loss > 0 else (99.0 if sum_win > 0 else 0.0)
        avg_win = round(sum_win / winning_trades, 2) if winning_trades > 0 else 0.0
        avg_loss = round(sum_loss / losing_trades, 2) if losing_trades > 0 else 0.0

        return BacktestResult(
            symbol=symbol,
            strategy_name=strategy.name,
            initial_capital=self.initial_capital,
            final_equity=round(final_equity, 2),
            total_return_pct=total_return_pct,
            annualized_return_pct=annualized_return_pct,
            max_drawdown_pct=round(max_drawdown_pct, 2),
            sharpe_ratio=sharpe_ratio,
            win_rate_pct=win_rate_pct,
            profit_factor=profit_factor,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            avg_win=avg_win,
            avg_loss=avg_loss,
            benchmark_return_pct=benchmark_return_pct,
            trades=trades,
            equity_curve=equity_df,
        )
