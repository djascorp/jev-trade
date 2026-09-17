"""Unit tests for core.jev_backtest (exit simulation and engine consistency)."""

import asyncio

import numpy as np
import pytest

from core.backtest import run_backtest
from core.jev_backtest import ExitReason, _simulate_exit, run_jev_backtest
from core.strategy import SignalType, TradingLabStrategy


def candle(epoch, open_, high, low, close):
    return {"epoch": epoch, "open": open_, "high": high, "low": low, "close": close}


def make_candles(n: int = 1500, seed: int = 42, start: float = 640.0, granularity: int = 60) -> list[dict]:
    """Deterministic random-walk OHLC candles, oldest first."""
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(rng.normal(0, 0.6, n))
    base_epoch = 1_700_000_000
    candles = []
    for i in range(n):
        open_ = float(closes[i - 1]) if i else start
        close = float(closes[i])
        spread = abs(float(rng.normal(0.4, 0.15)))
        candles.append({
            "epoch": base_epoch + i * granularity,
            "open": round(open_, 5),
            "high": round(max(open_, close) + spread, 5),
            "low": round(min(open_, close) - spread, 5),
            "close": round(close, 5),
        })
    return candles


# --- exit simulation ------------------------------------------------------

def test_simulate_exit_tp_hit():
    candles = [candle(i, 100, 101, 99, 100.5) for i in range(5)]
    # Candle 1 reaches the take profit.
    candles[1] = candle(1, 100.5, 104.5, 100.0, 104.2)
    reason, profit, r, idx = _simulate_exit("LONG", 100.0, 98.0, 104.0, candles, 0)
    assert reason == ExitReason.TP
    assert profit == pytest.approx(4.0)
    assert r == pytest.approx(2.0)
    assert idx == 1


def test_simulate_exit_sl_hit_is_negative_r():
    candles = [candle(i, 100, 101, 97.5, 99.0) for i in range(5)]
    reason, profit, r, idx = _simulate_exit("LONG", 100.0, 98.0, 104.0, candles, 0)
    assert reason == ExitReason.SL
    assert profit == pytest.approx(-2.0)
    assert r == pytest.approx(-1.0)


def test_simulate_exit_sl_checked_before_tp():
    # Both levels touched on the same candle: SL wins (engine order).
    candles = [candle(0, 100, 105, 97, 99)]
    reason, _, r, _ = _simulate_exit("LONG", 100.0, 98.0, 104.0, candles, 0)
    assert reason == ExitReason.SL
    assert r == pytest.approx(-1.0)


def test_simulate_exit_short_side():
    candles = [candle(i, 100, 100.5, 95, 96) for i in range(5)]
    reason, profit, r, _ = _simulate_exit("SHORT", 100.0, 102.0, 96.0, candles, 0)
    assert reason == ExitReason.TP
    assert profit == pytest.approx(4.0)
    assert r == pytest.approx(2.0)


def test_simulate_exit_eod_force_close():
    # Never touches SL or TP: closed at the last close.
    candles = [candle(i, 100, 100.8, 99.2, 100.1) for i in range(5)]
    reason, profit, r, idx = _simulate_exit("LONG", 100.0, 98.0, 104.0, candles, 0)
    assert reason == ExitReason.EOD
    assert profit == pytest.approx(0.1)
    assert idx == len(candles) - 1


# --- engine consistency with the reference backtest ------------------------

def test_dry_run_matches_reference_engine():
    candles = make_candles()

    reference = run_backtest(candles, TradingLabStrategy())
    report = asyncio.run(run_jev_backtest(candles, TradingLabStrategy(), symbol="R_100", granularity=60))

    # A random walk with these seeds must produce signals for the comparison to matter.
    assert reference.total_trades > 0, "seed produced no signals; adjust the generator"

    assert report.total_signals == reference.total_trades
    assert report.judged_signals == 0  # dry-run: no client, no API calls
    assert report.judgment_errors == 0
    assert len(report.records) == len(reference.trades)

    for mine, ref in zip(report.records, reference.trades):
        assert mine.direction == ref.direction
        assert mine.exit_reason == ref.exit_reason
        assert mine.entry_price == pytest.approx(ref.entry_price)
        assert mine.profit_pips == pytest.approx(ref.profit_pips)
        # Signed R: wins positive, losses negative, magnitude matches reference
        # (reference rounds to 2 decimals, this engine to 3).
        assert abs(mine.r_reward) == pytest.approx(ref.r_reward, abs=5e-3)
        assert (mine.r_reward > 0) == (ref.profit_pips > 0)

    assert report.baseline.trades == reference.total_trades
    assert report.baseline.wins == reference.wins


def test_max_judgments_limits_api_calls():
    candles = make_candles()

    calls = {"n": 0}

    class CountingClient:
        async def system_one(self, state, questions):
            from typesafe_sdk import NoulAnswer, ScoreAnswer, SystemOneResponse, Usage
            calls["n"] += 1
            legend = {i: f"level{i}" for i in range(5)}
            answers = {
                dim: ScoreAnswer(score=2.0, confidence=0.5, legend=legend,
                                 probabilities={i: 0.2 for i in range(5)})
                for dim in ("trend_quality", "momentum", "volatility_fit", "setup_quality", "session_fit")
            }
            answers["context_ok"] = NoulAnswer(noul=0.6)
            return SystemOneResponse(model="jev-fake", usage=Usage(input_tokens=100, output_tokens=5), answers=answers)

    report = asyncio.run(run_jev_backtest(
        candles, TradingLabStrategy(), symbol="R_100", granularity=60,
        client=CountingClient(), max_judgments=2))

    assert calls["n"] == 2
    assert report.judged_signals == 2
    assert report.total_signals == report.judged_signals + sum(
        1 for r in report.records if r.judgment is None)
    # Filtered stats only ever include judged records.
    assert all(stats.trades <= report.judged_signals for stats in report.filtered.values())
