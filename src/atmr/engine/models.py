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
