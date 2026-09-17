"""Technical indicators: EMA, RSI, Engulfing Candle detection."""

import numpy as np
from core.logger import get_logger

logger = get_logger("indicators")


def ema(closes: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average."""
    if len(closes) < period:
        return np.array([])
    multiplier = 2.0 / (period + 1)
    result = np.empty(len(closes))
    result[:period] = np.nan
    result[period - 1] = np.mean(closes[:period])
    for i in range(period, len(closes)):
        result[i] = (closes[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Calculate Relative Strength Index."""
    if len(closes) < period + 1:
        return np.array([])
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.zeros(len(closes))
    avg_loss = np.zeros(len(closes))

    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])

    for i in range(period + 1, len(closes)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

    with np.errstate(divide='ignore', invalid='ignore'):
        rs = np.where(avg_loss != 0, avg_gain / avg_loss, 100.0)
    result = 100.0 - (100.0 / (1.0 + rs))
    result[:period] = np.nan
    return result


def detect_bullish_engulfing(prev_open: float, prev_close: float,
                              curr_open: float, curr_close: float) -> bool:
    """Detect Bullish Engulfing pattern (rmunoz logic).
    
    - Previous candle is bearish (close < open)
    - Current candle is bullish (close > open)
    - Current body engulfs previous body: curr_open <= prev_close AND curr_close >= prev_open
    """
    prev_bearish = prev_close < prev_open
    curr_bullish = curr_close > curr_open
    engulfs = curr_open <= prev_close and curr_close >= prev_open
    return prev_bearish and curr_bullish and engulfs


def detect_bearish_engulfing(prev_open: float, prev_close: float,
                              curr_open: float, curr_close: float) -> bool:
    """Detect Bearish Engulfing pattern (rmunoz logic).
    
    - Previous candle is bullish (close > open)
    - Current candle is bearish (close < open)
    - Current body engulfs previous body: curr_close <= prev_open AND curr_open >= prev_close
    """
    prev_bullish = prev_close > prev_open
    curr_bearish = curr_close < curr_open
    engulfs = curr_close <= prev_open and curr_open >= prev_close
    return prev_bullish and curr_bearish and engulfs


def compute_indicators(candles: list[dict], ema_period: int = 200, rsi_period: int = 14, engulf_ratio: float = 1.0) -> dict:
    """Compute all indicators on a list of OHLC candles.

    Each candle dict must have: open, high, low, close (as epoch integers or floats).

    Args:
        candles: List of OHLC candle dicts.
        ema_period: EMA calculation period (default 200).
        rsi_period: RSI calculation period (default 14).
        engulf_ratio: Minimum engulfing ratio. Current body must be >= engulf_ratio * |prev_body|.

    Returns dict with latest indicator values.
    """
    min_required = ema_period + 1
    if len(candles) < min_required:
        logger.warning(f"Not enough candles for full analysis: {len(candles)} < {min_required}")
        return {}

    closes = np.array([float(c["close"]) for c in candles])
    opens = np.array([float(c["open"]) for c in candles])
    highs = np.array([float(c["high"]) for c in candles])
    lows = np.array([float(c["low"]) for c in candles])

    ema_val = ema(closes, ema_period)
    rsi_val = rsi(closes, rsi_period)

    # Latest values
    latest_close = closes[-1]
    latest_ema = ema_val[-1]
    latest_rsi = rsi_val[-1]

    # Engulfing on the last completed candle (index -1) vs its previous (index -2)
    # With optional engulf_ratio filter: current body must be >= ratio * |prev_body|
    prev_body = abs(opens[-2] - closes[-2])
    curr_body_long = closes[-1] - opens[-1]  # positive for bullish
    curr_body_short = opens[-1] - closes[-1]  # positive for bearish

    bullish_engulf = (detect_bullish_engulfing(
        opens[-2], closes[-2], opens[-1], closes[-1])
        and (curr_body_long >= engulf_ratio * prev_body))

    bearish_engulf = (detect_bearish_engulfing(
        opens[-2], closes[-2], opens[-1], closes[-1])
        and (curr_body_short >= engulf_ratio * prev_body))

    candle_size = highs[-1] - lows[-1]

    result = {
        "close": latest_close,
        "open": opens[-1],
        "high": highs[-1],
        "low": lows[-1],
        "ema_200": latest_ema,
        "rsi_14": latest_rsi,
        "bullish_engulfing": bullish_engulf,
        "bearish_engulfing": bearish_engulf,
        "candle_size": candle_size,
        "candle_count": len(candles),
    }

    logger.debug(
        f"Indicators | Close={latest_close:.5f} EMA{ema_period}={latest_ema:.5f} "
        f"RSI{rsi_period}={latest_rsi:.2f} BullEng={bullish_engulf} BearEng={bearish_engulf} "
        f"Size={candle_size:.5f}"
    )

    return result
