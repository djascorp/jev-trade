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
│   ├── trader.py          # Trade execution & position management
│   ├── backtest.py        # Backtest engine for engulfing strategy
│   ├── backtest_data.py   # Historical data fetcher & CSV cache
│   ├── position_sizing.py # Dynamic position sizing
│   └── logger.py          # Terminal logging setup
├── web/
│   └── index.html         # Web UI (multi-bot tabs)
├── backtest_data/         # Cached candle CSV files
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


NB: Fait attention au clean code et à la nouvelle architecture
A chaque création de nouveau fichier, respecter le clean code.