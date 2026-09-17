# Bloc-Trade - Trading Bot

## Environment
- Conda env: `tradepy` (Python 3.11)
- Python path: `D:\INSTALLED\anaconda3\envs\tradepy\python.exe`
- Pip path: `D:\INSTALLED\anaconda3\envs\tradepy\python.exe -m pip`
- Activation: `conda activate tradepy`

## Run Bot
```
# Single bot (legacy)
D:\INSTALLED\anaconda3\envs\tradepy\python.exe main.py

# Multi-bot Web UI
D:\INSTALLED\anaconda3\envs\tradepy\python.exe web_main.py
```

## Run Backtest
```
# Engulfing strategy (default)
D:\INSTALLED\anaconda3\envs\tradepy\python.exe backtest.py
D:\INSTALLED\anaconda3\envs\tradepy\python.exe backtest.py --count 5000 --trades

# SMC Breaker Block strategy
D:\INSTALLED\anaconda3\envs\tradepy\python.exe backtest.py --strategy smc --count 5000 --trades
D:\INSTALLED\anaconda3\envs\tradepy\python.exe backtest.py --strategy smc --swing-order 5 --max-setup-age 30 --min-rr 1.5
```

## Run Jev Validation (TypeSafe decision layer)
```
# Dry-run: decision points + outcomes only, no TYPESAFE_API_KEY needed
python scripts/jev_backtest.py --symbol R_100 --granularity 60 --count 5000 --dry-run

# Live Jev validation (requires TYPESAFE_API_KEY in .env), capped for cost
python scripts/jev_backtest.py --symbol R_100 --granularity 60 --count 5000 --limit 100

# Custom composite weights (re-tune offline from a saved report, no re-inference)
python scripts/jev_backtest.py --weights '{"trend_quality": 0.5, "momentum": 0.2, "volatility_fit": 0.2, "setup_quality": 0.1, "session_fit": 0.0}'
```

## Run Tests
```
python -m pytest tests/ -q
```

## Architecture
```
bloc-trade/
├── AGENTS.md              # This file
├── .env                   # Secrets (Deriv API token, app_id)
├── .env.template          # Template for .env
├── main.py                # Entry point (single bot, legacy)
├── web_main.py            # Web UI entry point (multi-bot)
├── backtest.py            # Backtest CLI (supports --strategy engulfing|smc)
├── config.py              # Global settings from .env (fallback values)
├── bot.py                 # TradingBot class (accepts profile dict)
├── bot_manager.py         # BotManager: orchestrates multiple BotInstance
├── web_server.py          # FastAPI backend (multi-bot API + WebSocket + auth)
├── profiles/              # Bot profile configurations (JSON)
│   ├── m1-r100-meilleure.json
│   ├── m1-gold-meilleure.json
│   ├── m5-r100-aggressive.json
│   ├── m5-gold.json
│   └── m60-swing.json
├── core/
│   ├── __init__.py
│   ├── deriv_api.py       # Deriv WebSocket client
│   ├── indicators.py      # EMA, RSI, Engulfing detection
│   ├── strategy.py        # TradingLab Scalping strategy
│   ├── smc_indicators.py  # SMC: Swing Points, MSS, FVG, Breaker Blocks, Liquidity Sweeps
│   ├── smc_strategy.py    # SMC Breaker Block / Unicorn strategy
│   ├── smc_backtest.py    # SMC backtest engine (pre-computed setups + pending entry)
│   ├── jev_layer.py       # Jev (TypeSafe) decision layer: state builder + composite scoring
│   ├── jev_backtest.py    # Jev validation engine: replay, lift, calibration
│   ├── trader.py          # Trade execution & position management
│   ├── backtest.py        # Backtest engine for engulfing strategy
│   ├── backtest_data.py   # Historical data fetcher & CSV cache
│   ├── position_sizing.py # Dynamic position sizing
│   └── logger.py          # Terminal logging setup
├── multibot/              # Multi-bot strategies + orchestrator (config.json)
├── scripts/
│   ├── jev_backtest.py    # CLI for the Jev validation engine
│   ├── balance_check.py   # Account balance check (PAT)
│   └── daily_report.py    # Daily trading report
├── tests/                 # Unit tests (pytest)
├── web/
│   └── index.html         # Web UI (multi-bot tabs)
├── backtest_data/         # Cached candle CSV files
└── experiments/results/   # Jev validation reports (JSON, gitignored)
```

## Strategy 1: TradingLab Scalping (default)
- EMA 200 trend filter + RSI 14 momentum filter + Engulfing candle signal
- SL = 2x candle size, TP = 2x SL distance (R:R = 1:2)
- Set & Forget: no trailing stop

## Strategy 2: SMC Breaker Block / Unicorn (--strategy smc)
- **Concept**: Captures explosive reversals using Smart Money Concepts
- **4-Step Setup**:
  1. Liquidity Sweep: price sweeps past a swing point then reverses
  2. Market Structure Shift (MSS): price breaks last swing after sweep
  3. Breaker Block (BB) + Fair Value Gap (FVG): formed during impulse move
  4. Entry: when price retests the FVG level (limit-order simulation)
- **Risk Management**:
  - SL: just above/below the Breaker Block
  - TP: opposite liquidity target (last swing low/high) or default R:R
- **Parameters**: swing_order, max_setup_age, min_rr, default_tp_rr, sl_buffer_pct

## Jev Decision Layer (TypeSafe System One)
- **Role**: judgment layer ON TOP of existing strategies — never replaces risk management or execution (those stay in code)
- **Mode B (composite scoring)**: each signal is scored by Jev on 5 dimensions (trend_quality, momentum, volatility_fit, setup_quality, session_fit) + 1 Noul gate (context_ok); code combines scores with weights into a composite (0..1)
- **Mode C (validation)**: `scripts/jev_backtest.py` replays history walk-forward, judges each signal, joins with outcomes, and reports baseline vs Jev-filtered lift, calibration and dimension diagnostics; raw judgments are saved so weights/thresholds can be re-tuned WITHOUT re-querying the model
- **Do not wire the gate into live trading until lift is demonstrated on an out-of-sample period**
- **Config**: TYPESAFE_API_KEY, JEV_MIN_COMPOSITE in .env; weights via --weights JSON


NB: Fait attention au clean code et à la nouvelle architecture
A chaque création de nouveau fichier, respecter le clean code.