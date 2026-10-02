"""RiskManager unit tests. No MT5. Values from TESTING.md section 2.3."""

from __future__ import annotations

from datetime import UTC, datetime

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
    LOCK_DAILY,
    LOCK_WEEKLY,
    AccountState,
    Direction,
    ExitAction,
    Position,
    PositionStage,
    Signal,
)
from atmr.engine.risk import RiskManager

_TS = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _config(*, groups: tuple[tuple[str, ...], ...] = ()) -> Config:
    symbols = (
        SymbolConfig("XAUUSD", "XAUUSD", 50),
        SymbolConfig("EURUSD", "EURUSD", 20),
        SymbolConfig("AUDUSD", "AUDUSD", 20),
    )
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
        symbols=symbols,
        indicators=IndicatorConfig(200, 14, 20, 2.0, 14),
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
            groups,
        ),
        sessions=SessionsConfig(False, ()),
        kill_switch=KillSwitchConfig("account", 60, 120),
        commands=CommandsConfig(600),
        notifications=NotificationsConfig("Asia/Kolkata", 5, 23),
        magic_number=20260928,
    )


def _manager(groups: tuple[tuple[str, ...], ...] = ()) -> RiskManager:
    return RiskManager(_config(groups=groups))


def _signal(symbol: str = "XAUUSD", direction: Direction = Direction.LONG) -> Signal:
    return Signal(symbol, direction, _TS, "test")


def _position(
    symbol: str = "XAUUSD",
    direction: Direction = Direction.LONG,
    **kwargs: object,
) -> Position:
    fields: dict[str, object] = {
        "ticket": 1,
        "symbol": symbol,
        "direction": direction,
        "lot": 0.10,
        "entry_price": 100.0,
        "sl_price": 90.0,
        "opened_at": _TS,
        "stage": PositionStage.NONE,
        "initial_sl_price": 90.0,
    }
    fields.update(kwargs)
    return Position(**fields)  # type: ignore[arg-type]


def _state(**kwargs: object) -> AccountState:
    fields: dict[str, object] = {
        "balance": 10_000.0,
        "equity": 10_000.0,
        "price": 2650.0,
        "atr": 6.0,
        "tick_value": 1.0,
        "tick_size": 0.01,
        "volume_step": 0.01,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "spread_points": 10.0,
    }
    fields.update(kwargs)
    return AccountState(**fields)  # type: ignore[arg-type]


def test_sizing_eurusd_1_percent_raw_and_rounded() -> None:
    # TESTING.md 2.3: $10,000, 1%, SL 0.0030, $300/lot → raw 0.33333 → 0.33
    manager = _manager()
    raw = manager.raw_position_size(10_000.0, 1.0, 0.0030, 1.0, 0.00001)
    final = manager.size_position(10_000.0, 1.0, 0.0030, 1.0, 0.00001, 0.01, 0.01, 100.0)
    assert raw == pytest.approx(0.33333, abs=5e-6)
    assert final == 0.33
    assert final <= raw


def test_sizing_eurusd_0_5_percent_demo() -> None:
    # TESTING.md 2.3: $50 risk → raw 0.16667 → 0.16
    manager = _manager()
    raw = manager.raw_position_size(10_000.0, 0.5, 0.0030, 1.0, 0.00001)
    final = manager.size_position(10_000.0, 0.5, 0.0030, 1.0, 0.00001, 0.01, 0.01, 100.0)
    assert raw == pytest.approx(0.16667, abs=5e-6)
    assert final == 0.16


def test_sizing_xauusd_1_percent() -> None:
    # TESTING.md 2.3: SL $9, $900/lot → raw 0.11111 → 0.11
    manager = _manager()
    raw = manager.raw_position_size(10_000.0, 1.0, 9.0, 1.0, 0.01)
    final = manager.size_position(10_000.0, 1.0, 9.0, 1.0, 0.01, 0.01, 0.01, 100.0)
    assert raw == pytest.approx(0.11111, abs=5e-6)
    assert final == 0.11


def test_sizing_below_volume_min_returns_zero_never_rounds_up() -> None:
    manager = _manager()
    # Raw lots ~0.0033 with step/min 0.01 → skip, do not round up to 0.01
    raw = manager.raw_position_size(100.0, 1.0, 0.0030, 1.0, 0.00001)
    final = manager.size_position(100.0, 1.0, 0.0030, 1.0, 0.00001, 0.01, 0.01, 100.0)
    assert 0 < raw < 0.01
    assert final == 0.0


def test_daily_2_percent_breach_closes_all_and_locks_daily() -> None:
    decision = _manager().check_daily_limit(9800.0, 10_000.0, 2.0)
    assert decision.breached is True
    assert decision.close_all is True
    assert decision.stop_trading is True
    assert decision.lock_type == LOCK_DAILY
    assert bool(decision) is True


def test_daily_limit_not_breached_below_2_percent() -> None:
    decision = _manager().check_daily_limit(9810.0, 10_000.0, 2.0)
    assert decision.breached is False
    assert decision.close_all is False
    assert decision.lock_type is None


def test_weekly_5_percent_breach_closes_all_and_locks_weekly() -> None:
    decision = _manager().check_weekly_limit(9500.0, 10_000.0, 5.0)
    assert decision.breached is True
    assert decision.close_all is True
    assert decision.stop_trading is True
    assert decision.lock_type == LOCK_WEEKLY


def test_max_concurrent_trades_blocks_third() -> None:
    manager = _manager()
    open_two = (_position(ticket=1), _position(ticket=2, symbol="EURUSD"))
    decision = manager.approve_trade(
        _signal("AUDUSD"),
        _state(open_positions=open_two, atr=0.001, tick_size=0.00001, price=1.10),
        _config(),
    )
    assert decision.approved is False
    assert decision.reason == "max concurrent trades"


def test_correlation_empty_groups_do_not_block() -> None:
    manager = _manager(groups=())
    open_eur = (_position(symbol="EURUSD", direction=Direction.LONG),)
    decision = manager.approve_trade(
        _signal("AUDUSD", Direction.LONG),
        _state(
            open_positions=open_eur,
            atr=0.001,
            tick_size=0.00001,
            price=0.66,
            spread_points=5.0,
        ),
        _config(groups=()),
    )
    assert decision.approved is True


def test_correlation_blocks_same_direction_in_group() -> None:
    groups = (("EURUSD", "AUDUSD"),)
    manager = _manager(groups=groups)
    config = _config(groups=groups)
    open_eur = (_position(symbol="EURUSD", direction=Direction.LONG),)
    blocked = manager.approve_trade(
        _signal("AUDUSD", Direction.LONG),
        _state(
            open_positions=open_eur,
            atr=0.001,
            tick_size=0.00001,
            price=0.66,
            spread_points=5.0,
        ),
        config,
    )
    assert blocked.approved is False
    assert "correlated" in blocked.reason
    allowed = manager.approve_trade(
        _signal("AUDUSD", Direction.SHORT),
        _state(
            open_positions=open_eur,
            atr=0.001,
            tick_size=0.00001,
            price=0.66,
            spread_points=5.0,
        ),
        config,
    )
    assert allowed.approved is True


def test_paused_and_lock_reject() -> None:
    manager = _manager()
    paused = manager.approve_trade(_signal(), _state(is_paused=True), _config())
    assert paused.approved is False
    locked = manager.approve_trade(_signal(), _state(lock_type=LOCK_DAILY), _config())
    assert locked.approved is False
    assert "DAILY" in locked.reason


def test_spread_too_wide_rejects() -> None:
    decision = _manager().approve_trade(
        _signal("XAUUSD"),
        _state(spread_points=51.0, atr=6.0, tick_size=0.01, price=2650.0),
        _config(),
    )
    assert decision.approved is False
    assert decision.reason == "spread too wide"


def test_stage_2_partial_fires_exactly_once() -> None:
    manager = _manager()
    at_1_5r = 100.0 + 1.5 * 10.0
    first = manager.evaluate_trailing(
        _position(stage=PositionStage.BREAKEVEN, sl_price=100.0),
        current_price=at_1_5r,
        atr=2.0,
    )
    assert first.action is ExitAction.PARTIAL_CLOSE
    assert first.close_fraction == 0.5
    second = manager.evaluate_trailing(
        _position(stage=PositionStage.PARTIAL_CLOSED, sl_price=100.0, lot=0.05),
        current_price=at_1_5r + 20.0,
        atr=2.0,
    )
    assert second.action is not ExitAction.PARTIAL_CLOSE


def test_stage_3_sl_never_moves_backward_long() -> None:
    manager = _manager()
    position = _position(
        stage=PositionStage.TRAILING,
        sl_price=110.0,
        entry_price=100.0,
        initial_sl_price=90.0,
    )
    # trail = price - 4; 108 - 4 = 104, which is worse than 110 → HOLD
    hold = manager.evaluate_trailing(position, current_price=108.0, atr=2.0)
    assert hold.action is ExitAction.NONE
    # trail = 130 - 4 = 126 > 110 → move up
    move = manager.evaluate_trailing(position, current_price=130.0, atr=2.0)
    assert move.action is ExitAction.TRAIL_SL
    assert move.new_sl is not None
    assert move.new_sl > position.sl_price


def test_stage_3_sl_never_moves_backward_short() -> None:
    manager = _manager()
    position = _position(
        direction=Direction.SHORT,
        stage=PositionStage.TRAILING,
        entry_price=100.0,
        sl_price=90.0,
        initial_sl_price=110.0,
    )
    # trail = 92 + 4 = 96, worse (higher) than 90 → HOLD
    hold = manager.evaluate_trailing(position, current_price=92.0, atr=2.0)
    assert hold.action is ExitAction.NONE
    # trail = 70 + 4 = 74 < 90 → move down (favourable for short)
    move = manager.evaluate_trailing(position, current_price=70.0, atr=2.0)
    assert move.action is ExitAction.TRAIL_SL
    assert move.new_sl is not None
    assert move.new_sl < position.sl_price
