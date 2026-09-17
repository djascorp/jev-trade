#!/usr/bin/env python3
"""
MACD Crossover Bot — trend following on indices.
Validated: R_25 M15 MACD 62.1% WR (p=0.0435)
           USDCAD M5 MACD 64.9% WR (p=0.0166)
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class MACDBot(BaseBot):
    """Trade with MACD signal line crossover."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "macd_bot"), config)
        self.fast = config.get("fast", 12)
        self.slow = config.get("slow", 26)
        self.signal = config.get("signal", 9)
        self.min_candles = self.slow + self.signal + 5

    def _compute_ema(self, data, period):
        out = np.full(len(data), np.nan)
        k = 2 / (period + 1)
        out[period-1] = np.mean(data[:period])
        for i in range(period, len(data)):
            out[i] = data[i] * k + out[i-1] * (1 - k)
        return out

    def _compute_macd(self, closes):
        ema_fast = self._compute_ema(closes, self.fast)
        ema_slow = self._compute_ema(closes, self.slow)
        macd_line = ema_fast - ema_slow
        
        signal_line = np.full(len(closes), np.nan)
        valid_start = self.slow - 1
        for i in range(valid_start + self.signal, len(closes)):
            if not np.isnan(macd_line[i]):
                sig_vals = macd_line[i-self.signal+1:i+1]
                if not np.any(np.isnan(sig_vals)):
                    signal_line[i] = np.mean(sig_vals)
        return macd_line, signal_line

    async def _on_candle_close(self, candle: dict):
        if len(self.candles) < self.min_candles:
            return

        closes = np.array([c['close'] for c in self.candles])
        macd_line, signal_line = self._compute_macd(closes)

        price = candle['close']
        i = len(closes) - 1

        if np.isnan(macd_line[i]) or np.isnan(signal_line[i]):
            return
        if np.isnan(macd_line[i-1]) or np.isnan(signal_line[i-1]):
            return

        signal = None
        reason = ""

        if macd_line[i-1] <= signal_line[i-1] and macd_line[i] > signal_line[i]:
            signal = "LONG"
            reason = f"MACD bullish cross | MACD={macd_line[i]:.5f} > Signal={signal_line[i]:.5f}"
        elif macd_line[i-1] >= signal_line[i-1] and macd_line[i] < signal_line[i]:
            signal = "SHORT"
            reason = f"MACD bearish cross | MACD={macd_line[i]:.5f} < Signal={signal_line[i]:.5f}"

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
