"""SMC Breaker Block / Unicorn Strategy.

Entry conditions (SHORT example):
1. Liquidity Sweep: price sweeps above a swing high then reverses
2. Market Structure Shift: price breaks below last swing low
3. Breaker Block: last bullish OB broken during the impulse, now resistance
4. Fair Value Gap: bearish FVG overlapping with the Breaker Block
5. Entry: when price retests the FVG top level

LONG is the exact mirror.

Risk Management:
- SL: just above/below the Breaker Block
- TP: opposite liquidity target (last swing low for shorts, last swing high for longs)
- If no clear TP target, use 2:1 R:R default
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

from core.logger import get_logger
from core.smc_indicators import (
    detect_smc_setups,
    find_entry_trigger,
    SMCSetup,
)

logger = get_logger("smc_strategy")


@dataclass
class SMCTradeSignal:
    signal_type: str  # "LONG" or "SHORT"
    entry_price: float
    sl_price: float
    tp_price: float
    setup: SMCSetup


class SMCStrategy:
    """SMC Breaker Block / Unicorn Strategy.

    This strategy looks for the 4-step setup:
    1. Liquidity Sweep -> 2. MSS -> 3. BB+FVG -> 4. Entry at FVG retest

    Parameters:
        swing_order: Number of bars each side for swing point detection.
        max_setup_age: Max candles to wait for FVG retest after formation.
        sl_buffer_pct: SL buffer as % of breaker block size.
        min_rr: Minimum R:R required to take the trade (default 1.5).
        default_tp_rr: Default TP R:R if no liquidity target found (default 2.0).
    """

    def __init__(
        self,
        swing_order: int = 5,
        max_setup_age: int = 30,
        sl_buffer_pct: float = 0.1,
        min_rr: float = 1.5,
        default_tp_rr: float = 2.0,
        min_setup_age: int = 0,
        sl_mode: str = "bb",
        atr_multiplier: float = 1.0,
        atr_period: int = 14,
        min_body_ratio: float = 0.0,
        min_sl_distance: float = 0.0,
        be_sl_rr: float = 0.0,
        trail_activate_rr: float = 0.0,
        trail_distance_rr: float = 0.0,
        min_fvg_size: float = 0.0,
        close_beyond: bool = False,
        max_positions: int = 1,
        max_hold_candles: int = 0,
        anti_correlate: bool = False,
        min_target_rr: float = 0.0,
        use_tp: bool = True,
        partial_tp_rr: float = 0.0,
        partial_close_pct: float = 0.5,
        trail_tighten_rr: float = 0.0,
        min_mss_strength: float = 0.0,
    ):
        self.swing_order = swing_order
        self.max_setup_age = max_setup_age
        self.sl_buffer_pct = sl_buffer_pct
        self.min_rr = min_rr
        self.default_tp_rr = default_tp_rr
        self.min_setup_age = min_setup_age
        self.sl_mode = sl_mode
        self.atr_multiplier = atr_multiplier
        self.atr_period = atr_period
        self.min_body_ratio = min_body_ratio
        self.min_sl_distance = min_sl_distance
        self.be_sl_rr = be_sl_rr
        self.trail_activate_rr = trail_activate_rr
        self.trail_distance_rr = trail_distance_rr
        self.min_fvg_size = min_fvg_size
        self.close_beyond = close_beyond
        self.max_positions = max_positions
        self.max_hold_candles = max_hold_candles
        self.anti_correlate = anti_correlate
        self.min_target_rr = min_target_rr
        self.use_tp = use_tp
        self.partial_tp_rr = partial_tp_rr
        self.partial_close_pct = partial_close_pct
        self.trail_tighten_rr = trail_tighten_rr
        self.min_mss_strength = min_mss_strength
        self.last_signal: Optional[SMCTradeSignal] = None
        self.active_setups: list[SMCSetup] = []

    def evaluate(self, candles: list[dict], current_index: int = None) -> Optional[SMCTradeSignal]:
        """Evaluate current market and return a signal if a complete setup forms.

        This is called at each candle close. It:
        1. Detects all active setups
        2. Checks if price has touched any FVG entry level

        Args:
            candles: Full candle history up to current point.
            current_index: Index of the current candle (default: last candle).

        Returns SMCTradeSignal if entry is triggered, else None.
        """
        if len(candles) < 50:
            return None

        idx = current_index if current_index is not None else len(candles) - 1

        # Detect setups on the candle window
        setups = detect_smc_setups(
            candles[:idx + 1],
            swing_order=self.swing_order,
            max_setup_age=self.max_setup_age,
            sl_buffer_pct=self.sl_buffer_pct,
            sl_mode=self.sl_mode,
            atr_multiplier=self.atr_multiplier,
            atr_period=self.atr_period,
            min_target_rr=self.min_target_rr,
        )

        if not setups:
            self.active_setups = []
            return None

        # Log new setups detected for debugging
        for setup in setups:
            setup_age = idx - setup.formation_index
            fvg_str = f"FVG=[{setup.fvg.bottom:.2f}-{setup.fvg.top:.2f}]" if setup.fvg else "FVG=None(BB-only)"
            logger.info(
                f"NEW SETUP {setup.direction} @ {setup.entry_price:.5f} | "
                f"age={setup_age} | "
                f"formation_idx={setup.formation_index} | "
                f"sl={setup.sl_price:.5f} tp={setup.tp_price:.5f} | "
                f"{fvg_str} | "
                f"total_setups_detected={len(setups)}"
            )

        # Filter to recent, unfilled setups
        valid_setups = []
        highs = np.array([float(c["high"]) for c in candles])
        lows = np.array([float(c["low"]) for c in candles])
        closes = np.array([float(c["close"]) for c in candles])

        for setup in setups:
            # CRITICAL FIX: Calculate age correctly from formation to current candle
            # The setup formation happens at setup.formation_index
            # We are now at candle idx
            age = idx - setup.formation_index
            
            # CRITICAL: Respect min_setup_age AND max_setup_age
            if age < self.min_setup_age or age > self.max_setup_age:
                logger.debug(
                    f"Setup {setup.direction} @{setup.entry_price:.5f} rejected: "
                    f"age={age} (min={self.min_setup_age}, max={self.max_setup_age})"
                )
                continue

            # === BB-ONLY MODE (synthetic indices scalping) ===
            # Skip FVG filled check for volatile markets - accept more setups
            # Check FVG hasn't been filled between formation and now
            # from core.smc_indicators import check_fvg_filled
            # if setup.fvg is not None and check_fvg_filled(setup.fvg, highs, lows, closes, idx):
            #     logger.debug(
            #         f"Setup {setup.direction} @{setup.entry_price:.5f} rejected: "
            #         f"FVG already filled"
            #     )
            #     continue

            valid_setups.append(setup)

        if not valid_setups:
            self.active_setups = []
            return None

        # Check for entry trigger on the most recent valid setup
        # (prioritize more recent setups)
        opens = np.array([c["open"] for c in candles[:idx + 1]])
        for setup in reversed(valid_setups):
            # Check if price has touched entry level since setup formation
            entry_idx = find_entry_trigger(
                setup, highs, lows, closes, setup.formation_index, idx,
                opens=opens,
                confirm_entry=True,
            )

            if entry_idx is None:
                continue

            # Calculate R:R
            sl_distance = abs(setup.entry_price - setup.sl_price)

            if sl_distance <= 0:
                continue

            if setup.tp_price is not None:
                tp_distance = abs(setup.tp_price - setup.entry_price)
                rr = tp_distance / sl_distance
            else:
                # Use default R:R
                tp_distance = sl_distance * self.default_tp_rr
                if setup.direction == "SHORT":
                    setup.tp_price = setup.entry_price - tp_distance
                else:
                    setup.tp_price = setup.entry_price + tp_distance
                rr = self.default_tp_rr

            if rr < self.min_rr:
                continue

            signal = SMCTradeSignal(
                signal_type=setup.direction,
                entry_price=setup.entry_price,
                sl_price=setup.sl_price,
                tp_price=setup.tp_price,
                setup=setup,
            )
            self.last_signal = signal

            logger.info(
                f"{setup.direction} SIGNAL | Entry={setup.entry_price:.5f} "
                f"SL={setup.sl_price:.5f} TP={setup.tp_price:.5f} | "
                f"R:R={rr:.2f} | Age={age} | formation_idx={setup.formation_index} | "
                f"FVG=[{setup.fvg.bottom:.5f}-{setup.fvg.top:.5f}] | "
                f"BB=[{setup.breaker_block.bottom:.5f}-{setup.breaker_block.top:.5f}]"
            )
            return signal

        self.active_setups = valid_setups
        return None

    def evaluate_simple(self, candles: list[dict]) -> Optional[SMCTradeSignal]:
        """Simplified evaluate for backtest compatibility (same interface as TradingLabStrategy)."""
        return self.evaluate(candles)
