#!/usr/bin/env python3
"""
BB Bounce Bot — R_100 Volatility Index.
OPTIMIZED: Bollinger Bands bounce (period=20, std=2.0)
Backtest: 62.9% WR @ 5min, +$67 PnL over 105 trades
Highest WR of all strategies. R_100 has wide swings → BB extremes predict reversals.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class BounceBot(BaseBot):
    """Trade R_100 with Bollinger Bands bounce."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "bbounce_bot"), config)
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2.0)
        self.min_candles = self.bb_period + 2

    def _compute_bollinger(self, closes: np.ndarray):
        """Return (upper, middle, lower) for the last value."""
        if len(closes) < self.bb_period:
            return None, None, None
        slice_ = closes[-self.bb_period:]
        sma = np.mean(slice_)
        std = np.std(slice_)
        upper = sma + self.bb_std * std
        lower = sma - self.bb_std * std
        return upper, sma, lower

    async def _on_candle_close(self, candle: dict):
        """BB bounce: enter when price closes at or beyond BB extreme."""
        if len(self.candles) < self.min_candles:
            return

        closes = np.array([c['close'] for c in self.candles])
        price = candle['close']

        upper, middle, lower = self._compute_bollinger(closes)
        if upper is None:
            return

        signal = None

        if price <= lower:
            signal = "LONG"
            reason = (f"BB bounce LONG | price={price:.2f} <= "
                      f"BB_lower={lower:.2f} (period={self.bb_period} std={self.bb_std})")
        elif price >= upper:
            signal = "SHORT"
            reason = (f"BB bounce SHORT | price={price:.2f} >= "
                      f"BB_upper={upper:.2f} (period={self.bb_period} std={self.bb_std})")

        if signal:
            self.signals_today += 1
            self.last_signal_time = time.time()

            if signal == "LONG":
                sl = price * 0.98
                tp = price * 1.02
            else:
                sl = price * 1.02
                tp = price * 0.98

            self.log(
                f"SIGNAL: {signal} | price={price:.2f} "
                f"BB=[{lower:.2f}, {middle:.2f}, {upper:.2f}] "
                f"bw={upper - lower:.2f}"
            )

            await self.execute_signal(signal, price, sl, tp, reason)
        else:
            if len(self.candles) % 12 == 0:
                self.log(
                    f"No signal | price={price:.2f} "
                    f"BB=[{lower:.2f}, {middle:.2f}, {upper:.2f}] | "
                    f"active={len(self.active_trades)}"
                )
