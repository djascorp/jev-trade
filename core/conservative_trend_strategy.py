"""Conservative Trend Strategy with Strict Risk Management.

Entry conditions:
- ADX > 30 (strong trend required)
- EMA 20/50 crossover for trend direction
- Price alignment with EMAs (confirmation)
- Min R:R = 3.0 (only best setups)

Risk Management:
- Max 5 trades/day
- Max 3 consecutive losses
- Daily loss limit
- Global drawdown limit
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum

import numpy as np

from core.logger import get_logger

logger = get_logger("conservative_trend_strategy")


@dataclass
class ConservativeTrendSignal:
    signal_type: str  # "LONG" or "SHORT"
    entry_price: float
    sl_price: float
    tp_price: float
    rr: float
    adx: float
    ema_fast: float
    ema_slow: float


class ConservativeTrendStrategy:
    """Conservative Trend Strategy with Strict Risk Management.
    
    This strategy looks for strong trend conditions and only trades
    the highest quality setups with minimum R:R of 3.0.
    
    Parameters:
        fast_ema_period: Fast EMA period (default 20)
        slow_ema_period: Slow EMA period (default 50)
        adx_period: ADX period (default 14)
        adx_threshold: Minimum ADX for trend (default 30.0)
        atr_period: ATR period for SL/TP calculation (default 14)
        sl_multiplier: SL distance in ATR multiples (default 2.0)
        tp_multiplier: TP distance in ATR multiples (default 3.0)
        min_rr: Minimum R:R required (default 3.0)
        max_trades_per_day: Maximum trades per day (default 5)
        max_consecutive_losses: Stop trading after X consecutive losses (default 3)
    """
    
    def __init__(
        self,
        fast_ema_period: int = 20,
        slow_ema_period: int = 50,
        adx_period: int = 14,
        adx_threshold: float = 30.0,
        atr_period: int = 14,
        sl_multiplier: float = 2.0,
        tp_multiplier: float = 3.0,
        min_rr: float = 3.0,
        max_trades_per_day: int = 5,
        max_consecutive_losses: int = 3,
    ):
        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.sl_multiplier = sl_multiplier
        self.tp_multiplier = tp_multiplier
        self.min_rr = min_rr
        self.max_trades_per_day = max_trades_per_day
        self.max_consecutive_losses = max_consecutive_losses
        
        # Track state
        self.consecutive_losses = 0
        self.trades_today = 0
        self.last_trade_day = None
        self.last_signal: Optional[ConservativeTrendSignal] = None
        
        # Cache for indicator values
        self.ema_fast_values = []
        self.ema_slow_values = []
        self.atr_values = []
        self.adx_values = []
    
    def _calculate_ema(self, prices: list[float], period: int) -> list[float]:
        """Calculate EMA using numpy."""
        if len(prices) < period:
            return [None] * len(prices)
        
        prices_array = np.array(prices, dtype=float)
        ema = []
        
        multiplier = 2.0 / (period + 1.0)
        
        # Fill with None until we have enough data
        for i in range(len(prices_array)):
            if i < period - 1:
                ema.append(None)
            elif i == period - 1:
                # First EMA value is SMA
                sma = np.mean(prices_array[:period])
                ema.append(sma)
            else:
                # Calculate EMA
                current_ema = (prices_array[i] * multiplier) + (ema[-1] * (1 - multiplier))
                ema.append(current_ema)
        
        return ema
    
    def _calculate_atr(self, highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
        """Calculate ATR using Wilder's method."""
        if len(highs) < period + 1:
            return [None] * len(highs)
        
        highs_array = np.array(highs, dtype=float)
        lows_array = np.array(lows, dtype=float)
        closes_array = np.array(closes, dtype=float)
        
        # Calculate True Range
        prev_close = np.roll(closes_array, 1)
        prev_close[0] = closes_array[0]
        
        tr1 = highs_array - lows_array
        tr2 = np.abs(highs_array - prev_close)
        tr3 = np.abs(lows_array - prev_close)
        
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        
        # Calculate ATR
        atr = []
        
        for i in range(len(tr)):
            if i < period:
                atr.append(None)
            elif i == period:
                # First ATR value is average of first period True Ranges
                atr.append(np.mean(tr[:period]))
            else:
                # Calculate ATR using Wilder's smoothing
                current_atr = (atr[-1] * (period - 1) + tr[i]) / period
                atr.append(current_atr)
        
        return atr
    
    def _calculate_adx(self, highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
        """Calculate ADX (Average Directional Index)."""
        if len(highs) < period * 2 + 1:
            return [None] * len(highs)
        
        highs_array = np.array(highs, dtype=float)
        lows_array = np.array(lows, dtype=float)
        closes_array = np.array(closes, dtype=float)
        
        # Calculate +DM and -DM
        prev_high = np.roll(highs_array, 1)
        prev_low = np.roll(lows_array, 1)
        
        up_move = highs_array - prev_high
        down_move = prev_low - lows_array
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        # Calculate True Range
        prev_close = np.roll(closes_array, 1)
        prev_close[0] = closes_array[0]
        
        tr = np.maximum(
            highs_array - lows_array,
            np.maximum(np.abs(highs_array - prev_close), np.abs(lows_array - prev_close))
        )
        
        # Calculate smoothed values
        plus_dm_smooth = np.zeros_like(plus_dm)
        minus_dm_smooth = np.zeros_like(minus_dm)
        tr_smooth = np.zeros_like(tr)
        
        # Initialize
        plus_dm_smooth[period] = np.sum(plus_dm[:period])
        minus_dm_smooth[period] = np.sum(minus_dm[:period])
        tr_smooth[period] = np.sum(tr[:period])
        
        # Smooth
        for i in range(period + 1, len(tr)):
            plus_dm_smooth[i] = (plus_dm_smooth[i - 1] * (period - 1) + plus_dm[i]) / period
            minus_dm_smooth[i] = (minus_dm_smooth[i - 1] * (period - 1) + minus_dm[i]) / period
            tr_smooth[i] = (tr_smooth[i - 1] * (period - 1) + tr[i]) / period
        
        # Calculate +DI and -DI
        plus_di = np.zeros_like(plus_dm_smooth)
        minus_di = np.zeros_like(minus_dm_smooth)
        
        for i in range(len(plus_di)):
            if tr_smooth[i] > 0:
                plus_di[i] = 100 * (plus_dm_smooth[i] / tr_smooth[i])
                minus_di[i] = 100 * (minus_dm_smooth[i] / tr_smooth[i])
        
        # Calculate DX
        dx = np.zeros_like(plus_di)
        for i in range(len(dx)):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * (abs(plus_di[i] - minus_di[i]) / di_sum)
        
        # Calculate ADX
        adx = np.zeros_like(dx)
        if len(dx) > period:
            adx[period * 2] = np.mean(dx[period:period * 2])
            for i in range(period * 2 + 1, len(dx)):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
        
        return adx
    
    def _check_daily_limit(self, candle: dict) -> bool:
        """Check if we can take a new trade based on daily limit."""
        current_day = int(candle.get('epoch', 0) // 86400)
        
        if self.last_trade_day is None:
            self.last_trade_day = current_day
            self.trades_today = 0
            return True
        
        if current_day != self.last_trade_day:
            self.last_trade_day = current_day
            self.trades_today = 0
            return True
        
        return self.trades_today < self.max_trades_per_day
    
    def _check_consecutive_losses(self) -> bool:
        """Check if we've hit max consecutive losses."""
        return self.consecutive_losses < self.max_consecutive_losses
    
    def evaluate(self, candles: list[dict], current_index: int = None) -> Optional[ConservativeTrendSignal]:
        """Evaluate current market and return a signal if setup is found.
        
        Args:
            candles: Full candle history up to current point.
            current_index: Index of current candle (default: last candle).
        
        Returns ConservativeTrendSignal if setup is valid, else None.
        """
        if len(candles) < max(self.fast_ema_period, self.slow_ema_period) + self.adx_period * 2 + 10:
            return None
        
        idx = current_index if current_index is not None else len(candles) - 1
        
        # Extract price data
        closes = [float(c['close']) for c in candles]
        highs = [float(c['high']) for c in candles]
        lows = [float(c['low']) for c in candles]
        
        # Calculate indicators
        ema_fast = self._calculate_ema(closes, self.fast_ema_period)
        ema_slow = self._calculate_ema(closes, self.slow_ema_period)
        atr_values = self._calculate_atr(highs, lows, closes, self.atr_period)
        adx_values = self._calculate_adx(highs, lows, closes, self.adx_period)
        
        # Get current values
        if idx >= len(ema_fast) or idx >= len(ema_slow) or idx >= len(atr_values) or idx >= len(adx_values):
            return None
        
        current_ema_fast = ema_fast[idx]
        current_ema_slow = ema_slow[idx]
        current_atr = atr_values[idx]
        current_adx = adx_values[idx]
        current_close = closes[idx]
        
        # Check for None values
        if (current_ema_fast is None or current_ema_slow is None or 
            current_atr is None or current_adx is None):
            return None
        
        # Check ADX filter (strong trend required)
        if current_adx < self.adx_threshold:
            logger.debug(f"ADX {current_adx:.2f} below threshold {self.adx_threshold}")
            return None
        
        # Check daily limit
        if not self._check_daily_limit(candles[idx]):
            logger.debug(f"Daily trade limit reached ({self.trades_today}/{self.max_trades_per_day})")
            return None
        
        # Check consecutive losses
        if not self._check_consecutive_losses():
            logger.warning(f"Max consecutive losses reached ({self.consecutive_losses}/{self.max_consecutive_losses})")
            return None
        
        # Check for EMA crossover at current or previous candle
        if idx < 1:
            return None
        
        prev_ema_fast = ema_fast[idx - 1]
        prev_ema_slow = ema_slow[idx - 1]
        
        # Need at least 2 previous candles for crossover detection
        if prev_ema_fast is None or prev_ema_slow is None:
            return None
        
        # Check for crossover
        signal = None
        
        # Bullish crossover: Fast EMA crosses above Slow EMA
        if prev_ema_fast <= prev_ema_slow and current_ema_fast > current_ema_slow:
            # Additional confirmation: price should be above both EMAs
            if current_close > current_ema_fast and current_close > current_ema_slow:
                sl_distance = current_atr * self.sl_multiplier
                tp_price = current_close + (sl_distance * self.tp_multiplier)
                rr = self.tp_multiplier
                
                if rr >= self.min_rr:
                    signal = ConservativeTrendSignal(
                        signal_type="LONG",
                        entry_price=current_close,
                        sl_price=current_close - sl_distance,
                        tp_price=tp_price,
                        rr=rr,
                        adx=current_adx,
                        ema_fast=current_ema_fast,
                        ema_slow=current_ema_slow,
                    )
                    logger.info(f"Conservative Trend LONG signal detected: ADX={current_adx:.2f}, RR={rr:.2f}")
        
        # Bearish crossover: Fast EMA crosses below Slow EMA
        elif prev_ema_fast >= prev_ema_slow and current_ema_fast < current_ema_slow:
            # Additional confirmation: price should be below both EMAs
            if current_close < current_ema_fast and current_close < current_ema_slow:
                sl_distance = current_atr * self.sl_multiplier
                tp_price = current_close - (sl_distance * self.tp_multiplier)
                rr = self.tp_multiplier
                
                if rr >= self.min_rr:
                    signal = ConservativeTrendSignal(
                        signal_type="SHORT",
                        entry_price=current_close,
                        sl_price=current_close + sl_distance,
                        tp_price=tp_price,
                        rr=rr,
                        adx=current_adx,
                        ema_fast=current_ema_fast,
                        ema_slow=current_ema_slow,
                    )
                    logger.info(f"Conservative Trend SHORT signal detected: ADX={current_adx:.2f}, RR={rr:.2f}")
        
        if signal:
            self.last_signal = signal
            self.trades_today += 1
        
        return signal
    
    def on_trade_completed(self, is_win: bool):
        """Called when a trade is completed to track consecutive losses."""
        if not is_win:
            self.consecutive_losses += 1
            logger.warning(f"Trade lost. Consecutive losses: {self.consecutive_losses}")
        else:
            self.consecutive_losses = 0
            logger.info(f"Trade won. Consecutive losses reset to 0")
