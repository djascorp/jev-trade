#!/usr/bin/env python3
"""
Momentum Bot — R_75 Volatility Index.
Strategy: EMA crossover + RSI confirmation.
Trade in the direction of momentum.
Expected: 5-10 trades/day, WR 55-65%, +$3.27/win.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class MomentumBot(BaseBot):
    """Trade R_75 with EMA crossover momentum."""
    
    def __init__(self, config: dict):
        super().__init__("momentum_r75", config)
        self.fast_ema = config.get("fast_ema", 9)
        self.slow_ema = config.get("slow_ema", 21)
        self.rsi_period = config.get("rsi_period", 7)
        self.rsi_min = config.get("rsi_min", 45)   # bullish momentum threshold
        self.rsi_max = config.get("rsi_max", 55)   # bearish momentum threshold
        self.min_candles = self.slow_ema + 5
    
    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Compute EMA."""
        multiplier = 2.0 / (period + 1)
        ema = np.zeros(len(data))
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = data[i] * multiplier + ema[i-1] * (1 - multiplier)
        return ema
    
    def _rsi(self, closes: np.ndarray) -> float:
        """Compute RSI."""
        if len(closes) < self.rsi_period + 1:
            return 50.0
        deltas = np.diff(closes[-(self.rsi_period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    async def _on_candle_close(self, candle: dict):
        """Evaluate momentum on each candle close."""
        if len(self.candles) < self.min_candles:
            return
        
        closes = np.array([c['close'] for c in self.candles])
        price = candle['close']
        
        fast = self._ema(closes, self.fast_ema)
        slow = self._ema(closes, self.slow_ema)
        rsi = self._rsi(closes)
        
        fast_now = fast[-1]
        fast_prev = fast[-2]
        slow_now = slow[-1]
        slow_prev = slow[-2]
        
        # Detect crossover
        bullish_cross = fast_prev <= slow_prev and fast_now > slow_now
        bearish_cross = fast_prev >= slow_prev and fast_now < slow_now
        
        # Also check momentum alignment (fast above slow = bullish trend)
        bullish_aligned = fast_now > slow_now and rsi > self.rsi_min
        bearish_aligned = fast_now < slow_now and rsi < (100 - self.rsi_min)
        
        signal = None
        reason = ""
        
        if bullish_cross and rsi > self.rsi_min:
            signal = "LONG"
            reason = f"EMA bullish cross | fast={fast_now:.2f}>slow={slow_now:.2f} | RSI={rsi:.1f}"
        elif bearish_cross and rsi < (100 - self.rsi_min):
            signal = "SHORT"
            reason = f"EMA bearish cross | fast={fast_now:.2f}<slow={slow_now:.2f} | RSI={rsi:.1f}"
        
        if signal:
            self.signals_today += 1
            self.last_signal_time = time.time()
            
            # For CALL/PUT binary: SL/TP are used for in-code monitoring only
            # We close early if price moves against us
            # TP is where we'd consider the trade "confirmed right"
            if signal == "LONG":
                sl = price - abs(price * 0.003)  # 0.3% stop
                tp = price + abs(price * 0.003)  # 0.3% target
            else:
                sl = price + abs(price * 0.003)
                tp = price - abs(price * 0.003)
            
            self.log(
                f"SIGNAL: {signal} | price={price:.2f} "
                f"EMA fast={fast_now:.2f} slow={slow_now:.2f} RSI={rsi:.1f} | {reason}"
            )
            
            await self.open_trade(signal, price, sl, tp, reason)
        else:
            # Periodic status
            if len(self.candles) % 12 == 0:
                trend_dir = "BULL" if fast_now > slow_now else "BEAR"
                self.log(
                    f"No signal | price={price:.2f} EMA trend={trend_dir} "
                    f"fast={fast_now:.2f} slow={slow_now:.2f} RSI={rsi:.1f} "
                    f"active={len(self.active_trades)}"
                )
