"""Unit tests for DataIngestor.

Validates raw rate conversion, UTC normalization, duplicate handling,
forming candle removal, new candle detection, and connectivity error handling.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from atmr.connectors.data import DataIngestor
from atmr.exceptions import ConnectivityError

_T0 = int(datetime(2026, 10, 1, 12, 0, tzinfo=UTC).timestamp())


def _make_raw_rates(count: int = 5, start_epoch: int = _T0, step_s: int = 3600) -> np.ndarray:
    """Create a mock numpy structured array matching MT5 copy_rates_from_pos."""
    dtype = np.dtype(
        [
            ("time", "<i8"),
            ("open", "<f8"),
            ("high", "<f8"),
            ("low", "<f8"),
            ("close", "<f8"),
            ("tick_volume", "<i8"),
            ("spread", "<i4"),
            ("real_volume", "<i8"),
        ]
    )
    records = []
    for i in range(count):
        t = start_epoch + i * step_s
        records.append((t, 2650.0 + i, 2655.0 + i, 2645.0 + i, 2652.0 + i, 100 + i, 20, 0))
    return np.array(records, dtype=dtype)


def test_clean_rates_normalizes_to_utc_dataframe() -> None:
    """Test converting raw MT5 rates to DataFrame with UTC DatetimeIndex."""
    ingestor = DataIngestor()
    raw = _make_raw_rates(count=3)
    df = ingestor.clean_rates(raw)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert df["open"].iloc[0] == 2650.0
    assert df["close"].iloc[2] == 2654.0


def test_clean_rates_drops_duplicates_and_sorts() -> None:
    """Test that duplicate timestamps are deduplicated and sorted chronologically."""
    ingestor = DataIngestor()
    dtype = np.dtype(
        [
            ("time", "<i8"),
            ("open", "<f8"),
            ("high", "<f8"),
            ("low", "<f8"),
            ("close", "<f8"),
            ("tick_volume", "<i8"),
        ]
    )
    # Provide out of order with a duplicate
    records = [
        (_T0 + 3600, 101.0, 102.0, 100.0, 101.5, 50),
        (_T0, 100.0, 101.0, 99.0, 100.5, 40),
        (_T0, 100.0, 101.0, 99.0, 100.5, 40),  # Duplicate
    ]
    raw = np.array(records, dtype=dtype)
    df = ingestor.clean_rates(raw)

    assert len(df) == 2
    assert df.index[0] < df.index[1]


def test_drop_forming_candle() -> None:
    """Test dropping the still-forming candle (the last bar)."""
    ingestor = DataIngestor()
    raw = _make_raw_rates(count=5)
    df = ingestor.clean_rates(raw)

    closed = ingestor.drop_forming_candle(df)
    assert len(closed) == 4
    # The last bar in closed should be bar index 3 from raw
    assert closed.index[-1] == df.index[3]


def test_drop_forming_candle_insufficient_bars() -> None:
    """Test dropping forming candle raises ValueError if fewer than 2 bars exist."""
    ingestor = DataIngestor()
    raw = _make_raw_rates(count=1)
    df = ingestor.clean_rates(raw)

    with pytest.raises(ValueError, match="Need at least 2 bars to drop forming candle"):
        ingestor.drop_forming_candle(df)


def test_fetch_ohlcv_success() -> None:
    """Test fetching OHLCV with a client, dropping the forming candle."""
    client = MagicMock()
    raw = _make_raw_rates(count=4)
    client.get_rates.return_value = raw

    ingestor = DataIngestor(client)
    df = ingestor.fetch_ohlcv("XAUUSD", "H1", count=3, drop_forming=True)

    client.get_rates.assert_called_once_with("XAUUSD", "H1", 4)
    assert len(df) == 3


def test_fetch_ohlcv_include_forming() -> None:
    """Test fetching OHLCV including the forming bar when drop_forming=False."""
    client = MagicMock()
    raw = _make_raw_rates(count=4)
    client.get_rates.return_value = raw

    ingestor = DataIngestor(client)
    df = ingestor.fetch_ohlcv("XAUUSD", "H1", count=4, drop_forming=False)

    client.get_rates.assert_called_once_with("XAUUSD", "H1", 4)
    assert len(df) == 4


def test_fetch_ohlcv_connectivity_error() -> None:
    """Test that ConnectivityError is raised when MT5 returns None or empty."""
    client = MagicMock()
    client.get_rates.return_value = None

    ingestor = DataIngestor(client)
    with pytest.raises(ConnectivityError, match="Failed to fetch rates"):
        ingestor.fetch_ohlcv("XAUUSD", "H1", count=10)


def test_has_new_closed_candle_detection() -> None:
    """Test detecting when a new closed candle appears."""
    client = MagicMock()
    ingestor = DataIngestor(client)

    # Initial check: bar 0 is forming, bar 1 is closed
    raw1 = _make_raw_rates(count=2, start_epoch=_T0)
    client.get_rates.return_value = raw1

    # First poll detects a closed candle and records its timestamp
    assert ingestor.has_new_closed_candle("XAUUSD", "H1") is True

    # Same candle returns False on subsequent poll
    assert ingestor.has_new_closed_candle("XAUUSD", "H1") is False

    # New candle formed 1 hour later
    raw2 = _make_raw_rates(count=2, start_epoch=_T0 + 3600)
    client.get_rates.return_value = raw2

    assert ingestor.has_new_closed_candle("XAUUSD", "H1") is True
    assert ingestor.has_new_closed_candle("XAUUSD", "H1") is False


def test_clean_rates_invalid_input() -> None:
    """Test clean_rates raises ValueError for missing columns or empty data."""
    ingestor = DataIngestor()
    with pytest.raises(ValueError, match="Rates data cannot be empty"):
        ingestor.clean_rates([])

    df_missing = pd.DataFrame([{"time": 1000, "open": 100.0}])
    with pytest.raises(ValueError, match="Missing required OHLC columns"):
        ingestor.clean_rates(df_missing)
