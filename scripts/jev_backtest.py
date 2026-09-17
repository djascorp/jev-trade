#!/usr/bin/env python3
"""Jev backtest CLI: measures whether Jev composite scoring improves a strategy.

Fetches historical candles (CSV cache or Deriv public data), replays them
walk-forward through the engulfing strategy, optionally asks Jev to judge each
signal, and reports baseline vs Jev-filtered statistics, calibration and
dimension diagnostics.

Examples:
    # Dry-run (no API key needed): decision points and outcomes only
    python scripts/jev_backtest.py --symbol R_100 --granularity 60 --count 5000 --dry-run

    # Live Jev validation, capped at 100 judgments for cost control
    python scripts/jev_backtest.py --symbol R_100 --granularity 60 --count 5000 --limit 100
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv

load_dotenv(PROJECT_DIR / ".env")

from core.backtest_data import fetch_candles
from core.jev_backtest import format_report, run_jev_backtest, save_report
from core.jev_layer import DEFAULT_WEIGHTS, has_api_key
from core.logger import get_logger
from core.strategy import TradingLabStrategy

logger = get_logger("jev-backtest-cli")

RESULTS_DIR = PROJECT_DIR / "experiments" / "results"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="R_100", help="Deriv symbol (default: R_100)")
    parser.add_argument("--granularity", type=int, default=60, help="Candle seconds: 60=M1, 300=M5 (default: 60)")
    parser.add_argument("--count", type=int, default=5000, help="Number of candles to replay (default: 5000)")
    parser.add_argument("--no-cache", action="store_true", help="Force re-download of candles")
    parser.add_argument("--dry-run", action="store_true", help="Skip Jev calls (no TYPESAFE_API_KEY needed)")
    parser.add_argument("--limit", type=int, default=None, help="Max Jev calls (cost guardrail)")
    parser.add_argument("--thresholds", type=float, nargs="+", default=[0.5, 0.6, 0.7],
                        help="Composite thresholds for the lift table (default: 0.5 0.6 0.7)")
    parser.add_argument("--weights", type=str, default=None,
                        help=f"Composite weights as JSON (default: {json.dumps(DEFAULT_WEIGHTS)})")
    parser.add_argument("--out", type=str, default=None,
                        help="Report JSON path (default: experiments/results/jev_<symbol>_<granularity>s.json)")
    return parser.parse_args()


def build_strategy() -> TradingLabStrategy:
    from config import RSI_THRESHOLD_LONG, RSI_THRESHOLD_SHORT
    return TradingLabStrategy(
        rsi_long_threshold=RSI_THRESHOLD_LONG,
        rsi_short_threshold=RSI_THRESHOLD_SHORT,
    )


async def main() -> int:
    args = parse_args()

    candles = await fetch_candles(args.symbol, args.granularity, args.count, use_cache=not args.no_cache)
    if len(candles) < args.count:
        logger.warning(f"Only {len(candles)} candles available (requested {args.count})")
    logger.info(f"Replaying {len(candles)} candles of {args.symbol} ({args.granularity}s)")

    client = None
    if not args.dry_run:
        if not has_api_key():
            print("Error: TYPESAFE_API_KEY is not set. Use --dry-run or configure .env")
            return 1
        from typesafe_sdk import AsyncTypeSafeClient
        client = AsyncTypeSafeClient()

    weights = json.loads(args.weights) if args.weights else None
    strategy = build_strategy()

    try:
        if client is not None:
            report = await run_jev_backtest(
                candles, strategy, symbol=args.symbol, granularity=args.granularity,
                client=client, weights=weights, thresholds=tuple(args.thresholds),
                max_judgments=args.limit,
            )
        else:
            report = await run_jev_backtest(
                candles, strategy, symbol=args.symbol, granularity=args.granularity,
                weights=weights, thresholds=tuple(args.thresholds),
            )
    finally:
        if client is not None:
            await client.aclose()

    print()
    print(format_report(report))

    out_path = args.out or RESULTS_DIR / f"jev_{args.symbol}_{args.granularity}s.json"
    save_report(report, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
