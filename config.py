import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "1089")
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

# New Deriv API (PAT-based OAuth)
DERIV_PAT_TOKEN = os.getenv("DERIV_PAT_TOKEN", "")
DERIV_PAT_APP_ID = os.getenv("DERIV_PAT_APP_ID", "")
DERIV_PAT_ACCOUNT_ID = os.getenv("DERIV_PAT_ACCOUNT_ID", "")
DERIV_USE_PAT = bool(DERIV_PAT_TOKEN and DERIV_PAT_APP_ID and DERIV_PAT_ACCOUNT_ID)

# Trading parameters
SYMBOL = os.getenv("SYMBOL", "R_100")
GRANULARITY = int(os.getenv("GRANULARITY", "300"))  # seconds: 60=M1, 300=M5, 900=M15
CANDLE_COUNT = int(os.getenv("CANDLE_COUNT", "250"))  # enough for EMA 200 + buffer
STAKE_AMOUNT = float(os.getenv("STAKE_AMOUNT", "4.0"))
CURRENCY = os.getenv("CURRENCY", "USD")

# Strategy parameters
EMA_PERIOD = 200
RSI_PERIOD = 14
RSI_THRESHOLD_LONG = int(os.getenv("RSI_THRESHOLD_LONG", "51"))
RSI_THRESHOLD_SHORT = int(os.getenv("RSI_THRESHOLD_SHORT", "49"))
SL_MULTIPLIER = 2.0
TP_MULTIPLIER = 2.0

# Strategy selection: "engulfing" or "smc"
STRATEGY = os.getenv("STRATEGY", "smc")

# SMC Breaker Block parameters (OPTIMIZED for 4$ stake on 50$ account)
SMC_SWING_ORDER = int(os.getenv("SMC_SWING_ORDER", "3"))
SMC_MAX_SETUP_AGE = int(os.getenv("SMC_MAX_SETUP_AGE", "150"))
SMC_MIN_RR = float(os.getenv("SMC_MIN_RR", "0.6"))
SMC_SL_BUFFER = float(os.getenv("SMC_SL_BUFFER", "0.0"))
SMC_MIN_SETUP_AGE = int(os.getenv("SMC_MIN_SETUP_AGE", "0"))
SMC_TRAIL_ACTIVATE_RR = float(os.getenv("SMC_TRAIL_ACTIVATE_RR", "0.3"))
SMC_TRAIL_DIST_RR = float(os.getenv("SMC_TRAIL_DIST_RR", "0.03"))
SMC_MAX_POSITIONS = int(os.getenv("SMC_MAX_POSITIONS", "3"))
SMC_DEFAULT_TP_RR = float(os.getenv("SMC_DEFAULT_TP_RR", "2.0"))
SMC_TRAIL_TIGHTEN_RR = float(os.getenv("SMC_TRAIL_TIGHTEN_RR", "2.0"))
SMC_MIN_MSS_STRENGTH = float(os.getenv("SMC_MIN_MSS_STRENGTH", "0.0"))

# Contract Mode: "binary" (CALL/PUT) or "multiplier" (MULTUP/MULTDOWN)
CONTRACT_MODE = os.getenv("CONTRACT_MODE", "multiplier").lower()
MULTIPLIER_LEVERAGE = int(os.getenv("MULTIPLIER_LEVERAGE", "100"))

# Jev (TypeSafe System One) decision layer
TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")
JEV_MIN_COMPOSITE = float(os.getenv("JEV_MIN_COMPOSITE", "0.6"))

# Binary mode SL/TP multipliers (applied on top of strategy-computed SL/TP)
# Values > 1.0 widen SL/TP for binary to avoid premature early-sell at unfavorable returns
BINARY_SL_MULTIPLIER = float(os.getenv("BINARY_SL_MULTIPLIER", "1.0"))
BINARY_TP_MULTIPLIER = float(os.getenv("BINARY_TP_MULTIPLIER", "1.0"))

# Position Sizing (Dynamic Sizing - MODIFIED for safety)
ENABLE_DYNAMIC_SIZING = os.getenv("ENABLE_DYNAMIC_SIZING", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "50"))
MAX_DRAWDOWN_PERCENT = float(os.getenv("MAX_DRAWDOWN_PERCENT", "40"))

# Web UI
PIN_CODE = os.getenv("PIN_CODE", "123456")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
SESSION_SECRET = os.getenv("SESSION_SECRET", "bloc-trade-secret-key-change-me")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
