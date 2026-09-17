"""Jev (TypeSafe System One) decision layer: composite scoring for trade filtering.

Mode B — composite scoring. Each candidate signal is scored by Jev on independent
dimensions (typed Score questions); code combines them with weights into a single
composite used as an entry gate. Raw scores are kept alongside the judgment so
weights and thresholds can be re-tuned offline without re-querying the model.

Integration contract:
- Strategies, risk management and execution stay in code (core/strategy.py, trader.py).
- This layer only judges the market context around an already-computed signal.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from typesafe_sdk import Noul, Score

from core.indicators import compute_indicators
from core.logger import get_logger
from core.smc_indicators import compute_atr

logger = get_logger("jev-layer")

_LEVELS = 5  # rubric levels per Score dimension (0..4)

#: Default weights of the composite score. Keys must match SCORE_DIMENSIONS.
#: Weights are normalized by their sum, so they do not need to total 1.
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend_quality": 0.30,
    "momentum": 0.20,
    "volatility_fit": 0.20,
    "setup_quality": 0.20,
    "session_fit": 0.10,
}

SCORE_DIMENSIONS = tuple(DEFAULT_WEIGHTS)


@dataclass
class JevJudgment:
    """Raw Jev output for one signal, kept complete for offline re-tuning."""
    scores: dict[str, float]                      # normalized 0..1 per dimension
    composite: float                              # weighted combination, 0..1
    noul_context_ok: float                        # P(context supports the entry)
    probabilities: dict[str, dict[int, float]]    # raw level distributions
    confidences: dict[str, float]                 # per-dimension confidence
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "scores": self.scores,
            "composite": round(self.composite, 4),
            "noul_context_ok": round(self.noul_context_ok, 4),
            "probabilities": self.probabilities,
            "confidences": self.confidences,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JevJudgment":
        return cls(
            scores=data["scores"],
            composite=data["composite"],
            noul_context_ok=data["noul_context_ok"],
            probabilities=data["probabilities"],
            confidences=data["confidences"],
            model=data.get("model", ""),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
        )


def has_api_key() -> bool:
    """True when a TypeSafe API key is available (env or .env via config)."""
    if os.getenv("TYPESAFE_API_KEY"):
        return True
    try:
        from config import TYPESAFE_API_KEY
        return bool(TYPESAFE_API_KEY)
    except ImportError:
        return False


def normalize_score(raw: float, levels: int = _LEVELS) -> float:
    """Map a raw Score position (0..levels-1) to 0..1, clamped."""
    return max(0.0, min(1.0, raw / (levels - 1)))


def composite_score(scores: dict[str, float], weights: Optional[dict[str, float]] = None) -> float:
    """Weighted mean of normalized dimension scores.

    Weights are normalized by their sum so any positive scale works. Raises
    ValueError when weights and scores do not cover exactly the same dimensions.
    """
    weights = weights or DEFAULT_WEIGHTS
    missing = set(weights) - set(scores)
    unknown = set(scores) - set(weights)
    if missing or unknown:
        raise ValueError(f"score/weight mismatch — missing: {sorted(missing)}, unknown: {sorted(unknown)}")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights must have a positive sum")
    return sum(scores[dim] * w for dim, w in weights.items()) / total


def _r(value: float, digits: int = 5) -> float:
    """Round a price-like float for compact state payloads."""
    return round(float(value), digits)


def _fmt_epoch(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _granularity_label(seconds: int) -> str:
    labels = {60: "M1", 120: "M2", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1"}
    return labels.get(seconds, f"{seconds}s")


def build_market_state(candles: list[dict], signal, symbol: str = "",
                       granularity: int = 60, lookback: int = 20,
                       signal_reason: str = "") -> dict:
    """Assemble the JSON state judged by Jev for one candidate signal.

    Args:
        candles: the window the strategy saw, oldest first, ending at the signal
            candle. No candle after the signal must be present (no lookahead).
        signal: TradeSignal-like object (signal_type, entry_price, sl_price,
            tp_price, sl_distance).
        symbol: symbol name, e.g. "R_100".
        granularity: candle size in seconds.
        lookback: number of recent candles embedded in the state.
        signal_reason: optional human hint, e.g. "bullish_engulfing".

    Raises:
        ValueError: when the window is too short for the indicators.
    """
    indicators = compute_indicators(candles)
    if not indicators:
        raise ValueError(f"window too short for indicators: {len(candles)} candles")

    highs = np.array([float(c["high"]) for c in candles])
    lows = np.array([float(c["low"]) for c in candles])
    closes = np.array([float(c["close"]) for c in candles])
    atr = compute_atr(highs, lows, closes, period=14)

    ranges = highs - lows
    avg_range = float(np.mean(ranges[-20:]))

    direction = signal.signal_type.value
    sl_distance = abs(signal.entry_price - signal.sl_price)
    tp_distance = abs(signal.tp_price - signal.entry_price)
    rr = tp_distance / sl_distance if sl_distance > 0 else 0.0

    recent = [
        {
            "utc_time": _fmt_epoch(c["epoch"]),
            "open": _r(c["open"]), "high": _r(c["high"]),
            "low": _r(c["low"]), "close": _r(c["close"]),
        }
        for c in candles[-lookback:]
    ]

    signal_epoch = int(candles[-1]["epoch"])
    signal_dt = datetime.fromtimestamp(signal_epoch, tz=timezone.utc)

    atr_value = float(atr[-1]) if len(atr) and not np.isnan(atr[-1]) else avg_range
    return {
        "market": {
            "symbol": symbol,
            "granularity_seconds": granularity,
            "granularity_label": _granularity_label(granularity),
        },
        "recent_candles": recent,
        "indicators": {
            "close": _r(indicators["close"]),
            "ema_200": _r(indicators["ema_200"]),
            "rsi_14": round(float(indicators["rsi_14"]), 2),
            "atr_14": _r(atr_value),
            "atr_pct_of_price": _r(atr_value / indicators["close"] * 100.0, 3),
            "avg_candle_range_20": _r(avg_range),
            "last_candle_range": _r(ranges[-1]),
        },
        "signal": {
            "direction": direction,
            "reason": signal_reason or f"{direction.lower()}_signal",
            "entry_price": _r(signal.entry_price),
            "sl_price": _r(signal.sl_price),
            "tp_price": _r(signal.tp_price),
            "risk_reward": _r(rr, 2),
        },
        "session": {
            "utc_hour": signal_dt.hour,
            "utc_weekday": signal_dt.strftime("%a"),
        },
    }


def build_questions() -> dict:
    """The question set: one Score per dimension plus a Noul context gate."""
    return {
        "trend_quality": Score(
            instructions=(
                "Judge the strength and cleanliness of the prevailing trend for a "
                "scalping entry in the direction of `signal.direction`, using "
                "`indicators.ema_200`, `indicators.close` and the sequence of closes "
                "in `recent_candles`."
            ),
            criteria=[
                "No trend: closes oscillate across the EMA with alternating sides and no direction.",
                "Weak sideways drift: price barely holds the correct side of the EMA, candles overlap heavily.",
                "Moderate trend: closes stay on one side of the EMA but pullbacks are deep and frequent.",
                "Clear trend: consistent closes on the correct side of the EMA with orderly, shallow pullbacks.",
                "Strong clean trend: steady progression of highs/lows in the trade direction, tight alignment with the EMA.",
            ],
        ),
        "momentum": Score(
            instructions=(
                "Judge whether short-term momentum supports `signal.direction`, using "
                "`indicators.rsi_14` and the bodies of the last candles in `recent_candles`."
            ),
            criteria=[
                "Momentum strongly opposes the trade direction (e.g. deeply overbought for a long, oversold for a short).",
                "Momentum leans against the trade direction or is clearly fading.",
                "Momentum is neutral: RSI near its midpoint, candle bodies small and mixed.",
                "Momentum supports the trade direction: RSI on the right side, recent bodies in the trade direction.",
                "Momentum strongly supports the trade direction: RSI clearly extended the right way, decisive recent bodies.",
            ],
        ),
        "volatility_fit": Score(
            instructions=(
                "Judge whether current volatility is suitable for this scalp, using "
                "`indicators.atr_pct_of_price`, `indicators.avg_candle_range_20` and "
                "`indicators.last_candle_range`. Both dead-flat and erratic volatility are bad."
            ),
            criteria=[
                "Extreme volatility: erratic ranges several times the recent average; the stop is likely to be swept immediately.",
                "Problematic volatility: either nearly flat (no movement to reach the target) or clearly excessive and unstable.",
                "Acceptable but not ideal: moderately low or moderately elevated versus the recent average.",
                "Good volatility: near the recent average with a steady rhythm.",
                "Ideal volatility: healthy and steady, slightly expanding in the trade direction with contained candle ranges.",
            ],
        ),
        "setup_quality": Score(
            instructions=(
                "Judge the technical quality of the entry signal itself, using "
                "`signal.reason`, `indicators.last_candle_range` and the last two "
                "candles of `recent_candles` (for engulfing patterns)."
            ),
            criteria=[
                "Marginal signal: tiny body that barely qualifies, e.g. engulfing a doji-like previous candle.",
                "Weak signal: valid pattern but small bodies and indecisive closes.",
                "Reasonable signal: clear pattern with a decisive body and a normal close.",
                "Good signal: decisive body fully engulfing or breaking a substantial predecessor, closing near the extreme.",
                "Textbook signal: high-conviction pattern, large decisive body, clean close at the extreme, no upper/lower wicks against the direction.",
            ],
        ),
        "session_fit": Score(
            instructions=(
                "Judge whether the time of `session.utc_hour` typically suits active "
                "scalping on this market type (`market.symbol`). For 24/7 synthetic "
                "indices any hour is comparable; for FX/metals, judge usual liquidity hours."
            ),
            criteria=[
                "Clearly unfavorable hour: typically dead or erratic for this market type.",
                "Unfavorable hour: liquidity usually thin or unstable at this time.",
                "Average hour: nothing special, comparable to most hours.",
                "Good hour: typically active with orderly movement.",
                "Prime hour: typically the most liquid and orderly part of the day for this market type.",
            ],
        ),
        "context_ok": Noul(
            instructions=(
                "Considering `recent_candles`, `indicators` and `signal`, do overall "
                "market conditions coherently support taking this scalping trade in "
                "the direction of `signal.direction` right now?"
            ),
            criteria={
                "true": "Trend, momentum, volatility and the signal itself coherently support the entry.",
                "false": "Conditions are mixed, contradictory or unfavorable for this entry.",
            },
        ),
    }


async def judge_market(client, state: dict, weights: Optional[dict[str, float]] = None) -> JevJudgment:
    """Ask Jev the full question set for one market state and build the judgment."""
    from typesafe_sdk import NoulAnswer, ScoreAnswer  # answer types, not question types

    response = await client.system_one(state=state, questions=build_questions())
    answers = response.answers

    scores: dict[str, float] = {}
    probabilities: dict[str, dict[int, float]] = {}
    confidences: dict[str, float] = {}
    for dim in SCORE_DIMENSIONS:
        answer = answers[dim]
        if not isinstance(answer, ScoreAnswer):
            raise TypeError(f"expected ScoreAnswer for '{dim}', got {type(answer).__name__}")
        scores[dim] = normalize_score(answer.score, len(answer.legend) or _LEVELS)
        probabilities[dim] = {int(k): v for k, v in answer.probabilities.items()}
        confidences[dim] = answer.confidence

    noul_answer = answers["context_ok"]
    if not isinstance(noul_answer, NoulAnswer):
        raise TypeError(f"expected NoulAnswer for 'context_ok', got {type(noul_answer).__name__}")

    usage = response.usage
    return JevJudgment(
        scores=scores,
        composite=composite_score(scores, weights),
        noul_context_ok=noul_answer.noul,
        probabilities=probabilities,
        confidences=confidences,
        model=response.model,
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
    )
