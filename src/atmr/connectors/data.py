"""Data acquisition and cleaning (DataIngestor).

Implements ARCHITECTURE.md Section 2 and AGENTS.md Ingestor Agent:
- Fetches OHLCV bars from MT5 via the MT5Connector client
- Cleans data, normalizes timestamps to UTC DatetimeIndex, and drops duplicates
- Drops the still-forming candle (bar 0) for closed-candle signal processing
- Detects new closed candles on time-based polling cycles

NOTE: Per AGENTS.md, this module NEVER imports MetaTrader5 directly.
All terminal interactions are decoupled through the client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from atmr.exceptions import ConnectivityError

_REQUIRED_OHLC = ("open", "high", "low", "close")


class RateProvider(Protocol):
    """Protocol for MT5 data provider decoupled from MetaTrader5 package."""

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Any:
        """Fetch raw candle rates for a symbol and timeframe."""
        ...


class DataIngestor:
    """The Senses: acquires, cleans, and manages candle data."""

    def __init__(self, client: Any = None) -> None:
        """
        Initialize the DataIngestor.

        Args:
            client: Optional MT5Connector client providing get_rates().
        """
        self._client = client
        self._last_closed_times: dict[tuple[str, str], datetime] = {}

    def clean_rates(self, raw_rates: Any) -> pd.DataFrame:
        """
        Convert raw MT5 rates to a clean pandas DataFrame with UTC DatetimeIndex.

        Args:
            raw_rates: Numpy structured array, list of dicts, or DataFrame.

        Returns:
            pd.DataFrame: Cleaned DataFrame with columns open, high, low, close, volume.

        Raises:
            ValueError: If raw_rates is empty or missing required OHLC columns.
        """
        if raw_rates is None or len(raw_rates) == 0:
            raise ValueError("Rates data cannot be empty")

        if isinstance(raw_rates, pd.DataFrame):
            df = raw_rates.copy()
        else:
            df = pd.DataFrame(raw_rates)
        df.columns = [col.lower() for col in df.columns]

        if not all(col in df.columns for col in _REQUIRED_OHLC):
            raise ValueError("Missing required OHLC columns in rates data")

        # Normalize volume column name
        if "tick_volume" in df.columns and "volume" not in df.columns:
            df["volume"] = df["tick_volume"]
        elif "volume" not in df.columns:
            df["volume"] = 0.0

        # Convert timestamp to UTC DatetimeIndex
        if "time" in df.columns:
            time_col = df["time"]
            if pd.api.types.is_numeric_dtype(time_col):
                df.index = pd.to_datetime(time_col, unit="s", utc=True)
            else:
                df.index = pd.to_datetime(time_col, utc=True)
        elif not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("Rates data must have a 'time' column or DatetimeIndex")
        elif df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        # Select standard columns, drop duplicates, and sort
        standard_cols = ["open", "high", "low", "close", "volume"]
        cleaned = df[standard_cols].astype("float64")
        cleaned = cleaned[~cleaned.index.duplicated(keep="last")]
        cleaned = cleaned.sort_index()

        return cleaned

    def drop_forming_candle(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Drop the last row representing the still-forming candle.

        Args:
            df: Cleaned OHLCV DataFrame.

        Returns:
            pd.DataFrame: DataFrame containing closed candles only.

        Raises:
            ValueError: If fewer than 2 bars are available.
        """
        if len(df) < 2:
            raise ValueError("Need at least 2 bars to drop forming candle")
        return df.iloc[:-1].copy()

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        count: int = 300,
        drop_forming: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles from the client and optionally drop the forming bar.

        Args:
            symbol: Tradable symbol (e.g. 'XAUUSD').
            timeframe: Primary timeframe string (e.g. 'H1').
            count: Number of closed bars requested.
            drop_forming: True to drop the latest still-forming bar.

        Returns:
            pd.DataFrame: Cleaned OHLCV DataFrame.

        Raises:
            ConnectivityError: If client is missing, disconnected, or returns no data.
        """
        if self._client is None:
            raise ConnectivityError("MT5 client is not configured on DataIngestor")

        fetch_count = count + 1 if drop_forming else count
        try:
            raw = self._client.get_rates(symbol, timeframe, fetch_count)
        except Exception as exc:
            msg = f"Failed to fetch rates for {symbol} {timeframe}: {exc}"
            raise ConnectivityError(msg) from exc

        if raw is None or len(raw) == 0:
            raise ConnectivityError(f"Failed to fetch rates for {symbol} {timeframe}: empty result")

        cleaned = self.clean_rates(raw)
        if drop_forming:
            cleaned = self.drop_forming_candle(cleaned)

        return cleaned.iloc[-count:]

    def has_new_closed_candle(self, symbol: str, timeframe: str) -> bool:
        """
        Check whether a new closed candle exists on the given timeframe.

        Args:
            symbol: Tradable symbol.
            timeframe: Timeframe string.

        Returns:
            bool: True if a new closed candle was detected since last check.
        """
        if self._client is None:
            return False

        try:
            # Fetch 2 bars: bar 0 is forming, bar 1 is the latest closed candle
            raw = self._client.get_rates(symbol, timeframe, 2)
        except Exception:
            return False

        if raw is None or len(raw) < 2:
            return False

        try:
            cleaned = self.clean_rates(raw)
            closed_bar_time = cleaned.index[-2].to_pydatetime()
        except Exception:
            return False

        key = (symbol, timeframe)
        last_time = self._last_closed_times.get(key)
        if last_time is None or closed_bar_time > last_time:
            self._last_closed_times[key] = closed_bar_time
            return True

        return False

    def get_last_closed_time(self, symbol: str, timeframe: str) -> datetime | None:
        """
        Get the UTC timestamp of the last detected closed candle.

        Args:
            symbol: Tradable symbol.
            timeframe: Timeframe string.

        Returns:
            datetime | None: UTC timestamp if recorded, else None.
        """
        return self._last_closed_times.get((symbol, timeframe))
