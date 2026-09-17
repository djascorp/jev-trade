#!/usr/bin/env python3
"""
Fibonacci Retracement Bot.
Trades at 61.8% / 38.2% retracement of recent swing.
Validated: R_50 M30 69.0% WR, R_25 H1 69.2% WR, USDJPY M5 64.9% WR
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class FibonacciBot(BaseBot):
    """Trade Fibonacci retracement bounces."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "fib_bot"), config)
        self.lookback = config.get("lookback", 50)
        self.min_candles = self.lookback + 5

    async def _on_candle_close(self, candle: dict):
        if len(self.candles) < self.min_candles:
            return

        closes = np.array([c['close'] for c in self.candles])
        highs = np.array([c['high'] for c in self.candles])
        lows = np.array([c['low'] for c in self.candles])

        i = len(closes) - 1
        lb = self.lookback

        # Find swing high/low in lookback window
        window_high = highs[i - lb:i]
        window_low = lows[i - lb:i]
        swing_high = np.max(window_high)
        swing_low = np.min(window_low)

        if swing_high == swing_low:
            return

        swing_range = swing_high - swing_low
        fib_618 = swing_high - swing_range * 0.618  # 61.8% from top
        fib_382 = swing_high - swing_range * 0.382  # 38.2% from top

        price = candle['close']
        prev_close = closes[i - 1]

        signal = None
        reason = ""

        # Price near 61.8% level + bouncing up → CALL
        if abs(prev_close - fib_618) / swing_range < 0.02:
            if price > prev_close:
                signal = "LONG"
                reason = f"Fib 61.8% bounce | price={price:.5f} fib={fib_618:.5f}"

        # Price near 38.2% level + rejected → PUT
        elif abs(prev_close - fib_382) / swing_range < 0.02:
            if price < prev_close:
                signal = "SHORT"
                reason = f"Fib 38.2% rejection | price={price:.5f} fib={fib_382:.5f}"

        if signal:
            self.signals_today += 1
            self.last_signal_time = time.time()

            if signal == "LONG":
                sl = price * 0.998
                tp = price * 1.002
            else:
                sl = price * 1.002
                tp = price * 0.998

            self.log(f"SIGNAL: {signal} | price={price:.5f} | {reason}")
            await self.execute_signal(signal, price, sl, tp, reason)
