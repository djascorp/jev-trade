"""Backtest engine: replays historical candles through the strategy with SL/TP simulation."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.indicators import compute_indicators
from core.logger import get_logger
from core.strategy import TradingLabStrategy, TradeSignal, SignalType

logger = get_logger("backtest")


class ExitReason:
    SL = "SL"
    TP = "TP"
    EOD = "EOD"  # end of data


@dataclass
class BacktestTrade:
    """Record of a single backtest trade."""
    trade_id: int
    direction: str  # "LONG" or "SHORT"
    entry_time: int
    entry_price: float
    sl_price: float
    tp_price: float
    exit_time: int
    exit_price: float
    exit_reason: str
    profit_pips: float
    r_reward: float  # actual R:R achieved


@dataclass
class BacktestResult:
    """Complete backtest results."""
    symbol: str = ""
    granularity: int = 0
    start_time: str = ""
    end_time: str = ""
    total_candles: int = 0
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
    trades: list = field(default_factory=list)


def run_backtest(
    candles: list[dict],
    strategy: TradingLabStrategy,
    symbol: str = "",
    granularity: int = 60,
) -> BacktestResult:
    """Run a backtest on historical candle data.

    Replays candles one by one, evaluating the strategy at each candle close.
    When a signal fires, opens a simulated position and checks subsequent
    candles' high/low for SL/TP hits. Only one position at a time, matching
    live bot behavior.

    Args:
        candles: List of OHLC dicts (epoch, open, high, low, close), oldest first.
        strategy: TradingLabStrategy instance with desired parameters.
        symbol: Symbol name for the report.
        granularity: Candle granularity in seconds for the report.

    Returns:
        BacktestResult with full statistics and trade list.
    """
    min_required = strategy.ema_period + 1
    if len(candles) < min_required:
        raise ValueError(f"Need at least {min_required} candles, got {len(candles)}")

    result = BacktestResult(
        symbol=symbol,
        granularity=granularity,
        start_time=_fmt_epoch(candles[0]["epoch"]),
        end_time=_fmt_epoch(candles[-1]["epoch"]),
        total_candles=len(candles),
    )

    warmup = strategy.ema_period + 1  # need EMA period + 1 for engulfing comparison
    position: Optional[dict] = None  # {signal, trade_id, ...}
    trade_id = 0
    equity_curve: list[float] = [0.0]
    consecutive_wins = 0
    consecutive_losses = 0

    for i in range(warmup, len(candles)):
        candle = candles[i]
        window = candles[max(0, i - 250):i + 1]

        # --- If no position, evaluate strategy ---
        if position is None:
            signal = strategy.evaluate(window)
            if signal is not None:
                trade_id += 1
                position = {
                    "signal": signal,
                    "trade_id": trade_id,
                    "entry_idx": i,
                    "entry_time": candle["epoch"],
                }

        # --- If position open, check SL/TP against current candle ---
        if position is not None:
            sig = position["signal"]
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
            exit_reason = None
            exit_price = close

            if sig.signal_type == SignalType.LONG:
                if low <= sig.sl_price:
                    exit_reason = ExitReason.SL
                    exit_price = sig.sl_price
                elif high >= sig.tp_price:
                    exit_reason = ExitReason.TP
                    exit_price = sig.tp_price

            elif sig.signal_type == SignalType.SHORT:
                if high >= sig.sl_price:
                    exit_reason = ExitReason.SL
                    exit_price = sig.sl_price
                elif low <= sig.tp_price:
                    exit_reason = ExitReason.TP
                    exit_price = sig.tp_price

            if exit_reason is not None:
                # Compute profit
                if sig.signal_type == SignalType.LONG:
                    profit = exit_price - sig.entry_price
                else:
                    profit = sig.entry_price - exit_price

                r_reward = abs(profit / sig.sl_distance) if sig.sl_distance > 0 else 0

                trade = BacktestTrade(
                    trade_id=position["trade_id"],
                    direction=sig.signal_type.value,
                    entry_time=position["entry_time"],
                    entry_price=sig.entry_price,
                    sl_price=sig.sl_price,
                    tp_price=sig.tp_price,
                    exit_time=candle["epoch"],
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    profit_pips=round(profit, 6),
                    r_reward=round(r_reward, 2),
                )
                result.trades.append(trade)

                # Update equity curve
                equity_curve.append(equity_curve[-1] + profit)

                # Update stats
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

                position = None

        # If position still open at end of data, force-close at last close
        if position is not None and i == len(candles) - 1:
            sig = position["signal"]
            close = float(candle["close"])
            if sig.signal_type == SignalType.LONG:
                profit = close - sig.entry_price
            else:
                profit = sig.entry_price - close
            r_reward = abs(profit / sig.sl_distance) if sig.sl_distance > 0 else 0

            trade = BacktestTrade(
                trade_id=position["trade_id"],
                direction=sig.signal_type.value,
                entry_time=position["entry_time"],
                entry_price=sig.entry_price,
                sl_price=sig.sl_price,
                tp_price=sig.tp_price,
                exit_time=candle["epoch"],
                exit_price=close,
                exit_reason=ExitReason.EOD,
                profit_pips=round(profit, 6),
                r_reward=round(r_reward, 2),
            )
            result.trades.append(trade)
            result.losses += 1
            result.avg_loss_pips += profit
            result.max_loss_pips = min(result.max_loss_pips, profit)
            equity_curve.append(equity_curve[-1] + profit)
            position = None

    # --- Compute summary statistics ---
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

        # Max drawdown from equity curve
        peak = equity_curve[0]
        max_dd = 0.0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pips = max_dd

    return result


def _fmt_epoch(epoch) -> str:
    try:
        return datetime.utcfromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return str(epoch)
