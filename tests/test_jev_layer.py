"""Unit tests for core.jev_layer (composite scoring, state building, judgment parsing)."""

import asyncio

import numpy as np
import pytest
from typesafe_sdk import Noul, NoulAnswer, Score, ScoreAnswer, SystemOneResponse, Usage

from core.jev_layer import (
    DEFAULT_WEIGHTS,
    SCORE_DIMENSIONS,
    build_market_state,
    build_questions,
    composite_score,
    judge_market,
    normalize_score,
)
from core.strategy import SignalType, TradeSignal


def make_candles(n: int = 260, seed: int = 7, start: float = 100.0, granularity: int = 60) -> list[dict]:
    """Deterministic random-walk OHLC candles, oldest first."""
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(rng.normal(0, 0.35, n))
    base_epoch = 1_700_000_000
    candles = []
    for i in range(n):
        open_ = float(closes[i - 1]) if i else start
        close = float(closes[i])
        spread = abs(float(rng.normal(0.25, 0.1)))
        candles.append({
            "epoch": base_epoch + i * granularity,
            "open": round(open_, 5),
            "high": round(max(open_, close) + spread, 5),
            "low": round(min(open_, close) - spread, 5),
            "close": round(close, 5),
        })
    return candles


def make_signal(direction=SignalType.LONG, entry=100.0, sl_distance=2.0, rr=2.0) -> TradeSignal:
    sign = 1 if direction == SignalType.LONG else -1
    return TradeSignal(
        signal_type=direction,
        entry_price=entry,
        candle_size=1.0,
        sl_distance=sl_distance,
        sl_price=entry - sign * sl_distance,
        tp_price=entry + sign * sl_distance * rr,
        ema_200=entry - sign * 1.0,
        rsi_14=55.0 if direction == SignalType.LONG else 45.0,
    )


class FakeClient:
    """Stand-in for AsyncTypeSafeClient returning a canned SystemOneResponse."""

    def __init__(self, score_by_dim: dict[str, float], noul: float = 0.75):
        self.score_by_dim = score_by_dim
        self.noul = noul
        self.last_state = None

    async def system_one(self, state, questions):
        self.last_state = state
        legend = {i: f"level{i}" for i in range(5)}
        answers = {}
        for dim, raw in self.score_by_dim.items():
            answers[dim] = ScoreAnswer(
                score=raw,
                confidence=0.8,
                legend=legend,
                probabilities={i: 0.2 for i in range(5)},
            )
        answers["context_ok"] = NoulAnswer(noul=self.noul)
        return SystemOneResponse(
            model="jev-fake",
            usage=Usage(input_tokens=120, output_tokens=8),
            answers=answers,
        )


# --- composite math -------------------------------------------------------

def test_composite_score_is_weighted_mean():
    scores = {"a": 1.0, "b": 0.0}
    assert composite_score(scores, {"a": 0.75, "b": 0.25}) == pytest.approx(0.75)


def test_composite_score_normalizes_weights():
    scores = {"a": 0.5, "b": 0.5}
    assert composite_score(scores, {"a": 3.0, "b": 1.0}) == pytest.approx(0.5)


def test_composite_score_rejects_key_mismatch():
    with pytest.raises(ValueError):
        composite_score({"a": 1.0}, {"a": 1.0, "b": 1.0})
    with pytest.raises(ValueError):
        composite_score({"a": 1.0, "b": 1.0}, {"a": 1.0})


def test_composite_score_rejects_zero_weight_sum():
    with pytest.raises(ValueError):
        composite_score({"a": 1.0}, {"a": 0.0})


def test_normalize_score_maps_and_clamps():
    assert normalize_score(0.0) == 0.0
    assert normalize_score(4.0) == 1.0
    assert normalize_score(2.0) == pytest.approx(0.5)
    assert normalize_score(9.0) == 1.0
    assert normalize_score(-3.0) == 0.0


# --- state building -------------------------------------------------------

def test_build_market_state_structure():
    candles = make_candles()
    state = build_market_state(candles, make_signal(), symbol="R_100", granularity=60)

    assert set(state) == {"market", "recent_candles", "indicators", "signal", "session"}
    assert state["market"]["symbol"] == "R_100"
    assert state["market"]["granularity_label"] == "M1"
    assert len(state["recent_candles"]) == 20
    assert state["recent_candles"][-1]["close"] == candles[-1]["close"]
    assert set(state["indicators"]) == {
        "close", "ema_200", "rsi_14", "atr_14",
        "atr_pct_of_price", "avg_candle_range_20", "last_candle_range",
    }
    assert state["signal"]["direction"] == "LONG"
    assert state["signal"]["risk_reward"] == 2.0
    assert 0 <= state["session"]["utc_hour"] <= 23


def test_build_market_state_no_lookahead():
    candles = make_candles(n=300)
    cut = 250  # strategy only saw candles[:250]
    state = build_market_state(candles[:cut], make_signal(), symbol="R_100", granularity=60)
    assert state["recent_candles"][-1]["close"] == candles[cut - 1]["close"]
    assert state["recent_candles"][-1]["utc_time"] == _fmt(candles[cut - 1]["epoch"])
    # The most recent embedded candle is the signal candle, nothing after it.
    assert max(c["utc_time"] for c in state["recent_candles"]) == _fmt(candles[cut - 1]["epoch"])


def _fmt(epoch: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def test_build_market_state_requires_enough_candles():
    with pytest.raises(ValueError):
        build_market_state(make_candles(n=100), make_signal())


# --- questions ------------------------------------------------------------

def test_build_questions_rubrics():
    questions = build_questions()
    assert set(questions) == set(SCORE_DIMENSIONS) | {"context_ok"}

    for dim in SCORE_DIMENSIONS:
        question = questions[dim]
        assert isinstance(question, Score)
        assert len(question.criteria) == 5
        assert all(isinstance(level, str) and level.strip() for level in question.criteria)
        # Instructions must point at the state via backticked paths.
        assert "`" in question.instructions

    noul = questions["context_ok"]
    assert isinstance(noul, Noul)
    assert set(noul.criteria) == {"true", "false"}


# --- judgment parsing -----------------------------------------------------

def test_judge_market_parses_response():
    raw = {"trend_quality": 4.0, "momentum": 3.0, "volatility_fit": 2.0, "setup_quality": 3.0, "session_fit": 2.0}
    client = FakeClient(raw, noul=0.7)

    judgment = asyncio.run(judge_market(client, {"some": "state"}))

    assert judgment.model == "jev-fake"
    assert judgment.input_tokens == 120
    assert judgment.noul_context_ok == pytest.approx(0.7)
    assert judgment.scores["trend_quality"] == pytest.approx(1.0)
    assert judgment.scores["session_fit"] == pytest.approx(0.5)
    expected = sum(raw[d] / 4 * w for d, w in DEFAULT_WEIGHTS.items())
    assert judgment.composite == pytest.approx(expected)
    assert judgment.confidences["trend_quality"] == 0.8
