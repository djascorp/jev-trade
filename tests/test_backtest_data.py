"""Regression tests for candle ordering in core.backtest_data.

A bug (now fixed) stored cached CSVs newest-first, silently replaying
backtests backwards in time. These tests pin the chronological contract.
"""

from core.backtest_data import _load_csv, _sorted_oldest_first


def test_sorted_oldest_first_reverses_newest_first_input():
    candles = [
        {"epoch": 300, "open": 3, "high": 3, "low": 3, "close": 3},
        {"epoch": 100, "open": 1, "high": 1, "low": 1, "close": 1},
        {"epoch": 200, "open": 2, "high": 2, "low": 2, "close": 2},
    ]
    assert [c["epoch"] for c in _sorted_oldest_first(candles)] == [100, 200, 300]


def test_sorted_oldest_first_drops_duplicate_epochs():
    candles = [
        {"epoch": 100, "open": 1, "high": 1, "low": 1, "close": 1},
        {"epoch": 100, "open": 1, "high": 1, "low": 1, "close": 1},
        {"epoch": 200, "open": 2, "high": 2, "low": 2, "close": 2},
    ]
    assert [c["epoch"] for c in _sorted_oldest_first(candles)] == [100, 200]


def test_load_csv_normalizes_newest_first_cache(tmp_path):
    path = tmp_path / "SYM_60s.csv"
    path.write_text(
        "epoch,open,high,low,close\n"
        "300,3.0,3.1,2.9,3.0\n"
        "100,1.0,1.1,0.9,1.0\n"
        "200,2.0,2.1,1.9,2.0\n"
    )
    candles = _load_csv(path)
    assert [c["epoch"] for c in candles] == [100, 200, 300]
    assert candles[-1]["close"] == 3.0
