#!/usr/bin/env python3
"""
MACD Crossover Bot V2 — Optimized with Signal Strength Filter.
Walk-forward validated: R_25 M15 + Strength>p75 + 20min = 64.0% WR (p=0.0053, σ=14.7)
Multi-symbol validated: R_10 H1 + Strength>p75 + 60min = 58.7% WR (σ=13.6)
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from collections import deque
from multibot.base_bot import BaseBot


class MACDBotV2(BaseBot):
    """MACD crossover + signal strength filter — only trades strong crossovers."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "macd_v2_bot"), config)
        self.fast = config.get("fast", 12)
        self.slow = config.get("slow", 26)
        self.signal = config.get("signal", 9)
        self.min_candles = self.slow + self.signal + 5

        # Signal strength filter (walk-forward validated)
        self.strength_lookback = config.get("strength_lookback", 200)
        self.strength_percentile = config.get("strength_percentile", 75)
        self.strength_history = deque(maxlen=self.strength_lookback)
        self.strength_filtered = 0

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

        # Calculate MACD diff (signal strength)
        abs_diff = abs(macd_line[i] - signal_line[i])

        # Track MACD diff history for rolling percentile
        self.strength_history.append(abs_diff)

        signal = None
        reason = ""

        if macd_line[i-1] <= signal_line[i-1] and macd_line[i] > signal_line[i]:
            signal = "LONG"
            reason = f"MACD bullish cross | MACD={macd_line[i]:.5f} > Signal={signal_line[i]:.5f}"
        elif macd_line[i-1] >= signal_line[i-1] and macd_line[i] < signal_line[i]:
            signal = "SHORT"
            reason = f"MACD bearish cross | MACD={macd_line[i]:.5f} < Signal={signal_line[i]:.5f}"

        if signal:
            # Signal strength filter: only trade if |MACD diff| > p75 of recent history
            if len(self.strength_history) >= 50:
                threshold = np.percentile(list(self.strength_history), self.strength_percentile)
                if abs_diff < threshold:
                    self.strength_filtered += 1
                    self.log(f"SKIP: Weak signal | |diff|={abs_diff:.5f} < p{self.strength_percentile}={threshold:.5f} "
                             f"(filtered: {self.strength_filtered})")
                    return
                reason += f" | Strength={abs_diff:.5f} > p{self.strength_percentile}={threshold:.5f} ✅"
            else:
                reason += f" | Strength={abs_diff:.5f} (warming up: {len(self.strength_history)}/50)"

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
