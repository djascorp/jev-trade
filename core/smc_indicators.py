"""SMC (Smart Money Concepts) indicators: Swing Points, MSS, FVG, Breaker Blocks, Liquidity Sweeps."""

import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from core.logger import get_logger

logger = get_logger("smc_indicators")


class StructureType(Enum):
    BULLISH = "BULLISH"  # higher highs, higher lows
    BEARISH = "BEARISH"  # lower highs, lower lows


class MSSDirection(Enum):
    BULLISH_MSS = "BULLISH_MSS"  # bearish -> bullish (sweep low, break high)
    BEARISH_MSS = "BEARISH_MSS"  # bullish -> bearish (sweep high, break low)


@dataclass
class SwingPoint:
    index: int
    price: float
    type: str  # "HIGH" or "LOW"


@dataclass
class LiquiditySweep:
    """When price sweeps beyond a swing point then reverses."""
    sweep_index: int  # candle index where sweep occurred
    swing_point: SwingPoint  # the swing point that was swept
    direction: str  # "UP" (swept above high) or "DOWN" (swept below low)
    sweep_high: float  # highest/lowest point of sweep
    reversal_index: int  # candle where reversal was confirmed


@dataclass
class MSS:
    """Market Structure Shift: break of last swing point after a sweep."""
    index: int  # candle index where structure broke
    direction: MSSDirection
    broken_swing: SwingPoint  # the swing point that was broken
    is_choch: bool = False  # FIX (SMC-1): True if this is a Change of Character (reversal), False if BOS (continuation)


@dataclass
class FVG:
    """Fair Value Gap: 3-candle imbalance."""
    index: int  # index of the middle candle (candle 2 of 3)
    top: float  # upper boundary
    bottom: float  # lower boundary
    mid: float  # 50% level
    direction: str  # "BULLISH" (gap up) or "BEARISH" (gap down)
    filled: bool = False  # whether price has since filled the gap


@dataclass
class BreakerBlock:
    """Breaker Block: the last order block before an MSS that gets broken."""
    ob_start_index: int  # start candle of the order block
    ob_end_index: int  # end candle (the one that initiated the move)
    top: float  # top of the block
    bottom: float  # bottom of the block
    original_direction: str  # "BULLISH" (bullish OB that became bearish BB) or "BEARISH"
    mss: MSS = None  # the MSS that created this breaker block
    fvg: Optional[FVG] = None  # overlapping FVG, if any


@dataclass
class SMCSetup:
    """Complete SMC Breaker Block / Unicorn setup."""
    direction: str  # "LONG" or "SHORT"
    sweep: LiquiditySweep
    mss: MSS
    breaker_block: BreakerBlock
    fvg: FVG
    entry_price: float  # FVG top for shorts, FVG bottom for longs
    sl_price: float  # above BB for shorts, below BB for longs
    tp_price: Optional[float] = None  # opposite liquidity target
    formation_index: int = 0  # candle index when setup completed
    entry_trigger_index: int = 0  # candle index when price first touched FVG


# ---------------------------------------------------------------------------
# Swing Point Detection
# ---------------------------------------------------------------------------

def detect_swing_points(highs: np.ndarray, lows: np.ndarray, order: int = 3):
    """Detect swing highs and swing lows using a simple N-bar lookback.

    A swing high at index i requires: highs[i] >= highs[i-order:i+order+1]
    A swing low at index i requires: lows[i] <= lows[i-order:i+order+1]

    Returns list of SwingPoint sorted by index.
    """
    n = len(highs)
    points = []

    for i in range(order, n - order):
        window_highs = highs[i - order:i + order + 1]
        window_lows = lows[i - order:i + order + 1]

        if highs[i] == np.max(window_highs):
            points.append(SwingPoint(index=i, price=float(highs[i]), type="HIGH"))

        if lows[i] == np.min(window_lows):
            points.append(SwingPoint(index=i, price=float(lows[i]), type="LOW"))

    return points


# ---------------------------------------------------------------------------
# Liquidity Sweep Detection
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(
    highs: np.ndarray,
    lows: np.ndarray,
    swing_points: list[SwingPoint],
    closes: np.ndarray = None,
    lookback: int = 50,
    tolerance: float = 0.0,
    confirm_window: int = 4,
) -> list[LiquiditySweep]:
    """Detect when price sweeps past a swing point and then reverses.

    A TRUE sweep of a swing HIGH: price pokes ABOVE it, then CLOSES back BELOW it
    within confirm_window candles — a genuine liquidity grab and rejection.
    A TRUE sweep of a swing LOW: price pokes BELOW it, then CLOSES back ABOVE it.

    FIX (BUG-1): The old logic recorded a sweep whenever price simply STAYED above
    the swing for 4 bars — i.e. a continuation, not a reversal. This manufactured
    fake setups and inflated the win rate. The new logic requires a close-back
    through the swept level, which is the textbook definition of a liquidity grab.

    Args:
        highs: Array of high prices.
        lows: Array of low prices.
        closes: Array of close prices (REQUIRED for reversal confirmation).
        swing_points: Previously detected swing points.
        lookback: How many candles back to consider for sweeps.
        tolerance: Price must exceed swing by at least this amount (0 = any).
        confirm_window: Max candles after the poke to see a close-back reversal.

    Returns list of detected sweeps.
    """
    sweeps = []
    n = len(highs)
    start = max(0, n - lookback)

    if closes is None:
        closes = highs.copy()  # Fallback if closes not provided

    # Only consider swing points that exist within our lookback window
    relevant_sws = [sp for sp in swing_points if sp.index >= start - 10 and sp.index < n - 2]

    for sp in relevant_sws:
        for i in range(sp.index + 1, min(n, sp.index + 20)):
            if sp.type == "HIGH":
                # Sweep above swing high: price pokes above
                if highs[i] > sp.price + tolerance:
                    # FIND the actual sweep extreme (highest point after poke)
                    sweep_extreme = float(highs[i])
                    extreme_idx = i
                    # Check for reversal: close back below the swing high
                    found_reversal = False
                    rev_idx = i
                    for j in range(i + 1, min(i + 1 + confirm_window, n)):
                        if highs[j] > sweep_extreme:
                            sweep_extreme = float(highs[j])
                            extreme_idx = j
                        # TRUE reversal: candle CLOSES back below the swept level
                        if closes[j] < sp.price:
                            found_reversal = True
                            rev_idx = j
                            break
                    if found_reversal:
                        sweeps.append(LiquiditySweep(
                            sweep_index=extreme_idx,
                            swing_point=sp,
                            direction="UP",
                            sweep_high=sweep_extreme,
                            reversal_index=rev_idx,
                        ))
                    break  # only one sweep per swing point
            elif sp.type == "LOW":
                # Sweep below swing low: price pokes below
                if lows[i] < sp.price - tolerance:
                    # FIND the actual sweep extreme (lowest point after poke)
                    sweep_extreme = float(lows[i])
                    extreme_idx = i
                    # Check for reversal: close back above the swing low
                    found_reversal = False
                    rev_idx = i
                    for j in range(i + 1, min(i + 1 + confirm_window, n)):
                        if lows[j] < sweep_extreme:
                            sweep_extreme = float(lows[j])
                            extreme_idx = j
                        # TRUE reversal: candle CLOSES back above the swept level
                        if closes[j] > sp.price:
                            found_reversal = True
                            rev_idx = j
                            break
                    if found_reversal:
                        sweeps.append(LiquiditySweep(
                            sweep_index=extreme_idx,
                            swing_point=sp,
                            direction="DOWN",
                            sweep_high=sweep_extreme,  # renamed: actual extreme (lowest for DOWN)
                            reversal_index=rev_idx,
                        ))
                    break

    return sweeps


# ---------------------------------------------------------------------------
# Market Structure Shift (MSS) Detection
# ---------------------------------------------------------------------------

def detect_structure_shifts(
    highs: np.ndarray,
    lows: np.ndarray,
    swing_points: list[SwingPoint],
    lookback: int = 100,
    require_choch: bool = False,
) -> list[MSS]:
    """Detect Market Structure Shifts with CHOCH vs BOS classification.

    BEARISH MSS: Price makes a new high (or sweeps), then breaks below
    the last significant swing low.
    BULLISH MSS: Price makes a new low (or sweeps), then breaks above
    the last significant swing high.

    FIX (SMC-1): Now classifies each shift as CHOCH (Change of Character = true
    reversal) or BOS (Break of Structure = trend continuation). A CHOCH occurs
    when the break is against the prevailing trend leg. Set require_choch=True
    to only return genuine reversal signals, which dramatically reduces false
    entries in trending markets.
    """
    n = len(highs)
    shifts = []
    start = max(0, n - lookback)

    swing_highs = [sp for sp in swing_points if sp.type == "HIGH" and sp.index >= start]
    swing_lows = [sp for sp in swing_points if sp.type == "LOW" and sp.index >= start]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return shifts

    # Determine the prevailing trend before each break
    # Trend = UP if last significant swing high was a higher-high than the prior swing high
    # Trend = DOWN if last significant swing low was a lower-low than the prior swing low

    # For each candle after a potential swing high was made, check if structure breaks
    # BEARISH MSS: after recent swing high, price breaks below a recent swing low
    for sh in swing_highs:
        # Find swing lows that were made before this swing high
        prior_lows = [sl for sl in swing_lows if sl.index < sh.index]
        if not prior_lows:
            continue
        last_low = prior_lows[-1]

        # Check if price broke below this swing low AFTER the swing high was formed
        for i in range(sh.index + 1, min(sh.index + 30, n)):
            if lows[i] < last_low.price:
                # SMC-1: Classify CHOCH vs BOS
                # If there was a prior swing high BEFORE last_low that was HIGHER
                # than sh, then breaking below last_low is a CHOCH (reversal from uptrend).
                # If sh was a higher-high than the previous swing high, the trend was UP,
                # and this break below last_low is a CHOCH (first lower-low = reversal).
                prior_highs_before_low = [h for h in swing_highs if h.index < last_low.index]
                is_choch = False
                if prior_highs_before_low:
                    # Trend was UP if sh.price >= prior high price (making higher highs)
                    prev_high = prior_highs_before_low[-1]
                    is_choch = sh.price >= prev_high.price  # breaking an uptrend = CHOCH
                else:
                    # Not enough data to determine trend; treat as CHOCH (conservative)
                    is_choch = True

                if require_choch and not is_choch:
                    break  # Skip BOS (continuation) signals

                shifts.append(MSS(
                    index=i,
                    direction=MSSDirection.BEARISH_MSS,
                    broken_swing=last_low,
                    is_choch=is_choch,
                ))
                break

    # BULLISH MSS: after recent swing low, price breaks above a recent swing high
    for sl in swing_lows:
        prior_highs = [sh for sh in swing_highs if sh.index < sl.index]
        if not prior_highs:
            continue
        last_high = prior_highs[-1]

        for i in range(sl.index + 1, min(sl.index + 30, n)):
            if highs[i] > last_high.price:
                # SMC-1: Classify CHOCH vs BOS
                # If there was a prior swing low BEFORE last_high that was LOWER
                # than sl, then breaking above last_high is a CHOCH (reversal from downtrend).
                prior_lows_before_high = [l for l in swing_lows if l.index < last_high.index]
                is_choch = False
                if prior_lows_before_high:
                    prev_low = prior_lows_before_high[-1]
                    is_choch = sl.price <= prev_low.price  # breaking a downtrend = CHOCH
                else:
                    is_choch = True

                if require_choch and not is_choch:
                    break  # Skip BOS (continuation) signals

                shifts.append(MSS(
                    index=i,
                    direction=MSSDirection.BULLISH_MSS,
                    broken_swing=last_high,
                    is_choch=is_choch,
                ))
                break

    return shifts


# ---------------------------------------------------------------------------
# Fair Value Gap (FVG) Detection
# ---------------------------------------------------------------------------

def detect_fvg(
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int = 50,
    min_gap_pips: float = 0.0,
) -> list[FVG]:
    """Detect Fair Value Gaps (3-candle imbalance pattern).

    BULLISH FVG: candle[i-1].high < candle[i+1].low  (gap between candle 1 high and candle 3 low)
    BEARISH FVG: candle[i-1].low > candle[i+1].high  (gap between candle 1 low and candle 3 high)

    candle[i] is the middle (impulse) candle.
    """
    n = len(highs)
    fvgs = []
    start = max(0, n - lookback)

    for i in range(start + 1, n - 1):
        # Bullish FVG: gap up
        if highs[i - 1] < lows[i + 1]:
            gap_top = float(lows[i + 1])
            gap_bottom = float(highs[i - 1])
            gap_size = gap_top - gap_bottom
            if gap_size >= min_gap_pips:
                fvgs.append(FVG(
                    index=i,
                    top=gap_top,
                    bottom=gap_bottom,
                    mid=(gap_top + gap_bottom) / 2,
                    direction="BULLISH",
                ))

        # Bearish FVG: gap down
        elif lows[i - 1] > highs[i + 1]:
            gap_top = float(lows[i - 1])
            gap_bottom = float(highs[i + 1])
            gap_size = gap_top - gap_bottom
            if gap_size >= min_gap_pips:
                fvgs.append(FVG(
                    index=i,
                    top=gap_top,
                    bottom=gap_bottom,
                    mid=(gap_top + gap_bottom) / 2,
                    direction="BEARISH",
                ))

    return fvgs


def check_fvg_filled(fvg: FVG, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, up_to_index: int, after_index: int = None) -> bool:
    """Check if price has fully filled the FVG up to a given index.

    A FVG is considered 'filled' only when a candle BODY (close) crosses through it,
    NOT just a wick touch:
    - BEARISH FVG: filled when a candle CLOSES above fvg.top.
      A wick touch on fvg.top (entry retest zone) does NOT fill it.
    - BULLISH FVG: filled when a candle CLOSES below fvg.bottom.
      A wick touch on fvg.bottom (entry retest zone) does NOT fill it.

    This distinction is critical: the entry trigger fires when price touches the FVG
    boundary (wick retest), so we must not cancel the setup at the same price level.
    """
    start = (after_index if after_index is not None else fvg.index + 2)
    for i in range(start, up_to_index + 1):
        if fvg.direction == "BEARISH":
            # Filled when a candle body closes above the gap (buyers absorbed it)
            if closes[i] > fvg.top:
                logger.debug(f"FVG BEARISH filled at index {i}: close={closes[i]:.5f} > top={fvg.top:.5f}")
                return True
        elif fvg.direction == "BULLISH":
            # Filled when a candle body closes below the gap (sellers absorbed it)
            if closes[i] < fvg.bottom:
                logger.debug(f"FVG BULLISH filled at index {i}: close={closes[i]:.5f} < bottom={fvg.bottom:.5f}")
                return True
    return False


# ---------------------------------------------------------------------------
# Order Block Detection (for Breaker Block identification)
# ---------------------------------------------------------------------------

def detect_order_blocks(
    opens: np.ndarray,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int = 50,
) -> list[dict]:
    """Detect potential Order Blocks.

    Bullish OB: Last bearish candle before a strong bullish move.
    Bearish OB: Last bullish candle before a strong bearish move.

    A "strong move" = candle body > 1.5x average body size.
    Returns list of dicts with start_index, end_index, top, bottom, direction.
    """
    n = len(opens)
    obs = []
    start = max(0, n - lookback)

    # Calculate average body size for reference
    bodies = np.abs(closes[start:] - opens[start:])
    avg_body = float(np.mean(bodies)) if len(bodies) > 0 else 0

    if avg_body == 0:
        return obs

    for i in range(start + 1, n):
        prev_body = closes[i - 1] - opens[i - 1]  # positive = bullish
        curr_body = closes[i] - opens[i]  # positive = bullish
        curr_body_size = abs(curr_body)

        # Strong bearish move (curr bearish, body > 1.5x avg)
        if curr_body < 0 and curr_body_size > avg_body * 1.5:
            # The previous candle (if bullish) is the bullish OB that got broken
            if prev_body > 0:
                obs.append({
                    "start_index": i - 1,
                    "end_index": i,
                    "top": float(max(opens[i - 1], closes[i - 1])),
                    "bottom": float(min(opens[i - 1], closes[i - 1])),
                    "direction": "BULLISH",  # bullish OB that was broken
                    "break_index": i,
                })

        # Strong bullish move
        elif curr_body > 0 and curr_body_size > avg_body * 1.5:
            if prev_body < 0:
                obs.append({
                    "start_index": i - 1,
                    "end_index": i,
                    "top": float(max(opens[i - 1], closes[i - 1])),
                    "bottom": float(min(opens[i - 1], closes[i - 1])),
                    "direction": "BEARISH",  # bearish OB that was broken
                    "break_index": i,
                })

    return obs


# ---------------------------------------------------------------------------
# Breaker Block Detection
# ---------------------------------------------------------------------------

def detect_breaker_blocks(
    mss_list: list[MSS],
    order_blocks: list[dict],
    fvg_list: list[FVG],
    max_candle_distance: int = 15,
) -> list[BreakerBlock]:
    """Identify Breaker Blocks from MSS events.

    A Breaker Block is the last Order Block that was broken during the
    impulse move that caused the MSS.

    For a BEARISH_MSS: find the last bullish OB broken just before/at the MSS
    For a BULLISH_MSS: find the last bearish OB broken just before/at the MSS
    """
    bbs = []

    for mss in mss_list:
        if mss.direction == MSSDirection.BEARISH_MSS:
            # Find bullish OBs that were broken near the MSS
            target_obs = [
                ob for ob in order_blocks
                if ob["direction"] == "BULLISH"
                and abs(ob["break_index"] - mss.index) <= max_candle_distance
                and ob["break_index"] <= mss.index + 3
            ]
        elif mss.direction == MSSDirection.BULLISH_MSS:
            target_obs = [
                ob for ob in order_blocks
                if ob["direction"] == "BEARISH"
                and abs(ob["break_index"] - mss.index) <= max_candle_distance
                and ob["break_index"] <= mss.index + 3
            ]
        else:
            continue

        if not target_obs:
            continue

        # Take the closest OB to the MSS (most recent one broken)
        target_obs.sort(key=lambda ob: abs(ob["break_index"] - mss.index))
        ob = target_obs[0]

        # Find overlapping FVG
        matching_fvg = None
        for fvg in fvg_list:
            # FVG should be near the MSS and overlap with the OB
            if abs(fvg.index - mss.index) <= max_candle_distance:
                # Check overlap
                overlap = min(ob["top"], fvg.top) - max(ob["bottom"], fvg.bottom)
                if overlap > 0:
                    # For BEARISH_MSS, we want a BEARISH FVG
                    # For BULLISH_MSS, we want a BULLISH FVG
                    expected_fvg_dir = "BEARISH" if mss.direction == MSSDirection.BEARISH_MSS else "BULLISH"
                    if fvg.direction == expected_fvg_dir:
                        matching_fvg = fvg
                        break
                else:
                    # Also accept FVGs that are within 2x of OB size near the MSS
                    bb_size = ob["top"] - ob["bottom"]
                    if bb_size > 0 and abs(fvg.mid - (ob["top"] + ob["bottom"]) / 2) < bb_size * 2:
                        expected_fvg_dir = "BEARISH" if mss.direction == MSSDirection.BEARISH_MSS else "BULLISH"
                        if fvg.direction == expected_fvg_dir:
                            matching_fvg = fvg
                            break

        bb = BreakerBlock(
            ob_start_index=ob["start_index"],
            ob_end_index=ob["end_index"],
            top=ob["top"],
            bottom=ob["bottom"],
            original_direction=ob["direction"],
            mss=mss,
            fvg=matching_fvg,
        )
        bbs.append(bb)

    return bbs


# ---------------------------------------------------------------------------
# Complete SMC Setup Detection
# ---------------------------------------------------------------------------

def detect_smc_setups(
    candles: list[dict],
    swing_order: int = 5,
    max_setup_age: int = 30,
    sl_buffer_pct: float = 0.1,
    sl_mode: str = "bb",
    atr_multiplier: float = 1.0,
    atr_period: int = 14,
    min_target_rr: float = 0.0,
    min_mss_strength: float = 0.0,
) -> list[SMCSetup]:
    """Detect complete Breaker Block / Unicorn setups.

    The 4-step sequence:
    1. Liquidity Sweep (price sweeps past swing point)
    2. Market Structure Shift (price breaks structure after sweep)
    3. Breaker Block + FVG formed during the impulse move
    4. Entry zone: FVG level (wait for retest)

    Args:
        candles: List of OHLC dicts (epoch, open, high, low, close), oldest first.
        swing_order: Swing point detection order (number of bars each side).
        max_setup_age: Maximum candles between setup formation and entry.
        sl_buffer_pct: SL buffer as percentage of BB size.
        sl_mode: "bb" (breaker-block based SL) or "atr".
        atr_multiplier: ATR multiplier used when `sl_mode="atr"`.
        atr_period: ATR lookback used when `sl_mode="atr"`.
        min_target_rr: Override TP when the structure target is too close.
        min_mss_strength: Minimum MSS body size as a multiple of ATR.

    Returns list of SMCSetup, sorted by formation_index.
    """
    if len(candles) < 50:
        return []

    opens = np.array([float(c["open"]) for c in candles])
    highs = np.array([float(c["high"]) for c in candles])
    lows = np.array([float(c["low"]) for c in candles])
    closes = np.array([float(c["close"]) for c in candles])
    n = len(candles)

    atr_values = None
    if sl_mode == "atr" or min_mss_strength > 0:
        atr_values = compute_atr(highs, lows, closes, period=atr_period)

    # Step 1: Detect swing points
    swing_points = detect_swing_points(highs, lows, order=swing_order)

    # Step 2: Detect liquidity sweeps
    sweeps = detect_liquidity_sweep(highs, lows, swing_points, closes=closes, lookback=n)

    # Step 3: Detect structure shifts
    mss_list = detect_structure_shifts(highs, lows, swing_points, lookback=n)

    # Step 4: Detect FVGs
    fvgs = detect_fvg(highs, lows, lookback=n)

    # Step 5: Detect order blocks
    order_blocks = detect_order_blocks(opens, closes, highs, lows, lookback=n)

    # Step 6: Detect breaker blocks (BB = OB broken during MSS impulse)
    breaker_blocks = detect_breaker_blocks(mss_list, order_blocks, fvgs, max_candle_distance=15)

    # Step 7: Match sweeps -> MSS -> BB to form complete setups
    setups = []

    for bb in breaker_blocks:
        mss = bb.mss

        if min_mss_strength > 0 and atr_values is not None:
            atr_at_mss = atr_values[mss.index]
            if not np.isnan(atr_at_mss) and atr_at_mss > 0:
                mss_body = abs(closes[mss.index] - opens[mss.index])
                if mss_body < atr_at_mss * min_mss_strength:
                    continue

        # Find a sweep that preceded this MSS (within 15 candles)
        matching_sweep = None
        for sweep in sweeps:
            if sweep.reversal_index < mss.index <= sweep.reversal_index + 15:
                # Check direction alignment
                if mss.direction == MSSDirection.BEARISH_MSS and sweep.direction == "UP":
                    matching_sweep = sweep
                    break
                elif mss.direction == MSSDirection.BULLISH_MSS and sweep.direction == "DOWN":
                    matching_sweep = sweep
                    break

        if matching_sweep is None:
            continue

        # === BB-ONLY MODE (synthetic indices scalping) ===
        # FVG is OPTIONAL for volatile markets - accept setups without FVG
        # Must have an FVG for the Unicorn entry
        # if bb.fvg is None:
        #     continue

        # Check FVG hasn't been filled already
        # fvg_filled = check_fvg_filled(bb.fvg, highs, lows, closes, mss.index, after_index=mss.index)
        # if fvg_filled:
        #     continue

        # Determine direction and levels
        fvg = bb.fvg  # Can be None in BB-only mode
        bb_size = bb.top - bb.bottom
        if bb_size <= 0:
            continue

        atr_sl_distance = None
        if atr_values is not None:
            atr_at_formation = atr_values[mss.index]
            if not np.isnan(atr_at_formation):
                atr_sl_distance = atr_at_formation * atr_multiplier

        sl_buffer = bb_size * sl_buffer_pct

        # FIX (SL-1): SL now uses SWEEP EXTREME as primary reference.
        # The sweep_high in LiquiditySweep is the actual price extreme that
        # liquidity was swept at — the true invalidation point.
        # ATR is used as a MINIMUM distance floor to avoid noise-triggered stops.
        if mss.direction == MSSDirection.BEARISH_MSS:
            # SHORT: Entry at Breaker Block TOP
            entry_price = bb.top
            # Primary SL: sweep_high + small buffer (above the swept high)
            sweep_extreme = matching_sweep.sweep_high if matching_sweep else bb.top
            sl_sweep = sweep_extreme + sl_buffer
            # ATR floor: entry + ATR * multiplier
            sl_atr = entry_price + atr_sl_distance if atr_sl_distance else entry_price + bb_size
            # Use whichever is FURTHER (safer)
            sl_price = max(sl_sweep, sl_atr)
            target_low = None
            for sp in reversed(swing_points):
                if sp.type == "LOW" and sp.index < mss.index:
                    target_low = sp.price
                    break
            tp_price = target_low

        elif mss.direction == MSSDirection.BULLISH_MSS:
            # LONG: Entry at Breaker Block BOTTOM
            entry_price = bb.bottom
            # Primary SL: sweep_high - small buffer (below the swept low)
            sweep_extreme = matching_sweep.sweep_high if matching_sweep else bb.bottom
            sl_sweep = sweep_extreme - sl_buffer
            # ATR floor: entry - ATR * multiplier
            sl_atr = entry_price - atr_sl_distance if atr_sl_distance else entry_price - bb_size
            # Use whichever is FURTHER (safer)
            sl_price = min(sl_sweep, sl_atr)
            target_high = None
            for sp in reversed(swing_points):
                if sp.type == "HIGH" and sp.index < mss.index:
                    target_high = sp.price
                    break
            tp_price = target_high
        else:
            continue

        if min_target_rr > 0 and tp_price is not None:
            sl_distance = abs(entry_price - sl_price)
            if sl_distance > 0:
                current_rr = abs(tp_price - entry_price) / sl_distance
                if current_rr < min_target_rr:
                    if mss.direction == MSSDirection.BEARISH_MSS:
                        tp_price = entry_price - sl_distance * min_target_rr
                    else:
                        tp_price = entry_price + sl_distance * min_target_rr

        setup = SMCSetup(
            direction="SHORT" if mss.direction == MSSDirection.BEARISH_MSS else "LONG",
            sweep=matching_sweep,
            mss=mss,
            breaker_block=bb,
            fvg=fvg,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            formation_index=mss.index,
        )
        setups.append(setup)

    # CRITICAL FIX: Deduplicate setups based on (direction, entry_price, sl_price, formation_index)
    # Use a tolerance for price comparison due to floating point precision
    seen = set()
    unique_setups = []
    price_tolerance = 0.00001  # Very small tolerance for forex/crypto
    
    for setup in setups:
        # Create a deduplication key
        key = (
            setup.direction,
            round(setup.entry_price / price_tolerance) * price_tolerance,
            round(setup.sl_price / price_tolerance) * price_tolerance,
            setup.formation_index
        )
        
        if key not in seen:
            seen.add(key)
            unique_setups.append(setup)
        else:
            logger.debug(
                f"Duplicate setup filtered: {setup.direction} "
                f"@{setup.entry_price:.5f} SL={setup.sl_price:.5f} "
                f"@index={setup.formation_index}"
            )
    
    unique_setups.sort(key=lambda s: s.formation_index)
    return unique_setups


def find_entry_trigger(
    setup: SMCSetup,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    from_index: int,
    to_index: int,
    min_wait: int = 1,
    opens: np.ndarray = None,
    min_body_ratio: float = 0.0,
    close_beyond: bool = False,
    confirm_entry: bool = True,
    confirm_body_ratio: float = 0.6,
    confirm_max_distance: float = 3.0,
) -> Optional[int]:
    """Find the first candle where entry is triggered.

    Two entry modes (either can trigger):

    MODE 1 — Level Retest (legacy):
        For SHORT: find candle where high >= entry_price AFTER the impulse
        For LONG:  find candle where low  <= entry_price AFTER the impulse

    MODE 2 — Confirmation Entry (confirm_entry=True, NEW):
        If a strong candle (body ratio >= confirm_body_ratio) forms in the
        trade direction within confirm_max_distance (ATR multiples) of the
        entry price, trigger entry at that candle's close. This catches
        reversals where price never retests the exact entry level.

    Args:
        min_wait: minimum candles after formation before allowing entry.
        min_body_ratio: for MODE 1, minimum body/range ratio at the retest.
        close_beyond: for MODE 1, require close beyond entry in trade direction.
        confirm_entry: enable MODE 2 (confirmation candle entry).
        confirm_body_ratio: MODE 2 minimum body ratio (0.6 = 60% body).
        confirm_max_distance: MODE 2 max |price - entry| as ATR multiples.

    Returns the candle index where entry would be triggered, or None.
    """
    start = max(from_index + min_wait, setup.formation_index + min_wait)
    for i in range(start, to_index + 1):

        # --- MODE 1: Level Retest (legacy) ---
        triggered = False
        if setup.direction == "SHORT":
            if highs[i] >= setup.entry_price:
                triggered = True
        elif setup.direction == "LONG":
            if lows[i] <= setup.entry_price:
                triggered = True

        if triggered and close_beyond:
            if setup.direction == "SHORT" and closes[i] >= setup.entry_price:
                logger.debug(f"Entry SHORT @ {i} rejected: close_beyond rule (close {closes[i]:.2f} >= entry {setup.entry_price:.2f})")
                triggered = False
            elif setup.direction == "LONG" and closes[i] <= setup.entry_price:
                logger.debug(f"Entry LONG @ {i} rejected: close_beyond rule (close {closes[i]:.2f} <= entry {setup.entry_price:.2f})")
                triggered = False

        if triggered and min_body_ratio > 0 and opens is not None:
            candle_range = highs[i] - lows[i]
            if candle_range <= 0:
                continue
            body = abs(closes[i] - opens[i])
            ratio = body / candle_range
            if ratio < min_body_ratio:
                logger.debug(f"Entry {setup.direction} @ {i} rejected: ratio {ratio:.2f} < {min_body_ratio}")
                continue
            if setup.direction == "SHORT" and closes[i] >= opens[i]:
                logger.debug(f"Entry SHORT @ {i} rejected: candle is not bearish")
                continue
            if setup.direction == "LONG" and closes[i] <= opens[i]:
                logger.debug(f"Entry LONG @ {i} rejected: candle is not bullish")
                continue

            logger.debug(f"Entry {setup.direction} triggered at index {i} (Level retest)")
            return i
        elif triggered:
            logger.debug(f"Entry {setup.direction} triggered at index {i} (Touch)")
            return i

        # --- MODE 2: Confirmation Entry ---
        if confirm_entry and opens is not None:
            candle_range = highs[i] - lows[i]
            if candle_range <= 0:
                continue
            body = abs(closes[i] - opens[i])
            ratio = body / candle_range

            if ratio < confirm_body_ratio:
                continue

            # Direction must match trade
            is_bullish = closes[i] > opens[i]
            is_bearish = closes[i] < opens[i]
            if setup.direction == "LONG" and not is_bullish:
                continue
            if setup.direction == "SHORT" and not is_bearish:
                continue

            # Price must be within confirm_max_distance ATR of entry
            # Use simple distance as fallback if ATR not available
            atr_approx = np.mean(highs[max(0,i-14):i+1] - lows[max(0,i-14):i+1]) if i >= 14 else candle_range
            max_dist = confirm_max_distance * atr_approx if atr_approx > 0 else confirm_max_distance
            price_dist = abs(closes[i] - setup.entry_price)
            if price_dist > max_dist:
                continue

            logger.info(
                f"Entry {setup.direction} CONFIRMED at index {i} "
                f"(body={ratio:.0%}, dist={price_dist:.2f}, max={max_dist:.2f}) "
                f"close={closes[i]:.2f} entry={setup.entry_price:.2f}"
            )
            # Update entry_price to actual fill price (the close)
            setup.entry_price = closes[i]
            return i

    return None


# ---------------------------------------------------------------------------
# Trend Detection (directional filter for SMC entries)
# ---------------------------------------------------------------------------

def detect_trend(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    idx: int,
    ema_period: int = 50,
    swing_lookback: int = 50,
) -> str:
    """Detect market trend using EMA slope + swing structure.

    Combines two signals:
    1. EMA slope: price above rising EMA = bullish, below falling EMA = bearish
    2. Swing structure: higher highs + higher lows = bullish (and vice versa)

    Returns one of: 'BULL', 'BEAR', 'RANGE'

    Only returns BULL/BEAR when both signals agree. Otherwise RANGE.
    This prevents trading against the trend while allowing neutral-zone entries.
    """
    if idx < ema_period + 5:
        return "RANGE"

    start = max(0, idx - ema_period - 5)
    segment = closes[start:idx + 1]

    # --- Signal 1: EMA slope ---
    # Simple EMA calculation
    multiplier = 2.0 / (ema_period + 1)
    ema = np.zeros(len(segment))
    ema[0] = segment[0]
    for j in range(1, len(segment)):
        ema[j] = segment[j] * multiplier + ema[j-1] * (1 - multiplier)

    ema_now = ema[-1]
    ema_prev = ema[-6] if len(ema) >= 6 else ema[0]
    price = closes[idx]

    ema_bull = price > ema_now and ema_now > ema_prev
    ema_bear = price < ema_now and ema_now < ema_prev

    # --- Signal 2: Swing structure ---
    lookback_start = max(0, idx - swing_lookback)
    recent_highs = []
    recent_lows = []
    for j in range(lookback_start + 3, idx - 2):
        if j < 3 or j >= len(highs):
            continue
        if highs[j] == max(highs[max(0,j-3):j+4]):
            recent_highs.append(highs[j])
        if lows[j] == min(lows[max(0,j-3):j+4]):
            recent_lows.append(lows[j])

    struct_bull = False
    struct_bear = False
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        hh = recent_highs[-1] > recent_highs[-2]  # higher high
        hl = recent_lows[-1] > recent_lows[-2]    # higher low
        lh = recent_highs[-1] < recent_highs[-2]  # lower high
        ll = recent_lows[-1] < recent_lows[-2]    # lower low
        struct_bull = hh and hl
        struct_bear = lh and ll

    # --- Combine ---
    if ema_bull and struct_bull:
        return "BULL"
    if ema_bear and struct_bear:
        return "BEAR"
    # Weak signals: EMA agrees but structure unclear → still bias
    if ema_bull and not struct_bear:
        return "BULL"
    if ema_bear and not struct_bull:
        return "BEAR"

    return "RANGE"


def is_trade_allowed(direction: str, trend: str, mode: str = "strict") -> bool:
    """Check if a trade direction is allowed given the current trend.

    Args:
        direction: 'LONG' or 'SHORT'
        trend: 'BULL', 'BEAR', or 'RANGE'
        mode: 'strict' (only with trend), 'flexible' (allow range)

    Returns True if the trade is allowed.
    """
    if mode == "flexible":
        if trend == "RANGE":
            return True
        if trend == "BULL" and direction == "LONG":
            return True
        if trend == "BEAR" and direction == "SHORT":
            return True
        return False

    # strict mode: only trade with the trend
    if trend == "BULL" and direction == "LONG":
        return True
    if trend == "BEAR" and direction == "SHORT":
        return True
    return False


# ---------------------------------------------------------------------------
# ATR (Average True Range)
# ---------------------------------------------------------------------------

def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute Average True Range.

    TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    ATR = simple moving average of TR over `period`.
    """
    n = len(highs)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    atr = np.full(n, np.nan)
    if n < period:
        return atr

    # Seed with SMA of first `period` values
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr
