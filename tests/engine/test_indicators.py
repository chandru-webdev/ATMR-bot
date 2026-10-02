"""
Indicator unit tests against hand-calculated MT5-style values.

Reference method (same as MetaTrader 5):
- EMA: first value is SMA of ``period`` closes, then alpha = 2/(period+1).
- RSI / ATR: Wilder smoothing; first average is SMA of the first ``period``
  gains/losses or true ranges (needs ``period + 1`` bars).
- Bollinger: SMA middle, population standard deviation (divide by N, ddof=0).

Numbers below were computed with that recurrence on the static series, not by
calling the production functions.
"""

from __future__ import annotations

import pandas as pd
import pytest

from atmr.engine.indicators import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_BB_PERIOD,
    DEFAULT_EMA_PERIOD,
    DEFAULT_RSI_PERIOD,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_rsi,
)

TOLERANCE = 1e-9


def _ohlc_from_close(closes: list[float]) -> pd.DataFrame:
    """Build a dummy OHLC frame where high=low=open=close except ATR tests."""
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
        }
    )


def test_ema_sma_seed_then_recurrence() -> None:
    # closes 10,11,12,13,14; period 3
    # SMA = (10+11+12)/3 = 11 at index 2
    # alpha = 2/4 = 0.5
    # EMA3 = 0.5*13 + 0.5*11 = 12
    # EMA4 = 0.5*14 + 0.5*12 = 13
    data = _ohlc_from_close([10.0, 11.0, 12.0, 13.0, 14.0])
    ema = calculate_ema(data, period=3)
    assert pd.isna(ema.iloc[0]) and pd.isna(ema.iloc[1])
    assert ema.iloc[2] == pytest.approx(11.0, abs=TOLERANCE)
    assert ema.iloc[3] == pytest.approx(12.0, abs=TOLERANCE)
    assert ema.iloc[4] == pytest.approx(13.0, abs=TOLERANCE)


def test_rsi_wilder_smoothing() -> None:
    # closes 10,12,11,13,12,14; period 3
    # changes: +2, -1, +2, -1, +2
    # first avg gain = 4/3, avg loss = 1/3 -> RSI = 80 at index 3
    # next: ag=8/9, al=5/9 -> RSI = 61.53846153846154 at index 4
    # next: ag=1.2592592592592593, al=0.3703703703703703 -> RSI = 77.27272727272728
    data = _ohlc_from_close([10.0, 12.0, 11.0, 13.0, 12.0, 14.0])
    rsi = calculate_rsi(data, period=3)
    assert pd.isna(rsi.iloc[2])
    assert rsi.iloc[3] == pytest.approx(80.0, abs=TOLERANCE)
    assert rsi.iloc[4] == pytest.approx(61.53846153846154, abs=1e-10)
    assert rsi.iloc[5] == pytest.approx(77.27272727272728, abs=1e-10)


def test_bollinger_population_stdev() -> None:
    # window [10,12,11]: mean 11, pop std = sqrt(((10-11)^2+(12-11)^2+(11-11)^2)/3)
    # = sqrt(2/3); bands = 11 +/- 2*sqrt(2/3)
    data = _ohlc_from_close([10.0, 12.0, 11.0, 13.0, 12.0])
    bands = calculate_bollinger_bands(data, period=3, stddev=2.0)
    sqrt_two_thirds = (2.0 / 3.0) ** 0.5
    assert bands["middle"].iloc[2] == pytest.approx(11.0, abs=TOLERANCE)
    assert bands["upper"].iloc[2] == pytest.approx(11.0 + 2.0 * sqrt_two_thirds, abs=TOLERANCE)
    assert bands["lower"].iloc[2] == pytest.approx(11.0 - 2.0 * sqrt_two_thirds, abs=TOLERANCE)


def test_atr_wilder_smoothing() -> None:
    # TR from previous close (first bar skipped): 3, 3, 5, 3
    # first ATR at index 3 = (3+3+5)/3 = 11/3
    # next = (11/3 * 2 + 3)/3 = 31/9
    data = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0, 11.0, 14.0],
            "high": [12.0, 13.0, 14.0, 15.0, 16.0],
            "low": [9.0, 10.0, 11.0, 10.0, 13.0],
            "close": [11.0, 12.0, 11.0, 14.0, 15.0],
        }
    )
    atr = calculate_atr(data, period=3)
    assert pd.isna(atr.iloc[2])
    assert atr.iloc[3] == pytest.approx(11.0 / 3.0, abs=TOLERANCE)
    assert atr.iloc[4] == pytest.approx(31.0 / 9.0, abs=TOLERANCE)


def test_ema_raises_without_enough_rows() -> None:
    data = _ohlc_from_close([1.0, 2.0])
    with pytest.raises(ValueError, match="at least 3"):
        calculate_ema(data, period=3)


def test_rsi_raises_without_period_plus_one_rows() -> None:
    data = _ohlc_from_close([1.0] * DEFAULT_RSI_PERIOD)
    with pytest.raises(ValueError, match="at least 15"):
        calculate_rsi(data, period=DEFAULT_RSI_PERIOD)


def test_atr_raises_without_period_plus_one_rows() -> None:
    data = _ohlc_from_close([1.0] * DEFAULT_ATR_PERIOD)
    with pytest.raises(ValueError, match="at least 15"):
        calculate_atr(data, period=DEFAULT_ATR_PERIOD)


def test_bollinger_raises_without_enough_rows() -> None:
    data = _ohlc_from_close([1.0] * (DEFAULT_BB_PERIOD - 1))
    with pytest.raises(ValueError, match="at least 20"):
        calculate_bollinger_bands(data, period=DEFAULT_BB_PERIOD)


def test_missing_ohlc_columns() -> None:
    data = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    with pytest.raises(ValueError, match="missing OHLC"):
        calculate_ema(data, period=3)


def test_default_ema_period_constant_matches_config() -> None:
    assert DEFAULT_EMA_PERIOD == 200
    assert DEFAULT_ATR_PERIOD == 14
