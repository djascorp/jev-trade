"""TradingLab Scalping Strategy: EMA 200 + RSI 14 + Engulfing Candle."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.logger import get_logger
from core.indicators import compute_indicators

logger = get_logger("strategy")


class SignalType(Enum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class TradeSignal:
    signal_type: SignalType
    entry_price: float
    candle_size: float
    sl_distance: float
    sl_price: float
    tp_price: float
    ema_200: float
    rsi_14: float


class TradingLabStrategy:
    """TradingLab Scalping Strategy (Trend-Following).
    
    Entry conditions:
    LONG: close > EMA200 AND RSI > threshold AND Bullish Engulfing
    SHORT: close < EMA200 AND RSI < threshold AND Bearish Engulfing
    
    Risk Management:
    SL = 2x candle size, TP = 2x SL distance (R:R = 1:2)
    Set & Forget: no trailing stop.
    """

    def __init__(self, rsi_long_threshold: int = 51, rsi_short_threshold: int = 49,
                 sl_multiplier: float = 2.0, tp_multiplier: float = 2.0,
                 ema_period: int = 200, rsi_period: int = 14,
                 min_candle_size_pct: float = 0.0, engulf_ratio: float = 1.0):
        self.rsi_long_threshold = rsi_long_threshold
        self.rsi_short_threshold = rsi_short_threshold
        self.sl_multiplier = sl_multiplier
        self.tp_multiplier = tp_multiplier
        self.ema_period = ema_period
        self.rsi_period = rsi_period
        self.min_candle_size_pct = min_candle_size_pct
        self.engulf_ratio = engulf_ratio
        self.last_signal: Optional[TradeSignal] = None

    def evaluate(self, candles: list[dict]) -> Optional[TradeSignal]:
        """Evaluate current market conditions and return a signal if entry criteria are met."""
        indicators = compute_indicators(candles, ema_period=self.ema_period, rsi_period=self.rsi_period, engulf_ratio=self.engulf_ratio)
        if not indicators:
            return None

        close = indicators["close"]
        ema_200 = indicators["ema_200"]
        rsi_14 = indicators["rsi_14"]
        bullish_engulf = indicators["bullish_engulfing"]
        bearish_engulf = indicators["bearish_engulfing"]
        candle_size = indicators["candle_size"]

        # Avoid zero-size candles (shouldn't happen but safety check)
        if candle_size <= 0:
            return None

        # Minimum candle size filter: skip small candles (noise)
        if self.min_candle_size_pct > 0:
            min_size = close * (self.min_candle_size_pct / 100.0)
            if candle_size < min_size:
                return None

        # --- LONG SIGNAL ---
        if (close > ema_200
                and rsi_14 > self.rsi_long_threshold
                and bullish_engulf):
            sl_distance = candle_size * self.sl_multiplier
            sl_price = close - sl_distance
            tp_price = close + (sl_distance * self.tp_multiplier)

            signal = TradeSignal(
                signal_type=SignalType.LONG,
                entry_price=close,
                candle_size=candle_size,
                sl_distance=sl_distance,
                sl_price=sl_price,
                tp_price=tp_price,
                ema_200=ema_200,
                rsi_14=rsi_14,
            )
            self.last_signal = signal
            logger.info(
                f"LONG SIGNAL | Entry={close:.5f} SL={sl_price:.5f} TP={tp_price:.5f} "
                f"| EMA200={ema_200:.5f} RSI={rsi_14:.2f} | SL_DIST={sl_distance:.5f}"
            )
            return signal

        # --- SHORT SIGNAL ---
        if (close < ema_200
                and rsi_14 < self.rsi_short_threshold
                and bearish_engulf):
            sl_distance = candle_size * self.sl_multiplier
            sl_price = close + sl_distance
            tp_price = close - (sl_distance * self.tp_multiplier)

            signal = TradeSignal(
                signal_type=SignalType.SHORT,
                entry_price=close,
                candle_size=candle_size,
                sl_distance=sl_distance,
                sl_price=sl_price,
                tp_price=tp_price,
                ema_200=ema_200,
                rsi_14=rsi_14,
            )
            self.last_signal = signal
            logger.info(
                f"SHORT SIGNAL | Entry={close:.5f} SL={sl_price:.5f} TP={tp_price:.5f} "
                f"| EMA200={ema_200:.5f} RSI={rsi_14:.2f} | SL_DIST={sl_distance:.5f}"
            )
            return signal

        # Log why no signal
        logger.debug(
            f"No signal | Close={close:.5f} vs EMA200={ema_200:.5f} | "
            f"RSI={rsi_14:.2f} | BullEng={bullish_engulf} BearEng={bearish_engulf}"
        )
        return None
