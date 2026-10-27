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
│   └── __init__.py
├── agents/                     # Specialized AI Agents
│   ├── ollama_client.py        # Local Ollama HTTP interface for llama3.2:1b
│   ├── market_research.py      # Agent 1: Market Research (Price action, OHLCV facts)
│   ├── technical_analysis.py   # Agent 2: Technical Analysis (SMA, EMA, RSI, MACD, BB)
│   ├── strategy_agent.py       # Agent 3: Strategy (Evaluates deterministic rules)
│   ├── risk_agent.py           # Agent 4: Risk Management (Capital allocation & sizing)
│   ├── portfolio_agent.py      # Agent 5: Portfolio (Equity tracking & performance)
│   └── trading_supervisor.py   # Agent 6: Supervisor (Workflow orchestration & trade gate)
├── market_data/                # Market Data Providers
│   ├── base.py                 # Abstract BaseMarketDataProvider interface
│   ├── yfinance_provider.py    # Yahoo Finance feed with caching & validation
│   └── mock_provider.py        # Offline synthetic generator for reproducible testing
├── services/                   # Deterministic Core Financial Services
│   ├── indicators.py           # Pure Python indicator calculations (Wilder's RSI, MACD, ATR)
│   ├── signal_engine.py        # Strategy signal coordinator
│   ├── risk_manager.py         # Position sizing and hard risk limit enforcement
│   ├── paper_broker.py         # Simulated order execution with slippage & transaction fees
│   ├── portfolio_service.py    # Mark-to-market valuations and trade statistics
│   └── backtester.py           # Strict candle-by-candle backtesting engine
├── strategies/                 # Trading Strategies
│   ├── base.py                 # BaseStrategy contract
│   ├── moving_average.py       # EMA Crossover Strategy
│   ├── rsi_strategy.py         # RSI Mean-Reversion with Confirmation
│   ├── trend_following.py      # Multi-Factor Trend Following (Trend + RSI + Volume)
│   └── custom_strategy.py      # Flexible user-configurable indicator rules
├── database/                   # SQLite Persistence
│   ├── models.py               # Domain dataclasses (Trade, Position, Signal, Snapshot)
│   └── repository.py           # Thread-safe SQLite repository with WAL mode & transactions
├── ui/                         # Streamlit Dashboard Views
│   ├── dashboard.py            # Portfolio equity, cash, open positions, P&L
│   ├── market_watch.py         # Candlestick charts, overlays & indicator subplots
│   ├── assistant.py            # Conversational AI assistant grounded in tool data
│   ├── signals.py              # Strategy signals, badges, and invalidation rules
│   ├── paper_trading.py        # Order desk with pre-trade risk audit & user confirmation
│   ├── backtesting.py          # Backtest runner, equity curves & trade logs
│   └── risk_settings.py        # Configurable risk thresholds & account reset
├── tests/                      # Automated Test Suite (23 automated unit tests)
│   ├── test_indicators.py
│   ├── test_strategies.py
│   ├── test_risk_manager.py
│   ├── test_paper_broker.py
│   ├── test_backtester.py
│   ├── test_ollama_resilience.py
│   └── test_app_import.py
├── single_agent/               # Preserved original single-agent chat app
├── multi_agent/                # Preserved original dual-agent chat app
├── requirements.txt            # Project dependencies
├── .env.example                # Sample environment variables
└── README.md                   # This documentation
```

---

## Core Features

### 1. Multi-Agent AI System
- **Agent 1: Market Research Agent**: Analyzes OHLCV price action, trends, and volume; strictly distinguishes observed market facts from AI interpretation.
- **Agent 2: Technical Analysis Agent**: Calculates technical indicators in pure Python without LLM hallucination:
  - SMA (20, 50)
  - EMA (12, 26)
  - Wilder's RSI (14)
  - MACD (12, 26, 9)
  - Bollinger Bands (20, 2)
  - ATR (14)
  - 20-period Volume SMA & surge ratio
  - Support and resistance levels
- **Agent 3: Strategy Agent**: Generates deterministic `BUY`, `SELL`, `HOLD`, or `NO_SIGNAL` outputs with clear entry conditions and invalidation criteria.
- **Agent 4: Risk Management Agent**: Audits every proposed order against portfolio risk rules:
  - Risk per trade (% of equity)
  - Position sizing based on distance to stop-loss: $\text{Size} = \frac{\text{Equity} \times \text{Risk\%}}{\text{Price} - \text{SL}}$
  - Maximum position allocation limit (% of portfolio)
  - Maximum daily realized loss circuit breaker
  - Maximum open positions ceiling
  - Transaction fee and slippage modeling
- **Agent 5: Portfolio Agent**: Tracks starting capital, liquid cash, unrealized/realized P&L, drawdown from peak, win rate, and profit factor.
- **Agent 6: Trading Supervisor**: Orchestrates the multi-agent pipeline and enforces mandatory user confirmation before any simulated trade execution.

### 2. Local-First & Privacy-Preserving
- **Local LLM**: Powered by Ollama running `llama3.2:1b` at `http://localhost:11434`.
- **Zero Cloud Leakage**: No private trading data, account balances, or prompts leave your machine.
- **Deterministic Math**: LLM models explain and synthesize, but all indicator values, position sizing, and signals are computed in deterministic Python.

### 3. Comprehensive Streamlit UI
- **Dashboard**: High-level portfolio equity, cash, unrealized & realized P&L, open positions table, and recent trades.
- **Market Watch**: Interactive Plotly candlestick charts, overlays (SMA, EMA, Bollinger Bands), volume bars, RSI subplots, and support/resistance zones. Supports US equities (e.g., `AAPL`, `MSFT`, `NVDA`) and Indian NSE symbols (e.g., `RELIANCE.NS`, `TCS.NS`, `INFY.NS`).
- **AI Trading Assistant**: Conversational reasoning directly with `llama3.2:1b`, grounded in tool telemetry. Try asking: *"Analyze AAPL"* or *"Explain RSI"*.
- **Trading Signals**: Visual evaluation of all strategies with color-coded status badges, rationales, and stop-loss/take-profit targets.
- **Paper Trading**: Virtual order desk with real-time pre-trade risk checks (dollar risk, max allowed size, effective slippage, fees) and explicit user execution confirmation.
- **Strategy Backtesting**: Event-driven candle-by-candle historical simulation with look-ahead bias protection, next-bar execution, fees, slippage, and interactive equity vs. benchmark charts.
- **Risk Settings**: Sliders to adjust risk limits and an account reset utility.

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
Confirm `llama3.2:1b` appears in the list.

### Step 2: Set Up Virtual Environment & Dependencies
In PowerShell, navigate to the project directory:
```powershell
cd c:\Users\ajith\Downloads\Local_AI_Agent

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables (Optional)
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Default settings will work out of the box (`http://localhost:11434` with `llama3.2:1b`).

### Step 4: Run Automated Tests
Verify all 23 unit tests pass:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

### Step 5: Launch the Streamlit Dashboard
```powershell
.\.venv\Scripts\streamlit.exe run app.py
```
Open your browser at `http://localhost:8501`.

---

## Strategy Details

1. **Moving Average Crossover**:
   - Golden Cross: Short EMA (12) crosses above Long EMA (26) $\rightarrow$ `BUY` signal with ATR-based trailing stop.
   - Death Cross: Short EMA (12) crosses below Long EMA (26) $\rightarrow$ `SELL` signal.
   - Prevents whipsaws by requiring previous candle state confirmation.

2. **RSI Reversal with Confirmation**:
   - Reversal: Triggers `BUY` only when 14-period RSI dips into oversold ($\le 30$) and crosses back upward with volume confirmation.
   - Prevents catching falling knives in strong downtrends.

3. **Multi-Factor Trend Following**:
   - Requires confluence of Price > 50-day SMA, 12-EMA > 26-EMA, positive MACD histogram, healthy RSI (45-75), and volume expansion.

4. **Custom Flexible Strategy**:
   - Allows traders to toggle and tune moving average filters, RSI thresholds, and Bollinger Band boundary touches directly in the UI.

---

## Backtesting Methodology & Look-Ahead Protection

- **Event-Driven Next-Bar Execution**: If a signal is triggered at candle $t$ close, execution occurs strictly at candle $t+1$ Open.
- **Realistic Friction**: Every backtested trade accounts for user-configurable broker commissions (default 0.1%) and execution slippage (default 0.05%).
- **Conservative Intra-Bar Assumptions**: If both Stop-Loss and Take-Profit price levels are touched within the same candle's High-Low range, the simulator conservatively assumes the Stop-Loss was triggered first.
- **Performance Reporting**:
  - Total Return % vs. Benchmark Buy & Hold %
  - Annualized Return %
  - Maximum Drawdown %
  - Sharpe Ratio (annualized using daily return volatility)
  - Win Rate % & Profit Factor
  - Complete trade-by-trade log

---

## Preserved Legacy Apps

The original general-purpose single-agent and multi-agent chat apps are preserved in their respective subdirectories:
- `single_agent/app.py`: CrewAI single-agent chat.
- `multi_agent/app.py`: CrewAI dual-agent pipeline (Assistant + Editor).

To run them individually:
```powershell
cd single_agent
streamlit run app.py

cd multi_agent
streamlit run app.py
```

---

## Known Limitations & Disclaimers

1. **Public Market Data Delay**: Free market data feeds from Yahoo Finance are delayed by 15-20 minutes. Do not use for high-frequency or tick-level execution.
2. **Local Model Capacity**: `llama3.2:1b` is an ultra-compact 1.3 GB local model. System prompts are optimized for concise, factual explanations. If you have a dedicated GPU, you can switch to `llama3.2:3b` or `qwen2.5:7b` in `.env`.
3. **No Financial Advice**: This software is intended purely for research, backtesting, and educational paper trading. It is not financial or investment advice.


---

## XAUTUSD ORB + EMA20 + Volume Assistant (v1)

This feature is additive: it preserves the existing Streamlit app, supervisor, Ollama client, SQLite repository, risk manager, and backtester. The new strategy is deterministic Python; the LLM may explain the returned checklist but cannot override the signal.

### Safety defaults
- PAPER / ALERT-ONLY only. No exchange order-placement implementation exists in this feature.
- Educational tool, not financial advice. Paper results are not evidence of future performance.
- Public candle requests need no API key. Do not put exchange secrets in logs, screenshots, prompts, or Git.
- Default Delta India public API base: `https://api.india.delta.exchange`; candles: `GET /v2/history/candles`, resolution `5m`, Unix seconds for `start` and `end`. See the [official Delta Exchange API documentation](https://docs.delta.exchange/).
- Contract size, fees, spread/slippage, leverage and session time are configurable; re-verify the live contract specification before relying on any estimate.

### Default strategy rules
1. Session start is `09:00` Asia/Kolkata by default. The ORB is the high/low of the first three completed 5-minute candles.
2. LONG requires a completed candle close above ORB high, close above EMA20, rising EMA20, and breakout volume above the mean of the prior 20 candles.
3. SHORT mirrors those conditions below ORB low with falling EMA20.
4. A later retest must touch the broken ORB boundary, hold outside the range, and print a directionally colored rejection candle with a configurable wick/body ratio.
5. Stop goes beyond the retest candle/invalidation boundary; target defaults to 2R.
6. No trade when ORB is incomplete, breakout only wicks through the range, price closes back inside, volume/EMA filters fail, EMA20 is chopped, the breakout/retest is overextended or late, minimum lot risk exceeds budget, daily limits are reached, or the post-loss cooldown is active.
7. Defaults are conservative starting parameters, not proven profitable settings. Calibrate them with out-of-sample data and realistic contract-specific costs.

### Run the CLI on Windows

Install existing dependencies (the feature uses packages already listed in `requirements.txt`):
```powershell
python -m pip install -r requirements.txt
```

Fetch public 5m candles and evaluate once:
```powershell
python cli.py once --symbol XAUTUSD --candles 500 --equity 10000
```

Evaluate the latest candle in a CSV:
```powershell
python cli.py csv .\data\xautusd_5m.csv --equity 10000
```

Replay a historical CSV and generate a Plotly equity curve:
```powershell
python cli.py backtest .\data\xautusd_5m.csv --equity 10000
```

CSV must include a timestamp/date/time column and Open, High, Low, Close, Volume columns. Naive timestamps are interpreted in the configured timezone; ambiguous/nonexistent DST timestamps are rejected. A sample data file is not bundled.

After recording a paper alert and later determining its simulated result:
```powershell
python cli.py record-result --pnl -25 --equity 10000
```
Use the actual paper-trade P&L and starting equity, not example numbers. A live fetch reserves the daily trade slot when a risk-approved alert is emitted. CSV evaluation does not reserve the live daily slot.

### Important modeling limitations
- The ORB default session start of 09:00 IST is configurable and is not claimed to be an exchange-defined XAUTUSD session open. Choose and validate the intended session for the instrument.
- Historical API candle availability, volume meaning, funding, mark-price behavior, contract tick/lot constraints, and current fees must be checked against Delta's current product documentation.
- The initial backtester is a research baseline; verify same-bar stop/target ordering, end-of-day open positions, cost assumptions, and all risk-state edge cases before using its metrics to make decisions.
