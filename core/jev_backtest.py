"""Jev validation engine: measures whether Jev judgments improve a strategy.

Mode C — historical replay. Replays candles walk-forward exactly like
core/backtest.py (one position at a time, SL/TP on candle high/low). At each
strategy signal it builds the market state visible at that moment, optionally
asks Jev, then resolves the trade outcome. Judgments are stored raw so weights
and thresholds can be re-tuned offline without re-querying the model.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.jev_layer import DEFAULT_WEIGHTS, SCORE_DIMENSIONS, JevJudgment, build_market_state, judge_market
from core.logger import get_logger
from core.strategy import SignalType, TradingLabStrategy

logger = get_logger("jev-backtest")


class ExitReason:
    SL = "SL"
    TP = "TP"
    EOD = "EOD"  # end of data, force-closed at last close


@dataclass
class JevTradeRecord:
    """One decision point: the signal, its outcome, and the judgment (if any)."""
    trade_id: int
    epoch: int
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    risk_reward: float
    exit_reason: str
    profit_pips: float
    r_reward: float
    judgment: Optional[JevJudgment] = None

    @property
    def is_win(self) -> bool:
        return self.profit_pips > 0

    def to_dict(self) -> dict:
        data = {
            "trade_id": self.trade_id,
            "epoch": self.epoch,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "risk_reward": self.risk_reward,
            "exit_reason": self.exit_reason,
            "profit_pips": self.profit_pips,
            "r_reward": self.r_reward,
        }
        if self.judgment is not None:
            data["jev"] = self.judgment.to_dict()
        return data


@dataclass
class StrategyStats:
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    total_r: float = 0.0
    profit_factor_r: float = 0.0


@dataclass
class JevBacktestReport:
    symbol: str = ""
    granularity: int = 0
    total_signals: int = 0
    judged_signals: int = 0
    judgment_errors: int = 0
    dry_run: bool = False
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    baseline: StrategyStats = field(default_factory=StrategyStats)
    filtered: dict[float, StrategyStats] = field(default_factory=dict)
    calibration: list[dict] = field(default_factory=list)
    dimension_diagnostics: dict[str, dict] = field(default_factory=dict)
    records: list[JevTradeRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "granularity": self.granularity,
            "total_signals": self.total_signals,
            "judged_signals": self.judged_signals,
            "judgment_errors": self.judgment_errors,
            "dry_run": self.dry_run,
            "weights": self.weights,
            "baseline": vars(self.baseline),
            "filtered": {str(k): vars(v) for k, v in self.filtered.items()},
            "calibration": self.calibration,
            "dimension_diagnostics": self.dimension_diagnostics,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "records": [r.to_dict() for r in self.records],
        }


def _simulate_exit(direction: str, entry_price: float, sl_price: float, tp_price: float,
                   candles: list[dict], start_idx: int) -> tuple[str, float, float, int]:
    """Resolve a trade opened at candles[start_idx].close, engine semantics.

    Checks SL before TP on each candle (same order as core/backtest.py), starting
    with the signal candle itself. Force-closes at the last close on end of data.

    Returns:
        (exit_reason, profit_pips, r_reward, exit_idx)
    """
    sl_distance = abs(entry_price - sl_price)
    for i in range(start_idx, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        close = float(candles[i]["close"])

        exit_reason = None
        exit_price = close
        if direction == SignalType.LONG.value:
            if low <= sl_price:
                exit_reason, exit_price = ExitReason.SL, sl_price
            elif high >= tp_price:
                exit_reason, exit_price = ExitReason.TP, tp_price
        else:
            if high >= sl_price:
                exit_reason, exit_price = ExitReason.SL, sl_price
            elif low <= tp_price:
                exit_reason, exit_price = ExitReason.TP, tp_price

        if exit_reason is None and i < len(candles) - 1:
            continue

        if exit_reason is None:
            exit_reason = ExitReason.EOD

        profit = (exit_price - entry_price) if direction == SignalType.LONG.value else (entry_price - exit_price)
        r_reward = profit / sl_distance if sl_distance > 0 else 0.0
        return exit_reason, round(profit, 6), round(r_reward, 3), i

    raise RuntimeError("unreachable: exit loop always returns")


def _stats(records: list[JevTradeRecord]) -> StrategyStats:
    stats = StrategyStats(trades=len(records))
    if not records:
        return stats
    stats.wins = sum(1 for r in records if r.is_win)
    stats.win_rate = stats.wins / stats.trades * 100.0
    stats.total_r = sum(r.r_reward for r in records)
    stats.expectancy_r = stats.total_r / stats.trades
    gross_win = sum(r.r_reward for r in records if r.r_reward > 0)
    gross_loss = abs(sum(r.r_reward for r in records if r.r_reward < 0))
    stats.profit_factor_r = gross_win / gross_loss if gross_loss > 0 else float("inf")
    return stats


def _calibration_table(records: list[JevTradeRecord], bins: int = 5) -> list[dict]:
    """Observed win rate per composite-score bucket (judged records only)."""
    judged = [r for r in records if r.judgment is not None]
    width = 1.0 / bins
    table = []
    for b in range(bins):
        low, high = b * width, (b + 1) * width
        bucket = [r for r in judged if (low <= r.judgment.composite < high) or (b == bins - 1 and r.judgment.composite == 1.0)]
        row = {
            "composite_range": f"{low:.1f}-{high:.1f}",
            "trades": len(bucket),
        }
        if bucket:
            wins = sum(1 for r in bucket if r.is_win)
            row["win_rate"] = round(wins / len(bucket) * 100.0, 1)
            row["avg_composite"] = round(sum(r.judgment.composite for r in bucket) / len(bucket), 3)
        else:
            row["win_rate"] = None
            row["avg_composite"] = None
        table.append(row)
    return table


def _dimension_diagnostics(records: list[JevTradeRecord]) -> dict[str, dict]:
    """Mean score per dimension for wins vs losses (judged records only)."""
    judged = [r for r in records if r.judgment is not None]
    diagnostics = {}
    for dim in SCORE_DIMENSIONS:
        wins = [r.judgment.scores[dim] for r in judged if r.is_win]
        losses = [r.judgment.scores[dim] for r in judged if not r.is_win]
        diagnostics[dim] = {
            "win_mean": round(sum(wins) / len(wins), 3) if wins else None,
            "loss_mean": round(sum(losses) / len(losses), 3) if losses else None,
        }
        if wins and losses:
            diagnostics[dim]["separation"] = round(diagnostics[dim]["win_mean"] - diagnostics[dim]["loss_mean"], 3)
        else:
            diagnostics[dim]["separation"] = None
    return diagnostics


def _fmt_stats(label: str, stats: StrategyStats) -> str:
    pf = f"{stats.profit_factor_r:.2f}" if stats.profit_factor_r != float("inf") else "inf"
    return (f"  {label:<24} trades={stats.trades:<5} win%={stats.win_rate:<6.1f} "
            f"expectancy={stats.expectancy_r:+.3f}R total={stats.total_r:+.2f}R PF={pf}")


def format_report(report: JevBacktestReport) -> str:
    """Human-readable summary of a Jev backtest."""
    lines = [
        f"=== Jev Backtest: {report.symbol} ({report.granularity}s) ===",
        f"signals={report.total_signals} judged={report.judged_signals} "
        f"errors={report.judgment_errors} dry_run={report.dry_run}",
        "",
        "Baseline (strategy alone):",
        _fmt_stats("all signals", report.baseline),
    ]
    if report.judged_signals:
        lines.append("")
        lines.append("Jev-filtered (composite >= threshold):")
        for threshold in sorted(report.filtered):
            stats = report.filtered[threshold]
            lines.append(_fmt_stats(f"composite >= {threshold:.2f}", stats))
        lines.append("")
        lines.append("Calibration (observed win rate per composite bucket):")
        for row in report.calibration:
            wr = f"{row['win_rate']:.1f}%" if row["win_rate"] is not None else "-"
            lines.append(f"  composite {row['composite_range']}: {row['trades']:>4} trades, win_rate={wr}")
        lines.append("")
        lines.append("Dimension diagnostics (mean score, wins vs losses):")
        for dim, diag in report.dimension_diagnostics.items():
            win_mean = f"{diag['win_mean']:.3f}" if diag["win_mean"] is not None else "-"
            loss_mean = f"{diag['loss_mean']:.3f}" if diag["loss_mean"] is not None else "-"
            sep = f"{diag['separation']:+.3f}" if diag["separation"] is not None else "-"
            lines.append(f"  {dim:<16} win={win_mean} loss={loss_mean} separation={sep}")
        lines.append("")
        lines.append(f"Tokens: input={report.input_tokens} output={report.output_tokens}")
    return "\n".join(lines)


def save_report(report: JevBacktestReport, path: str | Path) -> Path:
    """Persist the full report (records + raw judgments) as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2))
    logger.info(f"Report saved to {path}")
    return path


async def run_jev_backtest(
    candles: list[dict],
    strategy: TradingLabStrategy,
    symbol: str = "",
    granularity: int = 60,
    client=None,
    weights: Optional[dict[str, float]] = None,
    thresholds: tuple[float, ...] = (0.5, 0.6, 0.7),
    max_judgments: Optional[int] = None,
    progress_every: int = 25,
) -> JevBacktestReport:
    """Replay history, judge each signal, and measure lift.

    Args:
        candles: OHLC dicts oldest first.
        strategy: TradingLabStrategy instance (same parameters as live).
        symbol: symbol name for the report and Jev state.
        granularity: candle size in seconds.
        client: AsyncTypeSafeClient for live Jev calls; None for dry-run
            (decision points and outcomes only, no API calls).
        weights: composite weights override.
        thresholds: composite thresholds for the lift table.
        max_judgments: stop calling Jev after this many signals (cost guardrail);
            remaining signals are still recorded with their outcomes.
        progress_every: log progress every N signals.

    Raises:
        ValueError: when there are not enough candles for the strategy.
    """
    min_required = strategy.ema_period + 1
    if len(candles) < min_required:
        raise ValueError(f"Need at least {min_required} candles, got {len(candles)}")

    weights = weights or dict(DEFAULT_WEIGHTS)
    report = JevBacktestReport(symbol=symbol, granularity=granularity, weights=weights, dry_run=client is None)

    warmup = strategy.ema_period + 1
    i = warmup
    trade_id = 0

    while i < len(candles):
        window = candles[max(0, i - 250):i + 1]
        signal = strategy.evaluate(window)
        if signal is None:
            i += 1
            continue

        trade_id += 1
        report.total_signals += 1
        direction = signal.signal_type.value
        reason = "bullish_engulfing" if direction == SignalType.LONG.value else "bearish_engulfing"

        state = build_market_state(window, signal, symbol=symbol, granularity=granularity, signal_reason=reason)

        judgment = None
        budget_left = max_judgments is None or report.judged_signals + report.judgment_errors < max_judgments
        if client is not None and budget_left:
            try:
                judgment = await judge_market(client, state, weights)
                report.judged_signals += 1
                report.input_tokens += judgment.input_tokens
                report.output_tokens += judgment.output_tokens
            except Exception as e:
                report.judgment_errors += 1
                logger.warning(f"Jev call failed for signal #{trade_id}: {e}")

        exit_reason, profit, r_reward, exit_idx = _simulate_exit(
            direction, signal.entry_price, signal.sl_price, signal.tp_price, candles, i)

        report.records.append(JevTradeRecord(
            trade_id=trade_id,
            epoch=int(candles[i]["epoch"]),
            direction=direction,
            entry_price=signal.entry_price,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price,
            risk_reward=round(abs(signal.tp_price - signal.entry_price) / signal.sl_distance, 2) if signal.sl_distance > 0 else 0.0,
            exit_reason=exit_reason,
            profit_pips=profit,
            r_reward=r_reward,
            judgment=judgment,
        ))

        if report.total_signals % progress_every == 0:
            logger.info(f"Progress: {report.total_signals} signals, {report.judged_signals} judged")

        # Resume the walk-forward after the trade closes (engine semantics).
        i = exit_idx + 1

    report.baseline = _stats(report.records)
    for threshold in thresholds:
        kept = [r for r in report.records if r.judgment is not None and r.judgment.composite >= threshold]
        report.filtered[threshold] = _stats(kept)
    report.calibration = _calibration_table(report.records)
    report.dimension_diagnostics = _dimension_diagnostics(report.records)

    logger.info(f"Replay done: {report.total_signals} signals, {report.judged_signals} judged, "
                f"{report.judgment_errors} errors")
    return report
