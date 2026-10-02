"""
Entry and exit evaluation on closed candles.

Pure: no MT5, no network, no ``datetime.now()``. Indicators are computed on
every row except the last, which is treated as the still-forming bar.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from atmr.config import Config
from atmr.engine.indicators import (
    BollingerBands,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_rsi,
)
from atmr.engine.models import (
    Direction,
    ExitAction,
    ExitDecision,
    Position,
    PositionStage,
    Signal,
)

_PARTIAL_STAGES = frozenset({PositionStage.PARTIAL_CLOSED, PositionStage.TRAILING})


class StrategyEngine:
    """Trend-filter + mean-reversion entries and D1 staged exits."""

    def evaluate_entry(self, data: pd.DataFrame, config: Config) -> Signal:
        """
        Evaluate a long/short entry on the latest closed candle.

        Args:
            data: OHLC frame whose last row is the forming candle (ignored).
            config: Validated bot config (indicator and strategy sections).

        Returns:
            Signal: LONG, SHORT, or NONE with a reason. Timestamp comes from
            the closed candle index or ``time`` column.
        """
        symbol = config.canonical_symbols()[0]
        closed = _closed_candles(data)
        if closed is None:
            return _none(symbol, _fallback_time(data), "not enough closed candles")
        timestamp = _candle_time(closed)
        try:
            indicators = _entry_indicators(closed, config)
        except ValueError:
            return _none(symbol, timestamp, "not enough data for indicators")
        long_reason = _long_entry_blockers(closed, indicators, config)
        if long_reason is None:
            return Signal(symbol, Direction.LONG, timestamp, "long pullback in uptrend")
        short_reason = _short_entry_blockers(closed, indicators, config)
        if short_reason is None:
            return Signal(symbol, Direction.SHORT, timestamp, "short pullback in downtrend")
        return _none(symbol, timestamp, long_reason)

    def evaluate_exit(
        self,
        data: pd.DataFrame,
        position: Position,
        config: Config,
    ) -> ExitDecision:
        """
        Evaluate D1 exit precedence for one open position.

        Order: SL / time stop → +1R breakeven → +1.5R partial once →
        RSI 50-55 or middle-band full exit → ATR trail.

        Args:
            data: OHLC frame; the last row is forming and is ignored.
            position: Open trade including stage and candles_open.
            config: Exit and trailing parameters.

        Returns:
            ExitDecision: One action for this closed bar. 1.5R is never FULL_CLOSE.
        """
        time_stop = config.exits.time_stop_candles
        if position.candles_open >= time_stop:
            return _full_close(f"TIME_STOP_{time_stop}")
        closed = _closed_candles(data)
        if closed is None:
            return ExitDecision(ExitAction.NONE, "not enough closed candles")
        bar = closed.iloc[-1]
        if _stop_hit(position, bar):
            return _full_close("SL")
        r_multiple = _r_multiple(position, bar)
        staged = _staged_exit(position, r_multiple, config)
        if staged is not None:
            return staged
        try:
            rsi, bands, atr = _exit_indicators(closed, config)
        except ValueError:
            return ExitDecision(ExitAction.NONE, "not enough data for exit indicators")
        if _remainder_full_exit(bar, rsi.iloc[-1], bands, config):
            return _full_close(_remainder_reason(bar, rsi.iloc[-1], bands, config))
        return _trail_if_needed(position, bar, atr.iloc[-1], config)


def _entry_indicators(
    closed: pd.DataFrame,
    config: Config,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Return EMA, RSI, lower band, upper band on closed candles."""
    ind = config.indicators
    ema = calculate_ema(closed, ind.ema_period)
    rsi = calculate_rsi(closed, ind.rsi_period)
    bands = calculate_bollinger_bands(closed, ind.bb_period, ind.bb_stddev)
    return ema, rsi, bands["lower"], bands["upper"]


def _exit_indicators(
    closed: pd.DataFrame,
    config: Config,
) -> tuple[pd.Series, BollingerBands, pd.Series]:
    """Return RSI, Bollinger bands, and ATR on closed candles."""
    ind = config.indicators
    rsi = calculate_rsi(closed, ind.rsi_period)
    bands = calculate_bollinger_bands(closed, ind.bb_period, ind.bb_stddev)
    atr = calculate_atr(closed, ind.atr_period)
    return rsi, bands, atr


def _long_entry_blockers(
    closed: pd.DataFrame,
    indicators: tuple[pd.Series, pd.Series, pd.Series, pd.Series],
    config: Config,
) -> str | None:
    """Return the first failed long condition, or None if all four pass."""
    ema, rsi, lower, _upper = indicators
    last = closed.iloc[-1]
    if pd.isna(ema.iloc[-1]) or pd.isna(rsi.iloc[-1]) or pd.isna(lower.iloc[-1]):
        return "indicators not ready"
    if float(last["close"]) <= float(ema.iloc[-1]):
        return "close not above EMA"
    if float(rsi.iloc[-1]) >= config.strategy.rsi_oversold:
        return "RSI not oversold"
    if not _band_touched(closed, lower, config.strategy.touch_lookback, side="lower"):
        return "no lower-band touch in lookback"
    if float(last["close"]) <= float(last["open"]):
        return "confirmation candle not bullish"
    return None


def _short_entry_blockers(
    closed: pd.DataFrame,
    indicators: tuple[pd.Series, pd.Series, pd.Series, pd.Series],
    config: Config,
) -> str | None:
    """Return the first failed short condition, or None if all four pass."""
    ema, rsi, _lower, upper = indicators
    last = closed.iloc[-1]
    if pd.isna(ema.iloc[-1]) or pd.isna(rsi.iloc[-1]) or pd.isna(upper.iloc[-1]):
        return "indicators not ready"
    if float(last["close"]) >= float(ema.iloc[-1]):
        return "close not below EMA"
    if float(rsi.iloc[-1]) <= config.strategy.rsi_overbought:
        return "RSI not overbought"
    if not _band_touched(closed, upper, config.strategy.touch_lookback, side="upper"):
        return "no upper-band touch in lookback"
    if float(last["close"]) >= float(last["open"]):
        return "confirmation candle not bearish"
    return None


def _band_touched(
    closed: pd.DataFrame,
    band: pd.Series,
    lookback: int,
    side: str,
) -> bool:
    """True if a candle *before* confirmation touched or closed through the band."""
    if lookback < 1 or len(closed) < lookback + 1:
        return False
    window = closed.iloc[-(lookback + 1) : -1]
    for idx in window.index:
        if pd.isna(band.loc[idx]):
            continue
        row = closed.loc[idx]
        level = float(band.loc[idx])
        close = float(row["close"])
        if side == "lower" and (float(row["low"]) <= level or close <= level):
            return True
        if side == "upper" and (float(row["high"]) >= level or close >= level):
            return True
    return False


def _staged_exit(
    position: Position,
    r_multiple: float,
    config: Config,
) -> ExitDecision | None:
    """Apply +1R then +1.5R. Never full-close at 1.5R (D1)."""
    trailing = config.risk.trailing
    if position.stage is PositionStage.NONE and r_multiple >= trailing.breakeven_r:
        return ExitDecision(
            ExitAction.MOVE_SL_BREAKEVEN,
            "STAGE_1_BREAKEVEN",
            new_sl=position.entry_price,
        )
    if position.stage not in _PARTIAL_STAGES and r_multiple >= trailing.partial_close_r:
        fraction = trailing.partial_close_pct / 100.0
        return ExitDecision(
            ExitAction.PARTIAL_CLOSE,
            "STAGE_2_PARTIAL_1_5R",
            close_fraction=fraction,
        )
    return None


def _remainder_full_exit(
    bar: pd.Series,
    rsi: float,
    bands: BollingerBands,
    config: Config,
) -> bool:
    """True when RSI is in 50-55 or price touches the middle band."""
    if pd.isna(rsi):
        return False
    low, high = config.exits.tp_rsi_low, config.exits.tp_rsi_high
    if low <= float(rsi) <= high:
        return True
    middle = bands["middle"].iloc[-1]
    if pd.isna(middle):
        return False
    mid = float(middle)
    return float(bar["low"]) <= mid <= float(bar["high"])


def _remainder_reason(
    bar: pd.Series,
    rsi: float,
    bands: BollingerBands,
    config: Config,
) -> str:
    """Name the remainder full-exit trigger."""
    low, high = config.exits.tp_rsi_low, config.exits.tp_rsi_high
    if not pd.isna(rsi) and low <= float(rsi) <= high:
        return "TP_RSI"
    return "TP_MID_BB"


def _trail_if_needed(
    position: Position,
    bar: pd.Series,
    atr: float,
    config: Config,
) -> ExitDecision:
    """Trail remainder at 2x ATR after the partial (D1 Stage 3)."""
    if position.stage not in _PARTIAL_STAGES or pd.isna(atr):
        return ExitDecision(ExitAction.NONE, "HOLD")
    distance = config.risk.trailing.trail_atr_multiplier * float(atr)
    close = float(bar["close"])
    if position.direction is Direction.LONG:
        trail_sl = close - distance
        should_move = trail_sl > position.sl_price
    else:
        trail_sl = close + distance
        should_move = trail_sl < position.sl_price
    if not should_move:
        return ExitDecision(ExitAction.NONE, "HOLD")
    return ExitDecision(ExitAction.TRAIL_SL, "STAGE_3_ATR_TRAIL", new_sl=trail_sl)


def _stop_hit(position: Position, bar: pd.Series) -> bool:
    """True if this closed candle traded through the current stop."""
    if position.direction is Direction.LONG:
        return float(bar["low"]) <= position.sl_price
    return float(bar["high"]) >= position.sl_price


def _r_multiple(position: Position, bar: pd.Series) -> float:
    """Favourable excursion of this bar in R, using the original SL distance."""
    initial_sl = (
        position.initial_sl_price if position.initial_sl_price is not None else position.sl_price
    )
    risk = abs(position.entry_price - initial_sl)
    if risk == 0:
        return 0.0
    if position.direction is Direction.LONG:
        return (float(bar["high"]) - position.entry_price) / risk
    return (position.entry_price - float(bar["low"])) / risk


def _closed_candles(data: pd.DataFrame) -> pd.DataFrame | None:
    """Drop the forming last row. None if nothing closed remains."""
    if len(data) < 2:
        return None
    return data.iloc[:-1]


def _candle_time(closed: pd.DataFrame) -> datetime:
    """UTC timestamp of the last closed bar from the index or a time column."""
    idx = closed.index[-1]
    stamp = pd.Timestamp(idx)
    converted = stamp.to_pydatetime()
    if isinstance(converted, datetime):
        return converted
    if "time" in closed.columns:
        value = closed["time"].iloc[-1]
        if isinstance(value, datetime):
            return value
    raise ValueError("closed candles need a DatetimeIndex or a time column")


def _fallback_time(data: pd.DataFrame) -> datetime:
    """Best-effort timestamp when there are no closed candles yet."""
    if len(data) == 0:
        return datetime(1970, 1, 1)
    try:
        return _candle_time(data)
    except ValueError:
        return datetime(1970, 1, 1)


def _none(symbol: str, timestamp: datetime, reason: str) -> Signal:
    """Build a no-entry signal."""
    return Signal(symbol, Direction.NONE, timestamp, reason)


def _full_close(reason: str) -> ExitDecision:
    """Build a remainder (or full) close."""
    return ExitDecision(ExitAction.FULL_CLOSE, reason, close_fraction=1.0)
