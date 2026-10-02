"""Frozen dataclasses passed between strategy, risk, and execution.

This module is pure: no I/O, no MT5, and no clock reads. Callers pass timestamps in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# D1(a): 1.5R is a 50% partial close, not a full take-profit.
EXIT_RULE_DYNAMIC = "RSI 50-55 / middle BB / 1.5R partial"


class Direction(StrEnum):
    """Trade or signal side. NONE means no entry."""

    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class PositionStage(StrEnum):
    """Staged management of an open trade (D1)."""

    NONE = "NONE"
    BREAKEVEN = "BREAKEVEN"
    PARTIAL_CLOSED = "PARTIAL_CLOSED"
    TRAILING = "TRAILING"


class ExitAction(StrEnum):
    """What the executor should do with an open position this bar."""

    NONE = "NONE"
    MOVE_SL_BREAKEVEN = "MOVE_SL_BREAKEVEN"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    FULL_CLOSE = "FULL_CLOSE"
    TRAIL_SL = "TRAIL_SL"


@dataclass(frozen=True)
class Signal:
    """
    Strategy output for one closed candle.

    Args:
        symbol: Canonical symbol without a slash, for example ``XAUUSD``.
        direction: LONG, SHORT, or NONE.
        timestamp: Candle close time in UTC (passed in; never ``datetime.now()``).
        reason: Human-readable why this signal fired or was skipped.
    """

    symbol: str
    direction: Direction
    timestamp: datetime
    reason: str


@dataclass(frozen=True)
class TradeRequest:
    """
    An approved order to send. There is no fixed TP price (D1).

    Args:
        symbol: Canonical symbol without a slash.
        direction: LONG or SHORT (never NONE).
        lot: Volume after rounding down to the broker step.
        entry_price: Intended fill price.
        sl_price: Mandatory stop loss; an order without SL is refused later.
        risk_pct: Percent of balance risked on this trade (for example 0.5).
        exit_rule: How the remainder exits; 1.5R is partial close only.
    """

    symbol: str
    direction: Direction
    lot: float
    entry_price: float
    sl_price: float
    risk_pct: float
    exit_rule: str = EXIT_RULE_DYNAMIC


@dataclass(frozen=True)
class Position:
    """
    A bot-managed open trade.

    Args:
        ticket: Broker ticket / order id.
        symbol: Canonical symbol without a slash.
        direction: LONG or SHORT.
        lot: Current remaining volume.
        entry_price: Fill price.
        sl_price: Current stop loss.
        opened_at: Fill time in UTC.
        stage: Trailing stage (NONE until +1R breakeven).
        candles_open: Closed candles since entry (time stop uses this).
        original_lot: Size at open, used for the 50% partial (D1 Stage 2).
        initial_sl_price: SL at entry, used for 1R / 1.5R. Current sl_price
            may already have moved to breakeven.
    """

    ticket: int
    symbol: str
    direction: Direction
    lot: float
    entry_price: float
    sl_price: float
    opened_at: datetime
    stage: PositionStage = PositionStage.NONE
    candles_open: int = 0
    original_lot: float | None = None
    initial_sl_price: float | None = None


@dataclass(frozen=True)
class RiskDecision:
    """
    Final veto from the Risk Manager. Callers must not override a rejection.

    Args:
        approved: True only when a trade may be sent.
        trade_request: Sized request when approved; None when rejected.
        reason: Why the signal was approved or rejected.
    """

    approved: bool
    reason: str
    trade_request: TradeRequest | None = None


@dataclass(frozen=True)
class ExitDecision:
    """
    Strategy instruction for an open position on one closed candle.

    D1: 1.5R produces PARTIAL_CLOSE only, never FULL_CLOSE.

    Args:
        action: NONE, MOVE_SL_BREAKEVEN, PARTIAL_CLOSE, FULL_CLOSE, or TRAIL_SL.
        reason: Why this action was chosen (TIME_STOP, SL, STAGE_1, ...).
        new_sl: Stop to apply for breakeven or ATR trail; None otherwise.
        close_fraction: 1.0 for a full close, 0.5 for the one-time partial.
    """

    action: ExitAction
    reason: str
    new_sl: float | None = None
    close_fraction: float = 1.0


LOCK_DAILY = "DAILY"
LOCK_WEEKLY = "WEEKLY"
LOCK_KILLED = "KILLED"


@dataclass(frozen=True)
class LimitDecision:
    """
    Result of a daily or weekly drawdown check.

    Args:
        breached: True when the loss limit is hit or exceeded.
        close_all: True when open trades must be closed (D6: both daily and weekly).
        stop_trading: True when new entries are locked.
        lock_type: ``DAILY``, ``WEEKLY``, or None.
    """

    breached: bool
    close_all: bool
    stop_trading: bool
    lock_type: str | None = None

    def __bool__(self) -> bool:
        """Return True when the limit is breached."""
        return self.breached


@dataclass(frozen=True)
class AccountState:
    """
    Snapshot of account and market facts for a risk decision.

    Time and prices are passed in; this type never reads a clock or MT5.

    Args:
        balance: Account balance used for sizing.
        equity: Current equity for drawdown checks.
        open_positions: Positions already open (bot-managed).
        is_paused: True when new entries are paused.
        lock_type: Active lock (DAILY / WEEKLY / KILLED) or None.
        spread_points: Current spread in points for the signal symbol.
        price: Intended entry price (passed in).
        atr: ATR used to place the initial SL.
        tick_value: Broker ``trade_tick_value``.
        tick_size: Broker ``trade_tick_size``.
        volume_step: Broker volume step.
        volume_min: Broker minimum volume.
        volume_max: Broker maximum volume.
    """

    balance: float
    equity: float
    open_positions: tuple[Position, ...] = ()
    is_paused: bool = False
    lock_type: str | None = None
    spread_points: float = 0.0
    price: float = 0.0
    atr: float = 0.0
    tick_value: float = 1.0
    tick_size: float = 1.0
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
