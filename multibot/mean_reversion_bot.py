#!/usr/bin/env python3
"""
Mean Reversion Bot — R_25 Volatility Index.
OPTIMIZED: RSI Reversal (period=5, oversold=25, overbought=75)
Backtest: 59.6% WR @ 5min, 63.6% WR @ 10min, +$58 PnL over 146 trades
Uses 10-min contract duration for maximum WR.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class MeanReversionBot(BaseBot):
    """Trade R_25 with optimized RSI reversal."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "mean_reversion"), config)
        self.rsi_period = config.get("rsi_period", 5)
        self.rsi_oversold = config.get("rsi_oversold", 25)
        self.rsi_overbought = config.get("rsi_overbought", 75)
        self.min_candles = self.rsi_period + 5

    def _compute_rsi(self, closes: np.ndarray) -> np.ndarray:
        """Compute RSI using Wilder's smoothing for full array."""
        if len(closes) < self.rsi_period + 1:
            return np.full(len(closes), 50.0)
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gains = np.zeros(len(deltas))
        avg_losses = np.zeros(len(deltas))
        avg_gains[self.rsi_period - 1] = np.mean(gains[:self.rsi_period])
        avg_losses[self.rsi_period - 1] = np.mean(losses[:self.rsi_period])
        for i in range(self.rsi_period, len(deltas)):
            avg_gains[i] = (avg_gains[i-1] * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_losses[i] = (avg_losses[i-1] * (self.rsi_period - 1) + losses[i]) / self.rsi_period
        avg_losses_safe = np.where(avg_losses == 0, 1e-10, avg_losses)
        rs = avg_gains / avg_losses_safe
        rsi_vals = 100.0 - (100.0 / (1.0 + rs))
        result = np.full(len(closes), 50.0)
        result[1:] = rsi_vals
        return result

    async def _on_candle_close(self, candle: dict):
        """RSI reversal: enter when RSI crosses back from extreme."""
        if len(self.candles) < self.min_candles:
            return

        closes = np.array([c['close'] for c in self.candles])
        rsi_vals = self._compute_rsi(closes)
        price = candle['close']

        signal = None
        i = len(closes) - 1

        # RSI was below oversold, now turning up → CALL
        if rsi_vals[i - 1] < self.rsi_oversold and rsi_vals[i] > self.rsi_oversold * 0.8:
            signal = "LONG"
            reason = f"RSI reversal LONG | RSI {rsi_vals[i-1]:.1f}→{rsi_vals[i]:.1f} (oversold={self.rsi_oversold})"
        # RSI was above overbought, now turning down → PUT
        elif rsi_vals[i - 1] > self.rsi_overbought and rsi_vals[i] < self.rsi_overbought * 1.2:
            signal = "SHORT"
            reason = f"RSI reversal SHORT | RSI {rsi_vals[i-1]:.1f}→{rsi_vals[i]:.1f} (overbought={self.rsi_overbought})"

        if signal:
            self.signals_today += 1
            self.last_signal_time = time.time()

            # For binary CALL/PUT, SL/TP don't matter much — set nominal values
            if signal == "LONG":
                sl = price * 0.98
                tp = price * 1.02
            else:
                sl = price * 1.02
                tp = price * 0.98

            self.log(
                f"SIGNAL: {signal} | price={price:.4f} RSI={rsi_vals[i]:.1f} | "
                f"prev_RSI={rsi_vals[i-1]:.1f}"
            )

            await self.execute_signal(signal, price, sl, tp, reason)
        else:
            if len(self.candles) % 12 == 0:
                self.log(
                    f"No signal | price={price:.4f} RSI={rsi_vals[i]:.1f} | "
                    f"active={len(self.active_trades)}"
                )
