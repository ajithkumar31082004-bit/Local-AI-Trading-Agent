# Local AI Trading Agent

A local-first quantitative trading research, strategy backtesting, and paper-trading platform powered by **local Ollama LLM inference (`llama3.2:1b`)**, deterministic Python financial indicators, and SQLite persistence.

> **CRITICAL NOTICE: PAPER TRADING ONLY**
> This application defaults strictly to simulated **Paper Trading** using virtual money. Real-money brokerage order execution is not enabled. All trades require explicit user confirmation and pass through deterministic risk filters before execution.

---

## Architecture Overview

```
Local_AI_Agent/
├── app.py                      # Streamlit Multi-Page Trading Dashboard
├── config/
│   ├── settings.py             # Pydantic configuration & .env loader
│   ├── orb_settings.py         # ORB & Delta parameters dataclass
│   ├── orb.yaml                # YAML backtest & session configuration
│   └── __init__.py
├── agents/                     # Specialized AI Agents
│   ├── ollama_client.py        # Local Ollama HTTP interface for llama3.2:1b
│   ├── orb_tools.py            # 6+ Deterministic ORB tools for LLM agent
│   ├── market_research.py      # Agent 1: Market Research (Price action, OHLCV facts)
│   ├── technical_analysis.py   # Agent 2: Technical Analysis (SMA, EMA, RSI, MACD, BB)
│   ├── strategy_agent.py       # Agent 3: Strategy (Evaluates deterministic rules)
│   ├── risk_agent.py           # Agent 4: Risk Management (Capital allocation & sizing)
│   ├── portfolio_agent.py      # Agent 5: Portfolio (Equity tracking & performance)
│   └── trading_supervisor.py   # Agent 6: Supervisor (Workflow orchestration & trade gate)
├── market_data/                # Market Data Providers
│   ├── base.py                 # Abstract BaseMarketDataProvider interface
│   ├── delta_candles.py        # Public Delta Exchange India OHLCV client
│   ├── news_provider.py        # Gold macro headlines & event blackout filter (Header auth)
│   ├── yfinance_provider.py    # Yahoo Finance feed with caching & validation
│   └── mock_provider.py        # Offline synthetic generator for reproducible testing
├── services/                   # Deterministic Core Financial Services
│   ├── delta_risk.py           # Tiny-account INR risk guard, 1 lot = 0.001 XAUT, 1 trade/day
│   ├── orb_backtest.py         # Chronological backtest replay with walk-forward analysis
│   ├── indicators.py           # Pure Python indicator calculations (Wilder's RSI, MACD, ATR)
│   ├── signal_engine.py        # Strategy signal coordinator
│   ├── risk_manager.py         # Position sizing and hard risk limit enforcement
│   ├── paper_broker.py         # Simulated order execution with slippage & transaction fees
│   └── portfolio_service.py    # Mark-to-market valuations and trade statistics
├── strategy/                   # ORB Strategy
│   └── orb_ema_volume.py       # Deterministic 15m ORB + EMA20 + Volume + Retest rules
├── journal/
│   └── orb_journal.py          # Append-only SQLite journal + CSV export + Telegram alerts
├── scripts/
│   ├── fetch_candles.py        # CLI to download N days of 5m candles from Delta India
│   └── run_backtest.py         # CLI backtest runner with walk-forward & equity curve export
├── ui/                         # Streamlit Dashboard Views
│   ├── orb_setup.py            # ORB Setup monitor, 8-point checklist & backtesting tab
│   ├── dashboard.py            # Portfolio equity, cash, open positions, P&L
│   ├── market_watch.py         # Candlestick charts, overlays & indicator subplots
│   ├── assistant.py            # Conversational AI assistant grounded in tool data
│   ├── signals.py              # Strategy signals, badges, and invalidation rules
│   ├── paper_trading.py        # Order desk with pre-trade risk audit & user confirmation
│   ├── backtesting.py          # Backtest runner, equity curves & trade logs
│   └── risk_settings.py        # Configurable risk thresholds & account reset
├── tests/                      # Automated Test Suite (96 automated unit & integration tests)
├── requirements.txt            # Project dependencies
├── .env.example                # Sample environment variables
└── README.md                   # This documentation
```

---

## Windows Installation & Quickstart

### Prerequisites
1. **Windows 10/11**
2. **Python 3.10+** (Python 3.13 supported)
3. **Ollama**: Installed from [https://ollama.com](https://ollama.com)

### Step 1: Pull the Local LLM Model
Open PowerShell and pull `llama3.2:1b`:
```powershell
ollama pull llama3.2:1b
ollama list
```

### Step 2: Set Up Virtual Environment & Dependencies
```powershell
cd c:\Users\ajith\Downloads\Local_AI_Agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Step 3: Run Automated Tests
Verify all 96 unit & integration tests pass (includes hard mock safety guard):
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

### Step 4: Launch the Streamlit Dashboard
```powershell
.\.venv\Scripts\streamlit.exe run app.py
```
Open your browser at `http://localhost:8501`. Select **"ORB Setup"** in the sidebar to view the XAUUSD strategy monitor.

---

## Mode Isolation & Delta Exchange Live Trading Architecture

The platform strictly isolates paper trading, alert monitoring, and live order execution across three explicit operational modes:

| Mode | Description | Real API Calls | Order Placement |
|---|---|---|---|
| `PAPER` *(default)* | Simulated virtual equity & virtual trade fills in SQLite. | Read-only public market data | **None** |
| `ALERT_ONLY` | Evaluates real live market data and signals without order placement. | Read-only public market data & balances | **None** |
| `LIVE` | Real account equity, real margin, and protective bracket order execution. | Authenticated REST endpoints | **Live bracket orders (SL/TP)** |

### Safety Controls & Dual Confirmation
To activate live trading, you must explicitly set **both** environment variables in your private `.env` file:
```env
TRADING_MODE=LIVE
LIVE_TRADING_ACKNOWLEDGED=YES
DELTA_EXCHANGE_ENABLED=true
DELTA_EXCHANGE_BASE_URL=https://api.india.delta.exchange
DELTA_EXCHANGE_API_KEY=your_live_trading_api_key
DELTA_EXCHANGE_API_SECRET=your_live_trading_api_secret
```
If `LIVE_TRADING_ACKNOWLEDGED` is not `YES`, or if credentials are placeholders, the system safely halts and refuses to execute orders.

### 10-Point Pre-Flight Safety Audit
Before any live order is dispatched, the `DeltaLiveExecutor` verifies:
1. Operational mode is strictly `LIVE`.
2. Secondary acknowledgment `LIVE_TRADING_ACKNOWLEDGED=YES`.
3. Emergency kill switch is inactive.
4. Non-placeholder API credentials configured.
5. Tradable product verified (XAUTUSD, Product ID 131253).
6. Authenticated account state & positive equity retrieved.
7. Zero conflicting open positions or pending orders.
8. Signal freshness & idempotency (prevents duplicate orders for the same 5m candle).
9. Daily risk guard check (1 trade/day limit & daily loss cooldown).
10. Sized lots $\ge 1$ and required margin $\le$ free available collateral.

---

## CLI Management & Telemetry Commands

### 1. Inspect Verified Real Account Equity (Read-Only)
Inspect authenticated wallet balance, equity, available free margin, used margin, and open positions:
```powershell
.\.venv\Scripts\python cli.py account --asset USD
```

### 2. Evaluate Current Market Signal Once
Fetch completed 5m candles and audit strategy rules and risk:
```powershell
.\.venv\Scripts\python cli.py once --candles 500
```

### 3. Reconcile Live Orders with Delta Exchange
Reconcile local transactional SQLite order records with exchange states (`GET /v2/orders/client_order_id/{id}`):
```powershell
# Reconcile all pending / unconfirmed orders
.\.venv\Scripts\python cli.py reconcile

# Reconcile a specific custom client order ID
.\.venv\Scripts\python cli.py reconcile --order-id ORB_1730218800_abc123
```

### 4. Emergency Kill Switch Order Cancellation
Immediately cancel all active open orders on Delta Exchange via `DELETE /v2/orders/all` and update local state:
```powershell
.\.venv\Scripts\python cli.py emergency-cancel --confirm
```
You can also set `EMERGENCY_KILL_SWITCH=true` in `.env` to permanently block any new entries.


---

## CLI Tools & Workflows

### 1. Fetch 5m Historical Candles from Delta Exchange India
Downloads completed 5-minute candles directly from the public REST API (`GET /v2/history/candles`) with bounded retries, gap detection, zero-volume warning, and saves to CSV:
```powershell
.\.venv\Scripts\python scripts/fetch_candles.py --symbol XAUTUSD --days 7 --out data/xautusd_5m.csv
```

### 2. Run ORB Strategy Backtests
Run chronological historical backtests with fees, spread, slippage, and conservative SL-first collision handling:
```powershell
# Standard backtest replay
.\.venv\Scripts\python scripts/run_backtest.py data/xautusd_5m.csv --config config/orb.yaml --equity 1000

# 70/30 Walk-Forward analysis (in-sample vs out-of-sample) to check overfitting
.\.venv\Scripts\python scripts/run_backtest.py data/xautusd_5m.csv --walk-forward
```

### 3. Telegram Alerts Configuration (Optional)
In your `.env`:
```env
TELEGRAM_BOT_TOKEN="your_bot_token"
TELEGRAM_CHAT_ID="your_chat_id"
```
When configured, completed valid ORB signals automatically trigger instant alerts. Off by default.

### 4. Macro Event News Blackout Window
In `config/orb.yaml`:
```yaml
news_blackout_windows:
  - "2026-03-12T12:30:00+00:00"  # US CPI Release
  - "2026-03-19T18:00:00+00:00"  # FOMC Decision
news_blackout_buffer_minutes: 30
```
Trades are strictly blocked within ±30 minutes of scheduled high-impact events.

---

## Known Limitations & Disclaimers

1. **Tiny Account Leverage Risk**: Trading derivatives with leverage up to 100x carries substantial risk of liquidation. Always adhere to the strict INR risk budget.
2. **Market Data Latency**: Public candle feeds may experience occasional network latency or rate limiting (HTTP 429).
3. **No Financial Advice**: This software is intended strictly for quantitative research, backtesting, and educational paper trading. It is not financial or investment advice.
