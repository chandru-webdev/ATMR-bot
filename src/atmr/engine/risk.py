"""
Risk Manager: sizing, loss-limit circuit breaker, trade veto, D1 trailing.

Pure: no MT5, no network, no ``datetime.now()``. The veto is final.
"""

from __future__ import annotations

import math

from atmr.config import (
    HARD_MAX_CONCURRENT_TRADES,
    HARD_MAX_DAILY_LOSS_PCT,
    HARD_MAX_RISK_PCT_DEMO,
    HARD_MAX_RISK_PCT_LIVE,
    HARD_MAX_WEEKLY_LOSS_PCT,
    Config,
)
from atmr.engine.models import (
    EXIT_RULE_DYNAMIC,
    LOCK_DAILY,
    LOCK_KILLED,
    LOCK_WEEKLY,
    AccountState,
    Direction,
    ExitAction,
    ExitDecision,
    LimitDecision,
    Position,
    PositionStage,
    RiskDecision,
    Signal,
    TradeRequest,
)

_PARTIAL_STAGES = frozenset({PositionStage.PARTIAL_CLOSED, PositionStage.TRAILING})
_ENTRY_LOCKS = frozenset({LOCK_DAILY, LOCK_WEEKLY, LOCK_KILLED})


class RiskManager:
    """Capital protection and the last veto before an order is sent."""

    def __init__(self, config: Config) -> None:
        """
        Store validated config for caps, correlation groups, and trailing.

        Args:
            config: Loaded bot configuration. Hard caps cannot be loosened.
        """
        self._config = config

    def size_position(
        self,
        balance: float,
        risk_pct: float,
        sl_distance: float,
        tick_value: float,
        tick_size: float,
        volume_step: float,
        volume_min: float,
        volume_max: float,
    ) -> float:
        """
        Return lot size rounded down to ``volume_step``, or 0 to skip.

        Args:
            balance: Account balance.
            risk_pct: Percent of balance to risk (0.5 means 0.5%, not 50%).
            sl_distance: Absolute price distance from entry to SL.
            tick_value: Broker money value of one tick per lot.
            tick_size: Broker tick size in price units.
            volume_step: Lot increment.
            volume_min: Minimum lot. Below this, return 0 (never round up).
            volume_max: Maximum lot clamp.

        Returns:
            float: Lots to send, or 0.0 if the trade must be skipped.
        """
        raw = self.raw_position_size(balance, risk_pct, sl_distance, tick_value, tick_size)
        if raw <= 0:
            return 0.0
        lots = _round_down(raw, volume_step)
        if volume_max > 0:
            lots = min(lots, volume_max)
            lots = _round_down(lots, volume_step)
        if lots < volume_min:
            return 0.0
        return lots

    def raw_position_size(
        self,
        balance: float,
        risk_pct: float,
        sl_distance: float,
        tick_value: float,
        tick_size: float,
    ) -> float:
        """
        Unrounded lots: ``(balance * risk_pct/100) / (sl * tick_value/tick_size)``.

        Args:
            balance: Account balance.
            risk_pct: Percent of balance to risk.
            sl_distance: Absolute SL distance in price.
            tick_value: Broker tick value per lot.
            tick_size: Broker tick size.

        Returns:
            float: Raw lots (may be below ``volume_min``).
        """
        if balance <= 0 or risk_pct <= 0 or sl_distance <= 0 or tick_value <= 0 or tick_size <= 0:
            return 0.0
        risk_money = balance * (risk_pct / 100.0)
        loss_per_lot = sl_distance * (tick_value / tick_size)
        if loss_per_lot <= 0:
            return 0.0
        return risk_money / loss_per_lot

    def check_daily_limit(
        self,
        current_equity: float,
        day_start_equity: float,
        limit_pct: float = HARD_MAX_DAILY_LOSS_PCT,
    ) -> LimitDecision:
        """
        True (breached) when equity is down ``limit_pct`` or more vs day start.

        A breach closes all trades and locks entries until the next day.

        Args:
            current_equity: Latest equity.
            day_start_equity: Equity at broker-day start.
            limit_pct: Drawdown percent. Capped at the hard 2% maximum.

        Returns:
            LimitDecision: ``close_all`` and ``lock_type=DAILY`` when breached.
        """
        capped = min(limit_pct, HARD_MAX_DAILY_LOSS_PCT)
        return _drawdown_decision(current_equity, day_start_equity, capped, LOCK_DAILY)

    def check_weekly_limit(
        self,
        current_equity: float,
        week_start_equity: float,
        limit_pct: float = HARD_MAX_WEEKLY_LOSS_PCT,
    ) -> LimitDecision:
        """
        True (breached) when equity is down ``limit_pct`` or more vs week start.

        D6: same as daily — close all and lock until the following week.

        Args:
            current_equity: Latest equity.
            week_start_equity: Equity at the week's first market open.
            limit_pct: Drawdown percent. Capped at the hard 5% maximum.

        Returns:
            LimitDecision: ``close_all`` and ``lock_type=WEEKLY`` when breached.
        """
        capped = min(limit_pct, HARD_MAX_WEEKLY_LOSS_PCT)
        return _drawdown_decision(current_equity, week_start_equity, capped, LOCK_WEEKLY)

    def approve_trade(
        self,
        signal: Signal,
        account_state: AccountState,
        config: Config,
    ) -> RiskDecision:
        """
        Veto or approve an entry. First failing check wins.

        Args:
            signal: Strategy signal. NONE is always rejected.
            account_state: Balances, open trades, spread, and broker volume.
            config: Risk, symbol, and mode settings.

        Returns:
            RiskDecision: ``approved`` with a ``TradeRequest``, or a reason.
        """
        reason = self._veto_reason(signal, account_state, config)
        if reason is not None:
            return RiskDecision(approved=False, reason=reason)
        request = self._build_request(signal, account_state, config)
        if request is None:
            return RiskDecision(approved=False, reason="lot below volume_min")
        return RiskDecision(approved=True, reason="approved", trade_request=request)

    def evaluate_trailing(
        self,
        position: Position,
        current_price: float,
        atr: float,
    ) -> ExitDecision:
        """
        D1 stages using a single current price (not the full candle).

        Stage 1 at +1R, Stage 2 at +1.5R once, Stage 3 trail 2x ATR forward only.

        Args:
            position: Open trade with stage and initial SL.
            current_price: Mark price passed in by the caller.
            atr: Current ATR for the trail distance.

        Returns:
            ExitDecision: Breakeven, one-time partial, trail, or NONE.
        """
        trailing = self._config.risk.trailing
        r_multiple = _r_from_price(position, current_price)
        if position.stage is PositionStage.NONE and r_multiple >= trailing.breakeven_r:
            return ExitDecision(
                ExitAction.MOVE_SL_BREAKEVEN,
                "STAGE_1_BREAKEVEN",
                new_sl=position.entry_price,
            )
        if position.stage not in _PARTIAL_STAGES and r_multiple >= trailing.partial_close_r:
            return ExitDecision(
                ExitAction.PARTIAL_CLOSE,
                "STAGE_2_PARTIAL_1_5R",
                close_fraction=trailing.partial_close_pct / 100.0,
            )
        if position.stage not in _PARTIAL_STAGES:
            return ExitDecision(ExitAction.NONE, "HOLD")
        return _trail_forward(position, current_price, atr, trailing.trail_atr_multiplier)

    def _veto_reason(
        self,
        signal: Signal,
        state: AccountState,
        config: Config,
    ) -> str | None:
        """Return the first rejection reason, or None if sizing is next."""
        if signal.direction is Direction.NONE:
            return "no entry signal"
        if state.is_paused:
            return "paused"
        if state.lock_type in _ENTRY_LOCKS:
            return f"locked:{state.lock_type}"
        max_trades = min(config.risk.limits.max_concurrent_trades, HARD_MAX_CONCURRENT_TRADES)
        if len(state.open_positions) >= max_trades:
            return "max concurrent trades"
        if _same_direction_correlated(signal, state.open_positions, config):
            return "correlated pair already open same direction"
        max_spread = _max_spread(config, signal.symbol)
        if state.spread_points > max_spread:
            return "spread too wide"
        return None

    def _build_request(
        self,
        signal: Signal,
        state: AccountState,
        config: Config,
    ) -> TradeRequest | None:
        """Size the trade and attach a mandatory SL. None if lots are 0."""
        risk_pct = _risk_pct_for_mode(config)
        sl_distance = config.risk.sl_atr_multiplier * state.atr
        lots = self.size_position(
            state.balance,
            risk_pct,
            sl_distance,
            state.tick_value,
            state.tick_size,
            state.volume_step,
            state.volume_min,
            state.volume_max,
        )
        if lots <= 0:
            return None
        sl_price = _stop_from_entry(signal.direction, state.price, sl_distance)
        return TradeRequest(
            symbol=signal.symbol,
            direction=signal.direction,
            lot=lots,
            entry_price=state.price,
            sl_price=sl_price,
            risk_pct=risk_pct,
            exit_rule=EXIT_RULE_DYNAMIC,
        )


def _drawdown_decision(
    current: float,
    start: float,
    limit_pct: float,
    lock_type: str,
) -> LimitDecision:
    """Build a LimitDecision from equity vs a baseline."""
    if start <= 0:
        return LimitDecision(True, True, True, lock_type)
    drawdown_pct = (start - current) / start * 100.0
    if drawdown_pct + 1e-12 >= limit_pct:
        return LimitDecision(True, True, True, lock_type)
    return LimitDecision(False, False, False, None)


def _round_down(lots: float, step: float) -> float:
    """Floor lots to a whole number of volume steps."""
    if step <= 0 or lots <= 0:
        return 0.0
    steps = math.floor((lots / step) + 1e-12)
    return round(steps * step, 10)


def _risk_pct_for_mode(config: Config) -> float:
    """Demo 0.5% or live (capped). Never above hard maxima."""
    if config.is_live():
        return min(config.risk.risk_pct_live_initial, HARD_MAX_RISK_PCT_LIVE)
    return min(config.risk.risk_pct_demo, HARD_MAX_RISK_PCT_DEMO)


def _max_spread(config: Config, symbol: str) -> float:
    """Look up ``max_spread_points`` for a canonical symbol."""
    for item in config.symbols:
        if item.canonical == symbol:
            return float(item.max_spread_points)
    return 0.0


def _same_direction_correlated(
    signal: Signal,
    open_positions: tuple[Position, ...],
    config: Config,
) -> bool:
    """True when an open trade in the same group shares this direction."""
    peers = _correlated_peers(signal.symbol, config.risk.correlation_groups)
    if not peers:
        return False
    for position in open_positions:
        if position.symbol in peers and position.direction is signal.direction:
            return True
    return False


def _correlated_peers(
    symbol: str,
    groups: tuple[tuple[str, ...], ...],
) -> frozenset[str]:
    """Other canonical symbols that share a correlation group with ``symbol``."""
    peers: set[str] = set()
    for group in groups:
        names = {name.upper() for name in group}
        if symbol.upper() in names:
            peers.update(names)
    peers.discard(symbol.upper())
    return frozenset(peers)


def _stop_from_entry(direction: Direction, entry: float, sl_distance: float) -> float:
    """Place SL ``sl_distance`` away from entry."""
    if direction is Direction.LONG:
        return entry - sl_distance
    return entry + sl_distance


def _r_from_price(position: Position, current_price: float) -> float:
    """R-multiple of ``current_price`` vs original SL distance."""
    initial_sl = (
        position.initial_sl_price if position.initial_sl_price is not None else position.sl_price
    )
    risk = abs(position.entry_price - initial_sl)
    if risk == 0:
        return 0.0
    if position.direction is Direction.LONG:
        return (current_price - position.entry_price) / risk
    return (position.entry_price - current_price) / risk


def _trail_forward(
    position: Position,
    current_price: float,
    atr: float,
    multiplier: float,
) -> ExitDecision:
    """Move SL only in the trade's favour (never backward)."""
    if atr <= 0:
        return ExitDecision(ExitAction.NONE, "HOLD")
    distance = multiplier * atr
    if position.direction is Direction.LONG:
        trail_sl = current_price - distance
        should_move = trail_sl > position.sl_price
    else:
        trail_sl = current_price + distance
        should_move = trail_sl < position.sl_price
    if not should_move:
        return ExitDecision(ExitAction.NONE, "HOLD")
    return ExitDecision(ExitAction.TRAIL_SL, "STAGE_3_ATR_TRAIL", new_sl=trail_sl)
