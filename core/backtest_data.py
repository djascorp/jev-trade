"""Fetch and cache historical candle data for backtesting."""

import asyncio
import csv
from datetime import datetime
from pathlib import Path

from core.deriv_api import DerivAPI
from core.logger import get_logger

logger = get_logger("backtest-data")

DATA_DIR = Path(__file__).parent.parent / "backtest_data"


def _cache_path(symbol: str, granularity: int) -> Path:
    return DATA_DIR / f"{symbol}_{granularity}s.csv"


async def fetch_candles(symbol: str, granularity: int, count: int,
                        use_cache: bool = True) -> list[dict]:
    """Fetch historical OHLC candles from Deriv API.

    Downloads candles in batches (max ~5000 per request) and optionally
    caches them to CSV for offline replay.

    Args:
        symbol: Deriv symbol (e.g. "R_100").
        granularity: Candle size in seconds (60, 300, 900...).
        count: Total number of candles to fetch.
        use_cache: If True, load from CSV cache when available.

    Returns:
        List of candle dicts with keys: epoch, open, high, low, close.
    """
    cache_file = _cache_path(symbol, granularity)

    if use_cache and cache_file.exists():
        logger.info(f"Loading cached candles from {cache_file}")
        candles = _load_csv(cache_file)
        if len(candles) >= count:
            logger.info(f"Cache hit: {len(candles)} candles (requested {count})")
            return candles[:count]
        logger.info(f"Cache too small ({len(candles)} < {count}), fetching more...")

    # Fetch from Deriv API
    candles = await _download_candles(symbol, granularity, count)

    # Save to cache
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _save_csv(cache_file, candles)
    logger.info(f"Saved {len(candles)} candles to {cache_file}")

    return candles


async def _download_candles(symbol: str, granularity: int, count: int) -> list[dict]:
    """Download candles from Deriv in batches."""
    from config import DERIV_WS_URL, DERIV_APP_ID
    ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    api = DerivAPI(ws_url, "")
    all_candles: list[dict] = []

    batch_size = 5000
    remaining = count
    end_epoch = "latest"

    try:
        await api.connect()

        while remaining > 0:
            batch = min(batch_size, remaining)
            logger.info(f"Fetching {batch} candles (remaining: {remaining})...")

            response = await api.send({
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": batch,
                "end": end_epoch,
                "granularity": granularity,
                "style": "candles",
            })

            if "error" in response:
                raise RuntimeError(f"API error: {response['error']['message']}")

            candles = response.get("candles", [])
            if not candles:
                logger.warning("No more candles available from API")
                break

            all_candles.extend(candles)
            remaining -= len(candles)

            # Next batch ends just before the oldest candle of this batch,
            # regardless of the order the API returned.
            end_epoch = min(int(c["epoch"]) for c in candles) - 1

            if len(candles) < batch:
                logger.info("Reached start of available data")
                break

    finally:
        await api.disconnect()

    candles = _sorted_oldest_first(all_candles)
    logger.info(f"Downloaded {len(candles)} candles total")
    return candles


def _sorted_oldest_first(candles: list[dict]) -> list[dict]:
    """Sort ascending by epoch and drop duplicate epochs (pagination overlap)."""
    deduped: list[dict] = []
    for c in sorted(candles, key=lambda c: int(c["epoch"])):
        if not deduped or int(c["epoch"]) > int(deduped[-1]["epoch"]):
            deduped.append(c)
    return deduped


def _load_csv(path: Path) -> list[dict]:
    candles = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append({
                "epoch": int(row["epoch"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    # Normalize ordering: caches written by older versions are newest-first.
    return _sorted_oldest_first(candles)


def _save_csv(path: Path, candles: list[dict]):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["epoch", "open", "high", "low", "close"])
        writer.writeheader()
        for c in candles:
            writer.writerow({
                "epoch": c["epoch"],
                "open": c.get("open", 0),
                "high": c.get("high", 0),
                "low": c.get("low", 0),
                "close": c.get("close", 0),
            })
