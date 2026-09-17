#!/usr/bin/env python3
"""
Consecutive Reversal Bot — Forex pairs.
OPTIMIZED: 4 Consecutive Reversal
Backtest EUR/AUD: 58.4% WR, +$31.42, 101 trades
Fade the streak — after 4 same-direction candles, reverse.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class ConsecutiveReversalBot(BaseBot):
    """Trade forex with N-consecutive candle reversal."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "consecutive_bot"), config)
        self.n_consecutive = config.get("n_consecutive", 4)
        self.min_candles = self.n_consecutive + 2

    async def _on_candle_close(self, candle: dict):
        """Enter when N consecutive same-direction candles complete."""
        if len(self.candles) < self.min_candles:
            return

        price = candle['close']
        n = self.n_consecutive

        # Check the last N candles before the current one
        recent = self.candles[-(n + 1):-1]
        if len(recent) < n:
            return

        all_bull = all(c['close'] > c['open'] for c in recent)
        all_bear = all(c['close'] < c['open'] for c in recent)

        signal = None

        if all_bear:
            signal = "LONG"
            reason = f"{n} consecutive bearish → CALL (reversal)"
        elif all_bull:
            signal = "SHORT"
            reason = f"{n} consecutive bullish → PUT (reversal)"

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
                f"SIGNAL: {signal} | price={price:.5f} | "
                f"{n} consec {'bull' if all_bull else 'bear'}"
            )

            await self.execute_signal(signal, price, sl, tp, reason)
        else:
            if len(self.candles) % 12 == 0:
                self.log(
                    f"No signal | price={price:.5f} | "
                    f"active={len(self.active_trades)}"
                )
