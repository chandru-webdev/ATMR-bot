"""StrategyEngine tests. No MT5; indicators are stubbed unless noted."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from atmr.config import (
    CommandsConfig,
    Config,
    ExitsConfig,
    IndicatorConfig,
    KillSwitchConfig,
    LimitsConfig,
    ModeConfig,
    NotificationsConfig,
    RiskConfig,
    Secrets,
    SessionsConfig,
    StrategyConfig,
    SymbolConfig,
    TrailingConfig,
)
from atmr.engine.models import (
    Direction,
    ExitAction,
    Position,
    PositionStage,
)
from atmr.engine.strategy import StrategyEngine

_START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def _config() -> Config:
    return Config(
        secrets=Secrets(
            mt5_login=1,
            mt5_password="x",
            mt5_server="Demo",
            n8n_base_url="https://example.test",
            x_bot_api_key="k" * 32,
            telegram_bot_token="t",
            telegram_chat_id="1",
        ),
        mode=ModeConfig("demo", "H1", 30, 5),
        symbols=(SymbolConfig("XAUUSD", "XAUUSD", 50),),
        indicators=IndicatorConfig(5, 5, 5, 2.0, 5),
        strategy=StrategyConfig(35, 70, 1),
        exits=ExitsConfig(50, 55, 15),
        risk=RiskConfig(
            0.5,
            1.0,
            0.25,
            1.5,
            1.5,
            TrailingConfig(1.0, 1.5, 50, 2.0),
            LimitsConfig(2, 2.0, 5.0),
            (),
        ),
        sessions=SessionsConfig(False, ()),
        kill_switch=KillSwitchConfig("account", 60, 120),
        commands=CommandsConfig(600),
        notifications=NotificationsConfig("Asia/Kolkata", 5, 23),
        magic_number=20260928,
    )


def _frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = [_START + timedelta(hours=i) for i in range(len(rows))]
    return pd.DataFrame(rows, index=index)


def _long_base_rows() -> list[dict[str, float]]:
    """Touch on the previous closed bar, bullish confirmation, dummy forming."""
    return [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 100.5, "low": 90.0, "close": 99.0},  # touch
        {"open": 99.0, "high": 111.0, "low": 98.5, "close": 110.0},  # confirm
        {"open": 110.0, "high": 112.0, "low": 109.0, "close": 111.0},  # forming
    ]


def _short_base_rows() -> list[dict[str, float]]:
    return [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 120.0, "low": 99.5, "close": 101.0},  # touch
        {"open": 101.0, "high": 102.0, "low": 89.0, "close": 90.0},  # confirm
        {"open": 90.0, "high": 91.0, "low": 88.0, "close": 89.0},  # forming
    ]


def _constant_series(data: pd.DataFrame, value: float) -> pd.Series:
    return pd.Series(value, index=data.index, dtype="float64")


def _patch_long_indicators(monkeypatch: pytest.MonkeyPatch, **overrides: float) -> None:
    ema = overrides.get("ema", 100.0)
    rsi = overrides.get("rsi", 30.0)
    lower = overrides.get("lower", 95.0)
    upper = overrides.get("upper", 120.0)
    middle = overrides.get("middle", 107.0)
    _patch_indicators(monkeypatch, ema=ema, rsi=rsi, lower=lower, upper=upper, middle=middle)


def _patch_short_indicators(monkeypatch: pytest.MonkeyPatch, **overrides: float) -> None:
    _patch_indicators(
        monkeypatch,
        ema=overrides.get("ema", 100.0),
        rsi=overrides.get("rsi", 80.0),
        lower=overrides.get("lower", 80.0),
        upper=overrides.get("upper", 110.0),
        middle=overrides.get("middle", 95.0),
    )


def _patch_indicators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ema: float,
    rsi: float,
    lower: float,
    upper: float,
    middle: float,
    atr: float = 2.0,
) -> None:
    def fake_ema(data: pd.DataFrame, period: int = 200) -> pd.Series:
        return _constant_series(data, ema)

    def fake_rsi(data: pd.DataFrame, period: int = 14) -> pd.Series:
        return _constant_series(data, rsi)

    def fake_bb(data: pd.DataFrame, period: int = 20, stddev: float = 2.0) -> dict[str, pd.Series]:
        return {
            "upper": _constant_series(data, upper),
            "middle": _constant_series(data, middle),
            "lower": _constant_series(data, lower),
        }

    def fake_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
        return _constant_series(data, atr)

    monkeypatch.setattr("atmr.engine.strategy.calculate_ema", fake_ema)
    monkeypatch.setattr("atmr.engine.strategy.calculate_rsi", fake_rsi)
    monkeypatch.setattr("atmr.engine.strategy.calculate_bollinger_bands", fake_bb)
    monkeypatch.setattr("atmr.engine.strategy.calculate_atr", fake_atr)


def _long_position(**kwargs: Any) -> Position:
    defaults: dict[str, Any] = {
        "ticket": 1,
        "symbol": "XAUUSD",
        "direction": Direction.LONG,
        "lot": 0.2,
        "entry_price": 100.0,
        "sl_price": 90.0,
        "opened_at": _START,
        "stage": PositionStage.NONE,
        "candles_open": 3,
        "original_lot": 0.2,
        "initial_sl_price": 90.0,
    }
    defaults.update(kwargs)
    return Position(**defaults)


def _exit_bars(high: float, low: float, close: float) -> pd.DataFrame:
    return _frame(
        [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"open": 100.0, "high": high, "low": low, "close": close},
            {"open": close, "high": close + 1, "low": close - 1, "close": close},
        ]
    )


def test_long_fires_when_all_four_conditions_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_long_indicators(monkeypatch)
    signal = StrategyEngine().evaluate_entry(_frame(_long_base_rows()), _config())
    assert signal.direction is Direction.LONG


@pytest.mark.parametrize(
    ("override", "row_changes"),
    [
        ({"ema": 120.0}, {}),
        ({"rsi": 40.0}, {}),
        ({"lower": 80.0}, {}),
        ({}, {2: {"open": 111.0, "high": 111.0, "low": 98.5, "close": 110.0}}),
    ],
)
def test_long_blocked_when_any_condition_fails(
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, float],
    row_changes: dict[int, dict[str, float]],
) -> None:
    _patch_long_indicators(monkeypatch, **override)
    rows = _long_base_rows()
    for index, values in row_changes.items():
        rows[index] = values
    signal = StrategyEngine().evaluate_entry(_frame(rows), _config())
    assert signal.direction is Direction.NONE


def test_short_fires_when_all_four_conditions_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_short_indicators(monkeypatch)
    signal = StrategyEngine().evaluate_entry(_frame(_short_base_rows()), _config())
    assert signal.direction is Direction.SHORT


@pytest.mark.parametrize(
    ("override", "row_changes"),
    [
        ({"ema": 80.0}, {}),
        ({"rsi": 60.0}, {}),
        ({"upper": 130.0}, {}),
        ({}, {2: {"open": 89.0, "high": 102.0, "low": 89.0, "close": 90.0}}),
    ],
)
def test_short_blocked_when_any_condition_fails(
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, float],
    row_changes: dict[int, dict[str, float]],
) -> None:
    _patch_short_indicators(monkeypatch, **override)
    rows = _short_base_rows()
    for index, values in row_changes.items():
        rows[index] = values
    signal = StrategyEngine().evaluate_entry(_frame(rows), _config())
    assert signal.direction is Direction.NONE


def test_forming_candle_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_long_indicators(monkeypatch)
    rows = _long_base_rows()
    rows[2] = {"open": 111.0, "high": 111.0, "low": 98.5, "close": 110.0}  # bearish closed
    rows[3] = {"open": 99.0, "high": 120.0, "low": 98.0, "close": 119.0}  # bullish forming
    signal = StrategyEngine().evaluate_entry(_frame(rows), _config())
    assert signal.direction is Direction.NONE


def test_one_point_five_r_is_never_full_close(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_long_indicators(monkeypatch, rsi=70.0, middle=50.0)
    engine = StrategyEngine()
    position = _long_position()
    # +1.5R: high = 100 + 1.5*10 = 115
    decision = engine.evaluate_exit(_exit_bars(115.0, 101.0, 114.0), position, _config())
    assert decision.action is not ExitAction.FULL_CLOSE
    assert decision.action is ExitAction.MOVE_SL_BREAKEVEN


def test_stage_2_partial_fires_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_long_indicators(monkeypatch, rsi=70.0, middle=50.0)
    engine = StrategyEngine()
    config = _config()
    first = engine.evaluate_exit(
        _exit_bars(115.0, 101.0, 114.0),
        _long_position(stage=PositionStage.BREAKEVEN, sl_price=100.0),
        config,
    )
    assert first.action is ExitAction.PARTIAL_CLOSE
    assert first.close_fraction == 0.5
    second = engine.evaluate_exit(
        _exit_bars(130.0, 120.0, 125.0),
        _long_position(stage=PositionStage.PARTIAL_CLOSED, sl_price=100.0, lot=0.1),
        config,
    )
    assert second.action is not ExitAction.PARTIAL_CLOSE


def test_time_stop_at_candle_15(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_long_indicators(monkeypatch, rsi=70.0, middle=50.0)
    engine = StrategyEngine()
    at_fifteen = engine.evaluate_exit(
        _exit_bars(101.0, 99.0, 100.0),
        _long_position(candles_open=15),
        _config(),
    )
    assert at_fifteen.action is ExitAction.FULL_CLOSE
    assert at_fifteen.reason.startswith("TIME_STOP")
    at_fourteen = engine.evaluate_exit(
        _exit_bars(101.0, 99.0, 100.0),
        _long_position(candles_open=14),
        _config(),
    )
    assert at_fourteen.action is not ExitAction.FULL_CLOSE
    assert "TIME_STOP" not in at_fourteen.reason


def test_rsi_band_full_closes_remainder(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_long_indicators(monkeypatch, rsi=52.0, middle=50.0)
    decision = StrategyEngine().evaluate_exit(
        _exit_bars(101.0, 99.0, 100.5),
        _long_position(stage=PositionStage.TRAILING, sl_price=90.0, lot=0.1),
        _config(),
    )
    assert decision.action is ExitAction.FULL_CLOSE
    assert decision.reason == "TP_RSI"


def test_middle_bb_full_closes_remainder(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_long_indicators(monkeypatch, rsi=40.0, middle=100.5)
    decision = StrategyEngine().evaluate_exit(
        _exit_bars(101.0, 100.0, 100.8),
        _long_position(stage=PositionStage.PARTIAL_CLOSED, sl_price=90.0, lot=0.1),
        _config(),
    )
    assert decision.action is ExitAction.FULL_CLOSE
    assert decision.reason == "TP_MID_BB"
