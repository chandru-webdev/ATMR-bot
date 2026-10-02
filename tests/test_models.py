"""Construction tests for frozen engine models. No I/O."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from atmr.engine.models import (
    EXIT_RULE_DYNAMIC,
    Direction,
    Position,
    PositionStage,
    RiskDecision,
    Signal,
    TradeRequest,
)

_TS = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_signal_long_construction() -> None:
    signal = Signal(
        symbol="XAUUSD",
        direction=Direction.LONG,
        timestamp=_TS,
        reason="EMA trend up, RSI oversold, lower band touch",
    )
    assert signal.symbol == "XAUUSD"
    assert signal.direction is Direction.LONG
    assert signal.timestamp == _TS


def test_signal_none_is_no_entry() -> None:
    signal = Signal(
        symbol="XAUUSD",
        direction=Direction.NONE,
        timestamp=_TS,
        reason="RSI not oversold",
    )
    assert signal.direction is Direction.NONE


def test_signal_is_frozen() -> None:
    signal = Signal(
        symbol="XAUUSD",
        direction=Direction.SHORT,
        timestamp=_TS,
        reason="short setup",
    )
    with pytest.raises(FrozenInstanceError):
        signal.reason = "mutated"  # type: ignore[misc]


def test_trade_request_has_sl_and_no_fixed_tp() -> None:
    request = TradeRequest(
        symbol="XAUUSD",
        direction=Direction.LONG,
        lot=0.10,
        entry_price=2650.0,
        sl_price=2640.0,
        risk_pct=0.5,
    )
    assert request.sl_price < request.entry_price
    assert request.exit_rule == EXIT_RULE_DYNAMIC
    assert not hasattr(request, "tp_price")


def test_position_default_stage_and_candles() -> None:
    position = Position(
        ticket=123456,
        symbol="XAUUSD",
        direction=Direction.SHORT,
        lot=0.20,
        entry_price=2650.0,
        sl_price=2660.0,
        opened_at=_TS,
    )
    assert position.stage is PositionStage.NONE
    assert position.candles_open == 0


def test_position_trailing_stages() -> None:
    position = Position(
        ticket=1,
        symbol="XAUUSD",
        direction=Direction.LONG,
        lot=0.10,
        entry_price=100.0,
        sl_price=100.0,
        opened_at=_TS,
        stage=PositionStage.PARTIAL_CLOSED,
        candles_open=4,
        original_lot=0.20,
    )
    assert position.stage is PositionStage.PARTIAL_CLOSED
    assert position.original_lot == 0.20


def test_risk_decision_approved() -> None:
    request = TradeRequest(
        symbol="XAUUSD",
        direction=Direction.LONG,
        lot=0.05,
        entry_price=2650.0,
        sl_price=2641.0,
        risk_pct=0.5,
    )
    decision = RiskDecision(approved=True, reason="all checks passed", trade_request=request)
    assert decision.approved is True
    assert decision.trade_request is request


def test_risk_decision_rejected_has_no_request() -> None:
    decision = RiskDecision(approved=False, reason="max concurrent trades")
    assert decision.approved is False
    assert decision.trade_request is None
