#!/usr/bin/env python3
"""
RSI+Stoch Combo Bot — stpRNG (Step Range Index).
OPTIMIZED: Stochastic + RSI combo reversal (k_period=5, rsi_period=7)
Backtest: 58.5% WR @ 5min, +$43 PnL over 135 trades
stpRNG is range-bound — this catches the reversal points.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from multibot.base_bot import BaseBot


class GridTraderBot(BaseBot):
    """Trade stpRNG with Stoch+RSI combo reversal."""

    def __init__(self, config: dict):
        super().__init__(config.get("name", "grid_bot"), config)
        self.k_period = config.get("k_period", 5)
        self.d_period = config.get("d_period", 3)
        self.rsi_period = config.get("rsi_period", 14)
        self.stoch_oversold = config.get("stoch_oversold", 20)
        self.stoch_overbought = config.get("stoch_overbought", 80)
        self.rsi_oversold = config.get("rsi_oversold", 40)
        self.rsi_overbought = config.get("rsi_overbought", 60)
        self.anti_trend_threshold = config.get("anti_trend_threshold", 0.01)  # 1% default, 0=disabled
        self.min_candles = max(self.k_period, self.rsi_period) + 5

    def _compute_rsi(self, closes: np.ndarray) -> np.ndarray:
        """RSI with Wilder's smoothing."""
        period = self.rsi_period
        if len(closes) < period + 1:
            return np.full(len(closes), 50.0)
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_g = np.zeros(len(deltas))
        avg_l = np.zeros(len(deltas))
        avg_g[period - 1] = np.mean(gains[:period])
        avg_l[period - 1] = np.mean(losses[:period])
        for i in range(period, len(deltas)):
            avg_g[i] = (avg_g[i-1] * (period - 1) + gains[i]) / period
            avg_l[i] = (avg_l[i-1] * (period - 1) + losses[i]) / period
        avg_l_safe = np.where(avg_l == 0, 1e-10, avg_l)
        rs = avg_g / avg_l_safe
        rsi_vals = 100.0 - (100.0 / (1.0 + rs))
        result = np.full(len(closes), 50.0)
        result[1:] = rsi_vals
        return result

    def _compute_stochastic(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
        """Stochastic %K."""
        n = len(closes)
        k = np.full(n, 50.0)
        for i in range(self.k_period - 1, n):
            hh = np.max(highs[i - self.k_period + 1:i + 1])
            ll = np.min(lows[i - self.k_period + 1:i + 1])
            if hh > ll:
                k[i] = ((closes[i] - ll) / (hh - ll)) * 100
        return k

    async def _on_candle_close(self, candle: dict):
        """Stoch+RSI combo: both must agree at extremes + trend filter."""
        if len(self.candles) < self.min_candles:
            return

        closes = np.array([c['close'] for c in self.candles])
        highs = np.array([c['high'] for c in self.candles])
        lows = np.array([c['low'] for c in self.candles])

        rsi_vals = self._compute_rsi(closes)
        k_vals = self._compute_stochastic(highs, lows, closes)

        price = candle['close']
        i = len(closes) - 1

        signal = None

        # Both stoch and RSI turning from oversold → CALL
        if (k_vals[i - 1] < self.stoch_oversold and k_vals[i] > k_vals[i - 1] and
                rsi_vals[i] < self.rsi_oversold):
            signal = "LONG"
            reason = (f"Stoch+RSI LONG | K={k_vals[i]:.1f} RSI={rsi_vals[i]:.1f} "
                      f"(stoch_os={self.stoch_oversold} rsi_os={self.rsi_oversold})")
        # Both stoch and RSI turning from overbought → PUT
        elif (k_vals[i - 1] > self.stoch_overbought and k_vals[i] < k_vals[i - 1] and
              rsi_vals[i] > self.rsi_overbought):
            signal = "SHORT"
            reason = (f"Stoch+RSI SHORT | K={k_vals[i]:.1f} RSI={rsi_vals[i]:.1f} "
                      f"(stoch_ob={self.stoch_overbought} rsi_ob={self.rsi_overbought})")

        if signal:
            # Anti-trend filter: skip if price has moved >threshold in signal's opposite
            # direction over last 10 candles (sustained trend = mean reversion trap)
            if self.anti_trend_threshold > 0 and len(closes) >= 10:
                recent_move = (closes[i] - closes[i - 10]) / closes[i - 10]
                if signal == "LONG" and recent_move < -self.anti_trend_threshold:
                    self.log(
                        f"SKIP {signal} | strong downtrend ({recent_move*100:+.2f}% in 10 candles) "
                        f"— mean reversion trap"
                    )
                    self.signals_today += 1
                    self.skipped_today += 1
                    return
                elif signal == "SHORT" and recent_move > self.anti_trend_threshold:
                    self.log(
                        f"SKIP {signal} | strong uptrend ({recent_move*100:+.2f}% in 10 candles) "
                        f"— mean reversion trap"
                    )
                    self.signals_today += 1
                    self.skipped_today += 1
                    return

            self.signals_today += 1
            self.last_signal_time = time.time()

            if signal == "LONG":
                sl = price * 0.98
                tp = price * 1.02
            else:
                sl = price * 1.02
                tp = price * 0.98

            self.log(
                f"SIGNAL: {signal} | price={price:.2f} K={k_vals[i]:.1f} "
                f"RSI={rsi_vals[i]:.1f}"
            )

            await self.execute_signal(signal, price, sl, tp, reason)
        else:
            if len(self.candles) % 12 == 0:
                self.log(
                    f"No signal | price={price:.2f} K={k_vals[i]:.1f} "
                    f"RSI={rsi_vals[i]:.1f} | active={len(self.active_trades)}"
                )
