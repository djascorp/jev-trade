#!/usr/bin/env python3
"""
Momentum Bot — R_75 Volatility Index.
OPTIMIZED: Range Breakout (lookback=5)
Backtest: 58.3% WR @ 5min, +$92 PnL over 309 trades
Highest trade volume of all strategies — up to 15+ trades/day.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class MomentumBot(BaseBot):
    """Trade R_75 with Range Breakout."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "momentum_bot"), config)
        self.lookback = config.get("lookback", 5)
        self.min_candles = self.lookback + 2

    async def _on_candle_close(self, candle: dict):
        """Range Breakout: enter when price breaks recent high/low."""
        if len(self.candles) < self.min_candles:
            return

        price = candle['close']

        # Get recent N candles (excluding current)
        recent = self.candles[-(self.lookback + 1):-1]
        if len(recent) < self.lookback:
            return

        recent_high = max(c['high'] for c in recent)
        recent_low = min(c['low'] for c in recent)

        signal = None

        if price > recent_high:
            signal = "LONG"
            reason = (f"Range breakout LONG | price={price:.2f} > "
                      f"recent_high={recent_high:.2f} (lookback={self.lookback})")
        elif price < recent_low:
            signal = "SHORT"
            reason = (f"Range breakout SHORT | price={price:.2f} < "
                      f"recent_low={recent_low:.2f} (lookback={self.lookback})")

        if signal:
            self.signals_today += 1
            self.last_signal_time = time.time()

            # Nominal SL/TP for binary contracts
            if signal == "LONG":
                sl = price * 0.98
                tp = price * 1.02
            else:
                sl = price * 1.02
                tp = price * 0.98

            self.log(
                f"SIGNAL: {signal} | price={price:.2f} "
                f"range=[{recent_low:.2f}, {recent_high:.2f}] | "
                f"range_width={recent_high - recent_low:.2f}"
            )

            await self.execute_signal(signal, price, sl, tp, reason)
        else:
            if len(self.candles) % 12 == 0:
                in_range = recent_low <= price <= recent_high
                self.log(
                    f"No signal | price={price:.2f} "
                    f"range=[{recent_low:.2f}, {recent_high:.2f}] "
                    f"in_range={'Y' if in_range else 'N'} | "
                    f"active={len(self.active_trades)}"
                )
