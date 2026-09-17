#!/usr/bin/env python3
"""
Big Move Reversal Bot — EUR/GBP.
OOS VALIDATED: Train=55.1% (107T), Test=57.0% (79T) on M1 data.

When a candle's move exceeds 1.5σ of the rolling mean, trade the reversal.
EUR/GBP has strong mean reversion (autocorrelation r=-0.105).
Conceptually different from BB Bounce: looks at SPEED of move, not position.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class BigMoveReversalBot(BaseBot):
    """Trade EUR/GBP with Big Move Reversal."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "bmr_bot"), config)
        self.lookback = config.get("lookback", 20)
        self.std_threshold = config.get("std_threshold", 1.5)
        self.min_candles = self.lookback + 5

    async def _on_candle_close(self, candle: dict):
        """Signal when last candle's move exceeds N std of recent returns."""
        if len(self.candles) < self.min_candles:
            return

        closes = np.array([c['close'] for c in self.candles])
        returns = np.diff(closes) / closes[:-1] * 100

        if len(returns) < self.lookback:
            return

        # Rolling stats (last `lookback` returns including current candle)
        recent_returns = returns[-self.lookback:]
        mean_ret = np.mean(recent_returns)
        std_ret = np.std(recent_returns)

        if std_ret == 0:
            return

        # Current candle return
        current_return = returns[-1]
        z_score = abs(current_return - mean_ret) / std_ret

        price = candle['close']
        signal = None

        if z_score >= self.std_threshold:
            if current_return > 0:
                signal = "SHORT"
                reason = (f"BMR SHORT | return={current_return:+.4f}% "
                          f"z={z_score:.2f}σ (>{self.std_threshold}σ) → reversal")
            else:
                signal = "LONG"
                reason = (f"BMR LONG | return={current_return:+.4f}% "
                          f"z={z_score:.2f}σ (>{self.std_threshold}σ) → reversal")

        if signal:
            self.signals_today += 1
            self.last_signal_time = time.time()

            if signal == "LONG":
                sl = price * 0.998
                tp = price * 1.002
            else:
                sl = price * 1.002
                tp = price * 0.998

            self.log(
                f"SIGNAL: {signal} | price={price:.5f} "
                f"return={current_return:+.4f}% z={z_score:.2f}σ "
                f"(mean={mean_ret:+.4f}% std={std_ret:.4f}%)"
            )

            await self.execute_signal(signal, price, sl, tp, reason)
        else:
            if len(self.candles) % 12 == 0:
                self.log(
                    f"No signal | price={price:.5f} "
                    f"return={current_return:+.4f}% z={z_score:.2f}σ | "
                    f"active={len(self.active_trades)}"
                )
