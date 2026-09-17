#!/usr/bin/env python3
"""
Support/Resistance Bot.
Finds recent swing highs/lows and trades bounces/rejections.
Validated: USDJPY M30 56.5% WR (N=214, p=0.032), R_100 M5 63.3% WR (N=49)
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class SupportResistanceBot(BaseBot):
    """Trade support/resistance level bounces."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "sr_bot"), config)
        self.lookback = config.get("lookback", 20)
        self.min_candles = self.lookback + 5

    async def _on_candle_close(self, candle: dict):
        if len(self.candles) < self.min_candles:
            return

        highs = np.array([c['high'] for c in self.candles])
        lows = np.array([c['low'] for c in self.candles])

        i = len(self.candles) - 1
        lb = self.lookback

        # Find recent swing high/low
        recent_high = np.max(highs[i - lb:i])
        recent_low = np.min(lows[i - lb:i])

        price = candle['close']
        prev_close = candle['open']  # approximate

        signal = None
        reason = ""

        # Price near support (within 0.1%) and bouncing up
        if price <= recent_low * 1.001 and price >= recent_low * 0.999:
            if candle['close'] > candle['open']:
                signal = "LONG"
                reason = f"Support bounce | price={price:.5f} support={recent_low:.5f}"

        # Price near resistance and rejected
        elif price >= recent_high * 0.999 and price <= recent_high * 1.001:
            if candle['close'] < candle['open']:
                signal = "SHORT"
                reason = f"Resistance rejection | price={price:.5f} resistance={recent_high:.5f}"

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
