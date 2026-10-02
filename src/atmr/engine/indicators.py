"""
OHLC indicator math used by the strategy engine.

Functions are pure: DataFrame in, Series/dict out. No MT5, network, files, or clock.
Smoothing matches MetaTrader 5 (SMA seed, then Wilder / EMA recurrence).
"""

from __future__ import annotations

from typing import TypedDict

import pandas as pd

# Defaults match config/config.yaml; callers should still pass config values.
DEFAULT_EMA_PERIOD = 200
DEFAULT_RSI_PERIOD = 14
DEFAULT_BB_PERIOD = 20
DEFAULT_BB_STDDEV = 2.0
DEFAULT_ATR_PERIOD = 14

_OHLC_COLUMNS = ("open", "high", "low", "close")


class BollingerBands(TypedDict):
    """Middle SMA plus upper/lower bands."""

    upper: pd.Series
    middle: pd.Series
    lower: pd.Series


def calculate_ema(
    data: pd.DataFrame,
    period: int = DEFAULT_EMA_PERIOD,
) -> pd.Series:
    """
    Calculate Exponential Moving Average of close, MT5 SMA-seeded.

    Args:
        data: OHLC DataFrame with a ``close`` column.
        period: Lookback. Default 200 from config.yaml.

    Returns:
        pd.Series: EMA aligned to ``data.index``. Leading values before the
        first full window are NaN.

    Raises:
        ValueError: If ``period`` is not a positive int or ``data`` is too short.
    """
    close = _close_series(data, period)
    alpha = 2.0 / (period + 1)
    ema = pd.Series(index=close.index, dtype="float64")
    ema.iloc[period - 1] = close.iloc[:period].mean()
    for i in range(period, len(close)):
        prev = ema.iloc[i - 1]
        ema.iloc[i] = alpha * close.iloc[i] + (1.0 - alpha) * prev
    ema.name = "ema"
    return ema


def calculate_rsi(
    data: pd.DataFrame,
    period: int = DEFAULT_RSI_PERIOD,
) -> pd.Series:
    """
    Calculate RSI of close with Wilder smoothing (MT5 ``iRSI``).

    Args:
        data: OHLC DataFrame with a ``close`` column.
        period: Lookback. Default 14 from config.yaml. Needs ``period + 1`` rows.

    Returns:
        pd.Series: RSI 0-100 aligned to ``data.index``. Leading values are NaN.

    Raises:
        ValueError: If ``period`` is invalid or there are fewer than ``period + 1`` rows.
    """
    _validate_period(period)
    close = _require_ohlc(data, min_rows=period + 1)["close"].astype("float64")
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    rsi = pd.Series(index=close.index, dtype="float64")
    avg_gain = float(gains.iloc[1 : period + 1].mean())
    avg_loss = float(losses.iloc[1 : period + 1].mean())
    rsi.iloc[period] = _rsi_from_averages(avg_gain, avg_loss)
    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + float(gains.iloc[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses.iloc[i])) / period
        rsi.iloc[i] = _rsi_from_averages(avg_gain, avg_loss)
    rsi.name = "rsi"
    return rsi


def calculate_bollinger_bands(
    data: pd.DataFrame,
    period: int = DEFAULT_BB_PERIOD,
    stddev: float = DEFAULT_BB_STDDEV,
) -> BollingerBands:
    """
    Calculate Bollinger Bands on close (SMA middle, population stdev).

    Args:
        data: OHLC DataFrame with a ``close`` column.
        period: SMA / stdev lookback. Default 20 from config.yaml.
        stddev: Band width in standard deviations. Default 2.0 from config.yaml.

    Returns:
        BollingerBands: Mapping with ``upper``, ``middle``, and ``lower`` Series.

    Raises:
        ValueError: If ``period``/``stddev`` are invalid or ``data`` is too short.
    """
    close = _close_series(data, period)
    if stddev <= 0:
        raise ValueError("stddev must be > 0")
    middle = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + stddev * std
    lower = middle - stddev * std
    middle.name = "bb_middle"
    upper.name = "bb_upper"
    lower.name = "bb_lower"
    return {"upper": upper, "middle": middle, "lower": lower}


def calculate_atr(
    data: pd.DataFrame,
    period: int = DEFAULT_ATR_PERIOD,
) -> pd.Series:
    """
    Calculate Average True Range with Wilder smoothing (MT5 ``iATR``).

    Args:
        data: OHLC DataFrame with ``high``, ``low``, and ``close`` columns.
        period: Lookback. Default 14 from config.yaml. Needs ``period + 1`` rows.

    Returns:
        pd.Series: ATR aligned to ``data.index``. Leading values are NaN.

    Raises:
        ValueError: If ``period`` is invalid or there are fewer than ``period + 1`` rows.
    """
    _validate_period(period)
    frame = _require_ohlc(data, min_rows=period + 1)
    high = frame["high"].astype("float64")
    low = frame["low"].astype("float64")
    close = frame["close"].astype("float64")
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = pd.Series(index=close.index, dtype="float64")
    atr.iloc[period] = float(true_range.iloc[1 : period + 1].mean())
    for i in range(period + 1, len(close)):
        prev = float(atr.iloc[i - 1])
        atr.iloc[i] = (prev * (period - 1) + float(true_range.iloc[i])) / period
    atr.name = "atr"
    return atr


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    """Convert Wilder average gain/loss into RSI."""
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _close_series(data: pd.DataFrame, period: int) -> pd.Series:
    """Return the close column after period and length checks."""
    _validate_period(period)
    return _require_ohlc(data, min_rows=period)["close"].astype("float64")


def _validate_period(period: int) -> None:
    """Raise ValueError unless period is an int > 0."""
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("period must be an integer > 0")


def _require_ohlc(data: pd.DataFrame, min_rows: int) -> pd.DataFrame:
    """
    Ensure OHLC columns exist and the frame is long enough.

    Args:
        data: Candidate price frame.
        min_rows: Minimum row count required for a stable first value.

    Returns:
        pd.DataFrame: The same frame if it is valid.

    Raises:
        ValueError: Missing columns or not enough rows.
    """
    missing = [name for name in _OHLC_COLUMNS if name not in data.columns]
    if missing:
        raise ValueError(f"DataFrame missing OHLC columns: {', '.join(missing)}")
    if len(data) < min_rows:
        raise ValueError(f"Need at least {min_rows} rows for this indicator, got {len(data)}")
    return data
