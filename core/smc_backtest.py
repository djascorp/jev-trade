"""SMC Breaker Block backtest engine (optimized).

Pre-computes all SMC components once, then iterates through candles
to track pending setups and trigger entries.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

from core.logger import get_logger
from core.smc_indicators import (
    detect_swing_points,
    detect_liquidity_sweep,
    detect_structure_shifts,
    detect_fvg,
    detect_order_blocks,
    detect_breaker_blocks,
    check_fvg_filled,
    find_entry_trigger,
    compute_atr,
    detect_trend,
    is_trade_allowed,
    SMCSetup,
    MSSDirection,
    BreakerBlock,
    FVG,
    LiquiditySweep,
    MSS,
)
from core.smc_strategy import SMCStrategy

logger = get_logger("smc_backtest")


class ExitReason:
    SL = "SL"
    TP = "TP"
    EOD = "EOD"


@dataclass
class SMCBacktestTrade:
    trade_id: int
    direction: str
    entry_time: int
    entry_price: float
    sl_price: float
    tp_price: float
    exit_time: int
    exit_price: float
    exit_reason: str
    profit_pips: float
    r_reward: float
    setup_age: int = 0


@dataclass
class SMCBacktestResult:
    symbol: str = ""
    granularity: int = 0
    start_time: str = ""
    end_time: str = ""
    total_candles: int = 0
    total_setups: int = 0
    setups_filled: int = 0
    setups_expired: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_profit_pips: float = 0.0
    avg_profit_pips: float = 0.0
    avg_win_pips: float = 0.0
    avg_loss_pips: float = 0.0
    max_win_pips: float = 0.0
    max_loss_pips: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown_pips: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_setup_age: float = 0.0
    avg_rr_achieved: float = 0.0
    trades: list = field(default_factory=list)


def _build_all_setups(
    highs: np.ndarray,
    lows: np.ndarray,
    opens: np.ndarray,
    closes: np.ndarray,
    swing_order: int,
    sl_buffer_pct: float,
    sl_mode: str = "bb",
    atr_multiplier: float = 1.0,
    atr_period: int = 14,
    min_target_rr: float = 0.0,
    min_mss_strength: float = 0.0,
) -> list[SMCSetup]:
    """Pre-compute all SMC setups from the full candle array.

    This runs once and returns all valid setups with their formation indices.

    Args:
        sl_mode: "bb" (BB-based SL) or "atr" (ATR-based SL).
        atr_multiplier: ATR multiplier for SL distance (only for sl_mode="atr").
        atr_period: ATR lookback period (only for sl_mode="atr").
        min_target_rr: If > 0, override TP to ensure minimum R:R when liquidity target is too close.
    """
    n = len(highs)

    # Compute ATR if needed
    atr_values = None
    if sl_mode == "atr" or min_mss_strength > 0:
        atr_values = compute_atr(highs, lows, closes, period=atr_period)

    swing_points = detect_swing_points(highs, lows, order=swing_order)
    sweeps = detect_liquidity_sweep(highs, lows, swing_points, closes=closes, lookback=n)
    mss_list = detect_structure_shifts(highs, lows, swing_points, lookback=n)
    fvgs = detect_fvg(highs, lows, lookback=n)
    order_blocks = detect_order_blocks(opens, closes, highs, lows, lookback=n)
    breaker_blocks = detect_breaker_blocks(mss_list, order_blocks, fvgs, max_candle_distance=15)

    setups = []

    for bb in breaker_blocks:
        mss = bb.mss

        # MSS strength filter: MSS candle body must be >= min_mss_strength * ATR
        if min_mss_strength > 0 and atr_values is not None:
            atr_at_mss = atr_values[mss.index]
            if not np.isnan(atr_at_mss) and atr_at_mss > 0:
                mss_body = abs(closes[mss.index] - opens[mss.index])
                if mss_body < atr_at_mss * min_mss_strength:
                    continue

        # Find matching sweep
        matching_sweep = None
        for sweep in sweeps:
            if sweep.reversal_index < mss.index <= sweep.reversal_index + 15:
                if mss.direction == MSSDirection.BEARISH_MSS and sweep.direction == "UP":
                    matching_sweep = sweep
                    break
                elif mss.direction == MSSDirection.BULLISH_MSS and sweep.direction == "DOWN":
                    matching_sweep = sweep
                    break

        if matching_sweep is None:
            continue

        # === BB-ONLY MODE (synthetic indices scalping) ===
        # FVG is optional - accept setups without FVG for more signals
        # if bb.fvg is None:
        #     continue

        fvg = bb.fvg  # Can be None in BB-only mode
        bb_size = bb.top - bb.bottom

        if bb_size <= 0:
            continue

        # ATR-based SL: use ATR * multiplier at formation time
        atr_sl_distance = None
        if sl_mode == "atr" and atr_values is not None:
            atr_at_formation = atr_values[mss.index]
            if np.isnan(atr_at_formation):
                continue
            atr_sl_distance = atr_at_formation * atr_multiplier

        sl_buffer = bb_size * sl_buffer_pct

        if mss.direction == MSSDirection.BEARISH_MSS:
            # SHORT: Entry at BB TOP (resistance zone)
            # Original: entry at FVG.top, BB-only: entry at BB.top
            entry_price = bb.top
            if sl_mode == "atr" and atr_sl_distance is not None:
                sl_price = entry_price + atr_sl_distance
            else:
                sl_price = bb.top + sl_buffer
            # TP: last swing low before MSS (opposite liquidity)
            target_low = None
            for sp in reversed(swing_points):
                if sp.type == "LOW" and sp.index < mss.index:
                    target_low = sp.price
                    break
            tp_price = target_low

        elif mss.direction == MSSDirection.BULLISH_MSS:
            # LONG: Entry at BB BOTTOM (support zone)
            # Original: entry at FVG.bottom, BB-only: entry at BB.bottom
            entry_price = bb.bottom
            if sl_mode == "atr" and atr_sl_distance is not None:
                sl_price = entry_price - atr_sl_distance
            else:
                sl_price = bb.bottom - sl_buffer
            # TP: last swing high before MSS (opposite liquidity)
            target_high = None
            for sp in reversed(swing_points):
                if sp.type == "HIGH" and sp.index < mss.index:
                    target_high = sp.price
                    break
            tp_price = target_high
        else:
            continue

        # === BB-ONLY MODE: Skip FVG filled check ===
        # Check FVG not filled at formation time
        # if fvg is not None and check_fvg_filled(fvg, highs, lows, closes, mss.index, after_index=mss.index):
        #     continue

        # Override TP if liquidity target gives R:R < min_target_rr
        if min_target_rr > 0 and tp_price is not None:
            sl_distance = abs(entry_price - sl_price)
            if sl_distance > 0:
                current_rr = abs(tp_price - entry_price) / sl_distance
                if current_rr < min_target_rr:
                    if mss.direction == MSSDirection.BEARISH_MSS:
                        tp_price = entry_price - sl_distance * min_target_rr
                    else:
                        tp_price = entry_price + sl_distance * min_target_rr

        setups.append(SMCSetup(
            direction="SHORT" if mss.direction == MSSDirection.BEARISH_MSS else "LONG",
            sweep=matching_sweep,
            mss=mss,
            breaker_block=bb,
            fvg=fvg,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            formation_index=mss.index,
        ))

    setups.sort(key=lambda s: s.formation_index)

    # Deduplicate: keep only one setup per formation_index
    seen = set()
    deduped = []
    for s in setups:
        key = (s.formation_index, s.direction)
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    return deduped


def run_smc_backtest(
    candles: list[dict],
    strategy: SMCStrategy,
    symbol: str = "",
    granularity: int = 60,
    slippage_candles: int = 0,
) -> SMCBacktestResult:
    """Run optimized SMC backtest.

    1. Pre-compute all SMC setups on full data (one pass)
    2. Iterate candles, tracking pending setups and entry triggers
    3. One position at a time
    """
    if len(candles) < 50:
        raise ValueError(f"Need at least 50 candles for SMC backtest, got {len(candles)}")

    result = SMCBacktestResult(
        symbol=symbol,
        granularity=granularity,
        start_time=_fmt_epoch(candles[0]["epoch"]),
        end_time=_fmt_epoch(candles[-1]["epoch"]),
        total_candles=len(candles),
    )

    n = len(candles)
    highs = np.array([float(c["high"]) for c in candles])
    lows = np.array([float(c["low"]) for c in candles])
    closes = np.array([float(c["close"]) for c in candles])
    opens = np.array([float(c["open"]) for c in candles])

    logger.info("Pre-computing SMC components...")
    all_setups = _build_all_setups(
        highs, lows, opens, closes,
        swing_order=strategy.swing_order,
        sl_buffer_pct=strategy.sl_buffer_pct,
        sl_mode=getattr(strategy, 'sl_mode', 'bb'),
        atr_multiplier=getattr(strategy, 'atr_multiplier', 1.0),
        atr_period=getattr(strategy, 'atr_period', 14),
        min_target_rr=getattr(strategy, 'min_target_rr', 0.0),
        min_mss_strength=getattr(strategy, 'min_mss_strength', 0.0),
    )
    logger.info(f"Found {len(all_setups)} raw setups")

    # Debug: log first few setups
    for s in all_setups[:5]:
        fvg_str = f"FVG=[{s.fvg.bottom:.5f}-{s.fvg.top:.5f}]" if s.fvg else "FVG=None"
        bb_str = f"BB=[{s.breaker_block.bottom:.5f}-{s.breaker_block.top:.5f}]" if s.breaker_block else "BB=None"
        logger.info(
            f"  Setup {s.direction} @ idx={s.formation_index} | "
            f"entry={s.entry_price:.5f} sl={s.sl_price:.5f} tp={s.tp_price} | "
            f"{fvg_str} {bb_str}"
        )

    # Build a lookup: formation_index -> setup
    setup_by_index = {s.formation_index: s for s in all_setups}
    formation_indices = sorted(setup_by_index.keys())

    warmup = 50
    max_positions = getattr(strategy, 'max_positions', 1)
    positions: list[dict] = []
    pending_setups: list[SMCSetup] = []
    seen_formations = set()
    trade_id = 0
    equity_curve: list[float] = [0.0]
    consecutive_wins = 0
    consecutive_losses = 0
    total_setups = 0
    setups_filled = 0
    setups_expired = 0

    def _manage_position_trailing(position: dict, high: float, low: float, candle_idx: int):
        """Update trailing stop and breakeven SL for a position.

        FIX (EX-1): Trail update is now delayed by 1 candle to model the async
        latency of Deriv's contract_update API. In live trading, the trailing
        stop update is sent asynchronously and takes effect on the NEXT candle.
        Previously, the backtest applied trail updates instantaneously on the
        same candle's high/low, which inflated PF vs live performance.
        """
        be_r_r = getattr(strategy, 'be_sl_rr', 0.0)
        if be_r_r > 0 and not position.get("be_triggered", False):
            one_r_dist = position["sl_distance"] * be_r_r
            # FIX (EX-2): Use candle CLOSE, not wick, for BE trigger
            # A wick touch that reverses = BE trap (stopped at entry for zero)
            if position["direction"] == "LONG" and position.get("_prev_close", 0) >= position["entry_price"] + one_r_dist:
                position["sl_price"] = position["entry_price"]
                position["be_triggered"] = True
            elif position["direction"] == "SHORT" and position.get("_prev_close", float("inf")) <= position["entry_price"] - one_r_dist:
                position["sl_price"] = position["entry_price"]
                position["be_triggered"] = True

        trail_activate = getattr(strategy, 'trail_activate_rr', 0.0)
        trail_distance = getattr(strategy, 'trail_distance_rr', 0.0)
        if trail_activate > 0 and trail_distance > 0 and not position.get("trail_active", False):
            activate_dist = position["sl_distance"] * trail_activate
            if position["direction"] == "LONG" and high >= position["entry_price"] + activate_dist:
                position["trail_active"] = True
                # FIX (EX-1): Trail SL applies from PREVIOUS candle's extreme (latency model)
                position["trail_sl"] = position.get("_prev_high", high) - position["sl_distance"] * trail_distance
            elif position["direction"] == "SHORT" and low <= position["entry_price"] - activate_dist:
                position["trail_active"] = True
                position["trail_sl"] = position.get("_prev_low", low) + position["sl_distance"] * trail_distance

        if position.get("trail_active", False):
            # Step trailing: tighten trail after price moves trail_tighten_rr
            trail_tighten_rr = getattr(strategy, 'trail_tighten_rr', 0.0)
            current_trail_dist = trail_distance
            if trail_tighten_rr > 0:
                entry = position["entry_price"]
                sl_dist = position["sl_distance"]
                if position["direction"] == "LONG":
                    favor_rr = (high - entry) / sl_dist if sl_dist > 0 else 0
                else:
                    favor_rr = (entry - low) / sl_dist if sl_dist > 0 else 0
                if favor_rr >= trail_tighten_rr:
                    current_trail_dist = trail_distance * 0.5

            trail_rr_dist = position["sl_distance"] * current_trail_dist
            if position["direction"] == "LONG":
                # FIX (EX-1): Use previous candle high for trail update (latency)
                prev_h = position.get("_prev_high", high)
                new_trail_sl = prev_h - trail_rr_dist
                if new_trail_sl > position.get("trail_sl", 0):
                    position["trail_sl"] = new_trail_sl
                if position["trail_sl"] > position["sl_price"]:
                    position["sl_price"] = position["trail_sl"]
            elif position["direction"] == "SHORT":
                # FIX (EX-1): Use previous candle low for trail update (latency)
                prev_l = position.get("_prev_low", low)
                new_trail_sl = prev_l + trail_rr_dist
                if new_trail_sl < position.get("trail_sl", float("inf")):
                    position["trail_sl"] = new_trail_sl
                if position["trail_sl"] < position["sl_price"]:
                    position["sl_price"] = position["trail_sl"]

    def _check_position_exit(position: dict, high: float, low: float):
        """Check if position hit SL or TP. Returns (exit_reason, exit_price) or (None, None)."""
        if position["direction"] == "LONG":
            if low <= position["sl_price"]:
                return ExitReason.SL, position["sl_price"]
            elif high >= position["tp_price"]:
                return ExitReason.TP, position["tp_price"]
        elif position["direction"] == "SHORT":
            if high >= position["sl_price"]:
                return ExitReason.SL, position["sl_price"]
            elif low <= position["tp_price"]:
                return ExitReason.TP, position["tp_price"]
        return None, None

    class MaxHoldExit:
        """Sentinel for max-hold-time exit."""
        pass

    def _record_trade(position: dict, exit_price: float, exit_reason: str, epoch: int):
        """Record a completed trade (includes partial TP profit if applicable)."""
        nonlocal consecutive_wins, consecutive_losses
        remaining_pct = position.get("remaining_pct", 1.0)

        if position["direction"] == "LONG":
            remaining_profit = (exit_price - position["entry_price"]) * remaining_pct
        else:
            remaining_profit = (position["entry_price"] - exit_price) * remaining_pct

        # Add any partial TP profit already taken
        partial_profit = position.get("partial_profit", 0.0)
        profit = partial_profit + remaining_profit

        r_reward = abs(profit / position["sl_distance"]) if position["sl_distance"] > 0 else 0

        result.trades.append(SMCBacktestTrade(
            trade_id=position["trade_id"],
            direction=position["direction"],
            entry_time=position["entry_time"],
            entry_price=position["entry_price"],
            sl_price=position["sl_price"],
            tp_price=position["tp_price"],
            exit_time=epoch,
            exit_price=exit_price,
            exit_reason=exit_reason,
            profit_pips=round(profit, 6),
            r_reward=round(r_reward, 2),
            setup_age=position["setup_age"],
        ))
        equity_curve.append(equity_curve[-1] + profit)

        if profit > 0:
            result.wins += 1
            consecutive_wins += 1
            consecutive_losses = 0
            result.avg_win_pips += profit
            result.max_win_pips = max(result.max_win_pips, profit)
        else:
            result.losses += 1
            consecutive_losses += 1
            consecutive_wins = 0
            result.avg_loss_pips += profit
            result.max_loss_pips = min(result.max_loss_pips, profit)

        result.max_consecutive_wins = max(result.max_consecutive_wins, consecutive_wins)
        result.max_consecutive_losses = max(result.max_consecutive_losses, consecutive_losses)

    def _try_entry(setup: SMCSetup, i: int, entry_price: float = None):
        """Try to enter a trade from a pending setup. Returns position dict or None.

        Args:
            entry_price: Override entry price (e.g. from slippage model).
                         If None, uses setup.entry_price (FVG level).
        """
        if entry_price is None:
            entry_price = setup.entry_price

        sl_distance = abs(setup.entry_price - setup.sl_price)
        if sl_distance <= 0:
            return None

        min_sl_dist = getattr(strategy, 'min_sl_distance', 0.0)
        if min_sl_dist > 0 and sl_distance < min_sl_dist:
            return None

        # Adjust SL to preserve distance from actual entry price
        if entry_price != setup.entry_price:
            if setup.direction == "SHORT":
                sl_price = entry_price + sl_distance
            else:
                sl_price = entry_price - sl_distance
        else:
            sl_price = setup.sl_price

        tp_price = setup.tp_price
        if tp_price is None:
            if setup.direction == "SHORT":
                tp_price = entry_price - sl_distance * strategy.default_tp_rr
            else:
                tp_price = entry_price + sl_distance * strategy.default_tp_rr
        else:
            # Preserve original TP distance from the structure target
            # but adjust if entry price changed (slippage)
            if entry_price != setup.entry_price:
                tp_dist = abs(setup.tp_price - setup.entry_price)
                if setup.direction == "SHORT":
                    tp_price = entry_price - tp_dist
                else:
                    tp_price = entry_price + tp_dist

        # Disable TP: set unreachable target so only trailing stop manages exits
        if not getattr(strategy, 'use_tp', True):
            if setup.direction == "SHORT":
                tp_price = entry_price - sl_distance * 1000
            else:
                tp_price = entry_price + sl_distance * 1000

        tp_distance = abs(tp_price - entry_price)
        rr = tp_distance / sl_distance if sl_distance > 0 else 0

        if rr < strategy.min_rr:
            return None

        age = i - setup.formation_index
        nonlocal trade_id
        trade_id += 1

        # Partial TP parameters
        partial_tp_rr = getattr(strategy, 'partial_tp_rr', 0.0)
        partial_close_pct = getattr(strategy, 'partial_close_pct', 0.5)

        return {
            "direction": setup.direction,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_distance": sl_distance,
            "trade_id": trade_id,
            "entry_time": candles[i]["epoch"],
            "setup_age": age,
            "rr": rr,
            "open_candle": i,
            "remaining_pct": 1.0,
            "partial_tp_rr": partial_tp_rr,
            "partial_close_pct": partial_close_pct,
            "partial_taken": False,
            "partial_profit": 0.0,
            "partial_exit_price": None,
        }

    for i in range(warmup, n):
        # --- Collect new setups that formed at or before this candle ---
        for fi in formation_indices:
            if fi <= i and fi not in seen_formations:
                seen_formations.add(fi)
                pending_setups.append(setup_by_index[fi])
                total_setups += 1

        high = highs[i]
        low = lows[i]
        close = closes[i]

        # --- Phase 1: Manage all open positions (trailing + partial TP + exit check) ---
        closed_this_candle = False
        still_open = []
        max_hold = getattr(strategy, 'max_hold_candles', 0)
        for position in positions:
            # Store previous candle data for trail latency model (EX-1)
            # _prev_high/_prev_low/_prev_close are updated AFTER trailing is evaluated
            _manage_position_trailing(position, high, low, i)

            # Partial TP check (before full exit check)
            if (not position.get("partial_taken", False)
                    and position.get("partial_tp_rr", 0.0) > 0):
                partial_rr = position["partial_tp_rr"]
                partial_pct = position.get("partial_close_pct", 0.5)
                if position["direction"] == "LONG":
                    partial_target = position["entry_price"] + position["sl_distance"] * partial_rr
                    if high >= partial_target:
                        position["partial_taken"] = True
                        position["partial_profit"] = (partial_target - position["entry_price"]) * partial_pct
                        position["partial_exit_price"] = partial_target
                        position["remaining_pct"] -= partial_pct
                elif position["direction"] == "SHORT":
                    partial_target = position["entry_price"] - position["sl_distance"] * partial_rr
                    if low <= partial_target:
                        position["partial_taken"] = True
                        position["partial_profit"] = (position["entry_price"] - partial_target) * partial_pct
                        position["partial_exit_price"] = partial_target
                        position["remaining_pct"] -= partial_pct

            exit_reason, exit_price = _check_position_exit(position, high, low)

            # Max hold time check
            if exit_reason is None and max_hold > 0:
                hold_duration = i - position.get("open_candle", i)
                if hold_duration >= max_hold:
                    exit_reason = "MAXHOLD"
                    exit_price = close

            if exit_reason is not None:
                closed_this_candle = True
                _record_trade(position, exit_price, exit_reason, candles[i]["epoch"])
            else:
                # Store current candle data as "previous" for next iteration's trail latency model (EX-1)
                position["_prev_high"] = float(high)
                position["_prev_low"] = float(low)
                position["_prev_close"] = float(close)
                still_open.append(position)
        positions = still_open

        # --- Phase 2: Try to enter new positions ---
        # Allow entry if there's room. Block same-candle re-entry only for single position.
        allow_entry = len(positions) < max_positions
        if max_positions == 1:
            allow_entry = allow_entry and not closed_this_candle
        if allow_entry:
            new_pending = []
            entered = False
            for setup in pending_setups:
                age = i - setup.formation_index
                if age > strategy.max_setup_age:
                    setups_expired += 1
                    continue

                min_fvg_size = getattr(strategy, 'min_fvg_size', 0.0)
                if min_fvg_size > 0:
                    fvg_size = setup.fvg.top - setup.fvg.bottom
                    if fvg_size < min_fvg_size:
                        continue

                entry_idx = find_entry_trigger(
                    setup, highs, lows, closes, setup.formation_index, i,
                    min_wait=strategy.min_setup_age,
                    opens=opens,
                    min_body_ratio=getattr(strategy, 'min_body_ratio', 0.0),
                    close_beyond=getattr(strategy, 'close_beyond', False),
                    confirm_entry=True,
                    confirm_body_ratio=0.6,
                    confirm_max_distance=3.0,
                )

                if entry_idx is None:
                    new_pending.append(setup)
                    continue

                # --- Trend filter ---
                trend_mode = getattr(strategy, 'trend_filter', 'flexible')
                trend = detect_trend(highs, lows, closes, entry_idx)
                if not is_trade_allowed(setup.direction, trend, trend_mode):
                    new_pending.append(setup)
                    continue

                # Slippage model: enter at open of candle slippage_candles later
                actual_entry_price = None
                if slippage_candles > 0:
                    slip_idx = entry_idx + slippage_candles
                    if slip_idx > i:
                        new_pending.append(setup)  # slippage candle not yet arrived
                        continue
                    if slip_idx >= n:
                        new_pending.append(setup)
                        continue
                    actual_entry_price = float(opens[slip_idx])

                # Anti-correlate: skip if same direction position already open
                if getattr(strategy, 'anti_correlate', False):
                    if any(p["direction"] == setup.direction for p in positions):
                        new_pending.append(setup)
                        continue

                new_pos = _try_entry(setup, i, entry_price=actual_entry_price)
                if new_pos is not None:
                    positions.append(new_pos)
                    entered = True
                    if len(positions) >= max_positions:
                        break
                else:
                    new_pending.append(setup)

            if entered:
                pending_setups = new_pending
            else:
                # Always update pending to remove expired setups
                pending_setups = new_pending

        # Force close all positions at end of data
        if i == n - 1:
            for position in positions:
                if position["direction"] == "LONG":
                    profit = close - position["entry_price"]
                else:
                    profit = position["entry_price"] - close

                r_reward = abs(profit / position["sl_distance"]) if position["sl_distance"] > 0 else 0

                result.trades.append(SMCBacktestTrade(
                    trade_id=position["trade_id"],
                    direction=position["direction"],
                    entry_time=position["entry_time"],
                    entry_price=position["entry_price"],
                    sl_price=position["sl_price"],
                    tp_price=position["tp_price"],
                    exit_time=candles[i]["epoch"],
                    exit_price=close,
                    exit_reason=ExitReason.EOD,
                    profit_pips=round(profit, 6),
                    r_reward=round(r_reward, 2),
                    setup_age=position["setup_age"],
                ))
                result.losses += 1
                result.avg_loss_pips += profit
                result.max_loss_pips = min(result.max_loss_pips, profit)
                equity_curve.append(equity_curve[-1] + profit)

    if pending_setups:
        setups_expired += len(pending_setups)

    # --- Summary statistics ---
    result.total_setups = total_setups
    result.setups_filled = setups_filled
    result.setups_expired = setups_expired
    result.total_trades = len(result.trades)

    if result.total_trades > 0:
        result.win_rate = result.wins / result.total_trades * 100
        result.total_profit_pips = sum(t.profit_pips for t in result.trades)
        result.avg_profit_pips = result.total_profit_pips / result.total_trades
        result.avg_win_pips = result.avg_win_pips / result.wins if result.wins > 0 else 0
        result.avg_loss_pips = result.avg_loss_pips / result.losses if result.losses > 0 else 0

        gross_profit = sum(t.profit_pips for t in result.trades if t.profit_pips > 0)
        gross_loss = abs(sum(t.profit_pips for t in result.trades if t.profit_pips < 0))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        avg_win = result.avg_win_pips if result.avg_win_pips > 0 else 0
        avg_loss = abs(result.avg_loss_pips) if result.avg_loss_pips < 0 else 0
        result.expectancy = (result.win_rate / 100 * avg_win) - ((1 - result.win_rate / 100) * avg_loss)

        peak = equity_curve[0]
        max_dd = 0.0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pips = max_dd

        result.avg_setup_age = sum(t.setup_age for t in result.trades) / result.total_trades
        result.avg_rr_achieved = sum(t.r_reward for t in result.trades) / result.total_trades

    return result


def _fmt_epoch(epoch) -> str:
    try:
        return datetime.utcfromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return str(epoch)
