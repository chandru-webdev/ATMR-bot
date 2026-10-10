"""MetaTrader 5 client connector (MT5Connector).

Sole module in ATMR-Bot authorized to import or interact directly with MetaTrader5.
Implements ARCHITECTURE.md Section 2, SECURITY.md, and CODE_STYLE.md:
- Lazy/injectable MetaTrader5 import (allows mocking and running on any OS)
- Connection with credential masking and exponential backoff
- Live-mode guard against trading real money in demo mode
- Account and Symbol info retrieval returning frozen dataclasses
- Timeframe mapping for M1/M15/H1/H4/D1
- order_send wrapper with retry on Requote/Price Changed, no retry on No Money,
  and timeout verification to prevent duplicate orders
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from atmr.config import Config
from atmr.engine.models import Direction, TradeRequest
from atmr.exceptions import (
    ConnectivityError,
    InsufficientMarginError,
    LiveModeGuardError,
    TradeExecutionError,
)
from atmr.utils.redact import mask_account

logger = logging.getLogger("MT5Connector")

# Standard MT5 Constants (defined for offline & cross-platform resilience)
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 6
TRADE_ACTION_REMOVE = 8

TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_TIMEOUT = 10015
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_PRICE_CHANGED = 10020
TRADE_RETCODE_PRICE_OFF = 10021

TIMEFRAME_MAP: dict[str, int] = {
    "M1": 1,
    "M15": 15,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}


def _load_mt5_module() -> Any:
    """Lazy import of MetaTrader5 with clear error if not installed."""
    try:
        import MetaTrader5 as mt5

        return mt5
    except ImportError as exc:
        raise ConnectivityError(
            "MetaTrader5 package is not installed. Install with 'pip install MetaTrader5'."
        ) from exc


@dataclass(frozen=True)
class AccountInfo:
    """Account balance, equity, and mode information."""

    login: int
    server: str
    balance: float
    equity: float
    currency: str
    margin_free: float
    leverage: int
    trade_mode: int


@dataclass(frozen=True)
class SymbolInfo:
    """Market specifications and pricing for a symbol."""

    name: str
    digits: int
    point: float
    spread: int
    tick_value: float
    tick_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    visible: bool
    filling_mode: int
    bid: float
    ask: float


@dataclass(frozen=True)
class PositionInfo:
    """Open position on the MT5 terminal."""

    ticket: int
    symbol: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    profit: float
    magic: int
    time: datetime


@dataclass(frozen=True)
class OrderResult:
    """Result of an order_send operation."""

    retcode: int
    deal: int
    order: int
    volume: float
    price: float
    comment: str
    request: dict[str, Any]


class MT5Connector:
    """The Hands: connects to MT5, fetches specifications, and manages orders."""

    def __init__(
        self,
        config: Config,
        mt5_module: Any = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        """
        Initialize the MT5Connector.

        Args:
            config: Bot configuration including secrets and symbols.
            mt5_module: Optional injected MetaTrader5 module for testing.
            sleep_fn: Optional sleep function for backoff timing.
        """
        self._config = config
        self._mt5 = mt5_module
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._connected = False

    def __enter__(self) -> MT5Connector:
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit."""
        self.disconnect()

    def is_connected(self) -> bool:
        """Return True if currently marked as connected."""
        return self._connected

    def connect(
        self,
        max_attempts: int = 3,
        initial_backoff_s: float = 1.0,
        backoff_factor: float = 2.0,
    ) -> None:
        """
        Connect to MT5 using credentials in Config with exponential backoff.

        Args:
            max_attempts: Maximum retry count.
            initial_backoff_s: Initial delay between retries in seconds.
            backoff_factor: Multiplier for backoff delay.

        Raises:
            ConnectivityError: If all connection attempts fail.
            LiveModeGuardError: If a real account is detected while in demo mode.
        """
        if self._mt5 is None:
            self._mt5 = _load_mt5_module()

        login = self._config.secrets.mt5_login
        password = self._config.secrets.mt5_password
        server = self._config.secrets.mt5_server
        masked = mask_account(str(login))
        delay = initial_backoff_s
        err: Any = "Unknown"

        for attempt in range(1, max_attempts + 1):
            logger.info(
                "Connecting to MT5 %s (account %s, attempt %d/%d)",
                server,
                masked,
                attempt,
                max_attempts,
            )
            if self._mt5.initialize(login=login, password=password, server=server):
                self._connected = True
                self._verify_account_mode()
                return

            err = self._mt5.last_error()
            logger.warning("MT5 connect failed (attempt %d/%d): %s", attempt, max_attempts, err)
            if attempt < max_attempts:
                self._sleep(delay)
                delay *= backoff_factor

        raise ConnectivityError(f"Failed to connect to MT5 after {max_attempts} attempts: {err}")

    def disconnect(self) -> None:
        """Disconnect and shutdown MT5 terminal connection."""
        if self._mt5 is not None and self._connected:
            try:
                self._mt5.shutdown()
            except Exception as exc:
                logger.warning("Error during MT5 shutdown: %s", exc)
        self._connected = False

    def _verify_account_mode(self) -> None:
        """Verify account info and enforce live-mode safety guard."""
        acc = self.get_account_info()
        if acc.trade_mode == ACCOUNT_TRADE_MODE_REAL and not self._config.is_live():
            self.disconnect()
            raise LiveModeGuardError(
                "Real trading account detected in demo mode (TRADING_MODE=demo). "
                "Switch to a demo account or configure live mode explicitly."
            )

    def _ensure_connected(self) -> None:
        """Ensure connection is established or initialize it."""
        if not self._connected or self._mt5 is None:
            self.connect()

    def resolve_broker_symbol(self, canonical_symbol: str) -> str:
        """
        Map canonical symbol to broker symbol name per D8.

        Args:
            canonical_symbol: Standard symbol name (e.g. 'EURUSD').

        Returns:
            str: Broker-specific symbol name (e.g. 'EURUSD.m').
        """
        for sym in self._config.symbols:
            if sym.canonical.upper() == canonical_symbol.upper():
                return sym.broker_symbol
        return canonical_symbol

    def map_timeframe(self, timeframe: str) -> int:
        """
        Map timeframe string to MT5 integer constant.

        Args:
            timeframe: One of 'M1', 'M15', 'H1', 'H4', 'D1'.

        Returns:
            int: MT5 timeframe constant.

        Raises:
            ValueError: If timeframe is not supported.
        """
        tf_upper = timeframe.upper()
        if tf_upper not in TIMEFRAME_MAP:
            allowed = sorted(TIMEFRAME_MAP)
            raise ValueError(f"Unsupported timeframe: {timeframe}. Allowed: {allowed}")
        return TIMEFRAME_MAP[tf_upper]

    def get_account_info(self) -> AccountInfo:
        """
        Get current account balance and margin details.

        Returns:
            AccountInfo: Immutable account details.

        Raises:
            ConnectivityError: If account info cannot be retrieved.
        """
        if self._mt5 is None:
            self._mt5 = _load_mt5_module()

        raw = self._mt5.account_info()
        if raw is None:
            raise ConnectivityError("Failed to get MT5 account info")

        return AccountInfo(
            login=raw.login,
            server=raw.server,
            balance=float(raw.balance),
            equity=float(raw.equity),
            currency=raw.currency,
            margin_free=float(raw.margin_free),
            leverage=int(raw.leverage),
            trade_mode=int(raw.trade_mode),
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """
        Get specifications for a tradable symbol, selecting it in Market Watch if hidden.

        Args:
            symbol: Canonical or broker symbol name.

        Returns:
            SymbolInfo: Immutable symbol specifications.

        Raises:
            TradeExecutionError: If symbol does not exist or cannot be selected.
        """
        self._ensure_connected()
        broker_sym = self.resolve_broker_symbol(symbol)
        raw = self._mt5.symbol_info(broker_sym)
        if raw is None:
            raise TradeExecutionError(f"Symbol {broker_sym} not found in MT5")

        if not raw.visible:
            if not self._mt5.symbol_select(broker_sym, True):
                msg = f"Symbol {broker_sym} could not be selected in Market Watch"
                raise TradeExecutionError(msg)
            raw = self._mt5.symbol_info(broker_sym)
            if raw is None or not raw.visible:
                raise TradeExecutionError(f"Symbol {broker_sym} failed to become visible")

        return SymbolInfo(
            name=raw.name,
            digits=int(raw.digits),
            point=float(raw.point),
            spread=int(raw.spread),
            tick_value=float(raw.trade_tick_value),
            tick_size=float(raw.trade_tick_size),
            volume_min=float(raw.volume_min),
            volume_max=float(raw.volume_max),
            volume_step=float(raw.volume_step),
            visible=bool(raw.visible),
            filling_mode=int(getattr(raw, "filling_mode", 1)),
            bid=float(getattr(raw, "bid", 0.0)),
            ask=float(getattr(raw, "ask", 0.0)),
        )

    def get_server_utc_offset(self, symbol: str | None = None) -> float:
        """
        Derive broker server UTC offset in hours, or fall back to config.

        Args:
            symbol: Optional symbol to probe latest tick time.

        Returns:
            float: Offset in hours such that utc_time = server_time - (offset * 3600).
        """
        configured_offset = getattr(self._config.mode, "server_utc_offset_hours", 0.0)
        if not self._connected or self._mt5 is None:
            return float(configured_offset)

        probe_symbol = (
            self.resolve_broker_symbol(symbol)
            if symbol
            else self._config.symbols[0].broker_symbol
            if self._config.symbols
            else None
        )
        if probe_symbol:
            try:
                tick = self._mt5.symbol_info_tick(probe_symbol)
                tick_time = getattr(tick, "time", None)
                if isinstance(tick_time, (int, float)) and tick_time > 0:
                    now_utc = datetime.now(UTC).timestamp()
                    diff_s = float(tick_time) - now_utc
                    # Derive whole hours if within a plausible 48-hour window
                    if abs(diff_s) < 172800:
                        return float(round(diff_s / 3600.0))
            except Exception:
                pass

        return float(configured_offset)

    def get_currency_conversion_rate(
        self,
        from_currency: str,
        to_currency: str,
    ) -> float:
        """
        Get exchange rate to convert from_currency to to_currency.

        For example, from 'USD' to 'GBP' on an account with GBP deposit currency.
        If from_currency == to_currency, returns 1.0.

        Args:
            from_currency: Source currency (e.g. 'USD').
            to_currency: Target account currency (e.g. 'GBP').

        Returns:
            float: Exchange rate multiplier (target = source * rate).
        """
        src = from_currency.strip().upper()
        dst = to_currency.strip().upper()
        if not src or not dst or src == dst:
            return 1.0

        self._ensure_connected()
        # 1. Try direct pair, e.g. USDGBP
        direct_pair = f"{src}{dst}"
        direct_sym = self.resolve_broker_symbol(direct_pair)
        if self._mt5.symbol_select(direct_sym, True):
            tick = self._mt5.symbol_info_tick(direct_sym)
            bid = getattr(tick, "bid", None)
            if isinstance(bid, (int, float)) and bid > 0:
                return float(bid)

        # 2. Try inverse pair, e.g. GBPUSD
        inv_pair = f"{dst}{src}"
        inv_sym = self.resolve_broker_symbol(inv_pair)
        if self._mt5.symbol_select(inv_sym, True):
            tick = self._mt5.symbol_info_tick(inv_sym)
            ask = getattr(tick, "ask", None)
            if isinstance(ask, (int, float)) and ask > 0:
                return 1.0 / float(ask)

        logger.warning(
            "Could not find conversion pair for %s -> %s; falling back to 1.0",
            src,
            dst,
        )
        return 1.0

    def get_rates(
        self,
        symbol: str,
        timeframe: str,
        count: int,
        start_pos: int = 0,
    ) -> Any:
        """
        Fetch raw candle rates from MT5.

        Args:
            symbol: Symbol name.
            timeframe: Timeframe string.
            count: Number of bars to fetch.
            start_pos: Bar offset (0 starts at current bar).

        Returns:
            Numpy structured array of rates.
        """
        self._ensure_connected()
        broker_sym = self.resolve_broker_symbol(symbol)
        tf_const = self.map_timeframe(timeframe)
        return self._mt5.copy_rates_from_pos(broker_sym, tf_const, start_pos, count)

    def get_positions(
        self,
        symbol: str | None = None,
        magic: int | None = None,
    ) -> tuple[PositionInfo, ...]:
        """
        Get open positions optionally filtered by symbol and/or magic number.

        Args:
            symbol: Optional symbol filter.
            magic: Optional magic number filter.

        Returns:
            tuple[PositionInfo, ...]: Matching open positions.
        """
        self._ensure_connected()
        broker_sym = self.resolve_broker_symbol(symbol) if symbol else None
        if broker_sym:
            raw_positions = self._mt5.positions_get(symbol=broker_sym)
        else:
            raw_positions = self._mt5.positions_get()
        if raw_positions is None:
            return ()

        result: list[PositionInfo] = []
        offset_s = self.get_server_utc_offset(broker_sym) * 3600.0
        for p in raw_positions:
            if magic is not None and getattr(p, "magic", None) != magic:
                continue
            result.append(
                PositionInfo(
                    ticket=int(p.ticket),
                    symbol=str(p.symbol),
                    type=int(p.type),
                    volume=float(p.volume),
                    price_open=float(p.price_open),
                    sl=float(p.sl),
                    tp=float(p.tp),
                    price_current=float(getattr(p, "price_current", p.price_open)),
                    profit=float(getattr(p, "profit", 0.0)),
                    magic=int(getattr(p, "magic", 0)),
                    time=datetime.fromtimestamp(p.time - offset_s, tz=UTC),
                )
            )
        return tuple(result)

    def get_position(self, ticket: int) -> PositionInfo | None:
        """Get a single position by its broker ticket number."""
        self._ensure_connected()
        raw_positions = self._mt5.positions_get(ticket=ticket)
        if not raw_positions:
            return None
        p = raw_positions[0]
        offset_s = self.get_server_utc_offset(str(p.symbol)) * 3600.0
        return PositionInfo(
            ticket=int(p.ticket),
            symbol=str(p.symbol),
            type=int(p.type),
            volume=float(p.volume),
            price_open=float(p.price_open),
            sl=float(p.sl),
            tp=float(p.tp),
            price_current=float(getattr(p, "price_current", p.price_open)),
            profit=float(getattr(p, "profit", 0.0)),
            magic=int(getattr(p, "magic", 0)),
            time=datetime.fromtimestamp(p.time - offset_s, tz=UTC),
        )

    def send_market_order(
        self,
        request: TradeRequest,
        deviation: int = 20,
        max_retries: int = 3,
        initial_backoff_s: float = 0.5,
    ) -> OrderResult:
        """
        Send a market order with SL set at order time.

        Retries on Requote/Price Changed. On timeout, checks open positions
        to prevent sending duplicate orders.

        Args:
            request: TradeRequest with entry, SL, lot, and symbol.
            deviation: Price slippage tolerance in points.
            max_retries: Max retry attempts on transient broker errors.
            initial_backoff_s: Initial delay between retries.

        Returns:
            OrderResult: Result of the executed order.

        Raises:
            InsufficientMarginError: If broker reports not enough money.
            TradeExecutionError: If broker rejects order.
        """
        self._ensure_connected()
        broker_sym = self.resolve_broker_symbol(request.symbol)
        sym_info = self.get_symbol_info(request.symbol)

        is_buy = request.direction is Direction.LONG
        order_type = ORDER_TYPE_BUY if is_buy else ORDER_TYPE_SELL
        price = sym_info.ask if is_buy else sym_info.bid

        req_dict: dict[str, Any] = {
            "action": TRADE_ACTION_DEAL,
            "symbol": broker_sym,
            "volume": float(request.lot),
            "type": order_type,
            "price": float(price),
            "sl": float(request.sl_price),
            "deviation": deviation,
            "magic": self._config.magic_number,
            "comment": f"ATMR {request.exit_rule[:15]}",
            "type_time": 0,
            "type_filling": sym_info.filling_mode or 1,
        }

        delay = initial_backoff_s
        for attempt in range(1, max_retries + 1):
            res = self._mt5.order_send(req_dict)
            if res is None:
                raise TradeExecutionError("order_send returned None from MT5")

            if res.retcode in (
                TRADE_RETCODE_DONE,
                TRADE_RETCODE_DONE_PARTIAL,
                TRADE_RETCODE_PLACED,
            ):
                return self._build_result(res, req_dict)

            if res.retcode == TRADE_RETCODE_TIMEOUT:
                logger.warning("Order timed out; checking positions to avoid duplicate...")
                matched = self._find_matching_position(broker_sym, order_type, request.lot)
                if matched is not None:
                    return OrderResult(
                        retcode=TRADE_RETCODE_DONE,
                        deal=matched.ticket,
                        order=matched.ticket,
                        volume=matched.volume,
                        price=matched.price_open,
                        comment="Recovered from timeout",
                        request=req_dict,
                    )

            if res.retcode == TRADE_RETCODE_NO_MONEY:
                raise InsufficientMarginError(f"Insufficient margin: {res.comment}")

            if res.retcode in (
                TRADE_RETCODE_REQUOTE,
                TRADE_RETCODE_PRICE_CHANGED,
                TRADE_RETCODE_PRICE_OFF,
                TRADE_RETCODE_TIMEOUT,
            ):
                logger.warning(
                    "Order retryable error (%d: %s), attempt %d/%d",
                    res.retcode,
                    res.comment,
                    attempt,
                    max_retries,
                )
                if attempt < max_retries:
                    self._sleep(delay)
                    delay *= 1.5
                    fresh = self.get_symbol_info(request.symbol)
                    req_dict["price"] = fresh.ask if is_buy else fresh.bid
                    continue

            raise TradeExecutionError(f"Order send failed ({res.retcode}): {res.comment}")

        raise TradeExecutionError(f"Order send exhausted {max_retries} retries")

    def _find_matching_position(
        self,
        broker_symbol: str,
        order_type: int,
        lot: float,
    ) -> PositionInfo | None:
        """Find a recently created position matching the requested parameters."""
        positions = self.get_positions(broker_symbol, magic=self._config.magic_number)
        now_ts = datetime.now(UTC).timestamp()
        for p in positions:
            if p.type == order_type and abs(p.volume - lot) < 1e-4:
                if abs(now_ts - p.time.timestamp()) < 60:
                    return p
        return None

    def modify_position(
        self,
        ticket: int,
        sl: float,
        tp: float | None = None,
        max_retries: int = 3,
    ) -> OrderResult:
        """
        Modify Stop Loss and optional Take Profit of an existing position.

        Args:
            ticket: Position ticket number.
            sl: New stop loss price.
            tp: Optional new take profit price.
            max_retries: Retry attempts on transient errors.

        Returns:
            OrderResult: Result of modification.
        """
        self._ensure_connected()
        req_dict: dict[str, Any] = {
            "action": TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": float(sl),
            "tp": float(tp) if tp is not None else 0.0,
        }
        return self._execute_request(req_dict, max_retries)

    def close_position(
        self,
        ticket: int,
        lot: float | None = None,
        deviation: int = 20,
        max_retries: int = 3,
    ) -> OrderResult:
        """
        Close an open position fully or partially.

        Args:
            ticket: Position ticket.
            lot: Optional volume to close (None closes full volume).
            deviation: Slippage tolerance.
            max_retries: Max retry count.

        Returns:
            OrderResult: Result of close order.
        """
        self._ensure_connected()
        pos = self.get_position(ticket)
        if pos is None:
            raise TradeExecutionError(f"Position {ticket} not found to close")

        sym_info = self.get_symbol_info(pos.symbol)
        is_buy = pos.type == ORDER_TYPE_BUY
        close_type = ORDER_TYPE_SELL if is_buy else ORDER_TYPE_BUY
        close_price = sym_info.bid if is_buy else sym_info.ask
        close_vol = lot if lot is not None else pos.volume

        req_dict: dict[str, Any] = {
            "action": TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": float(close_vol),
            "type": close_type,
            "price": float(close_price),
            "deviation": deviation,
            "magic": self._config.magic_number,
            "comment": "ATMR Close",
            "type_time": 0,
            "type_filling": sym_info.filling_mode or 1,
        }
        return self._execute_request(req_dict, max_retries)

    def cancel_order(self, ticket: int, max_retries: int = 3) -> OrderResult:
        """Cancel a pending order."""
        self._ensure_connected()
        req_dict: dict[str, Any] = {
            "action": TRADE_ACTION_REMOVE,
            "order": ticket,
        }
        return self._execute_request(req_dict, max_retries)

    def _execute_request(self, req_dict: dict[str, Any], max_retries: int) -> OrderResult:
        """Execute request with retries on transient errors."""
        delay = 0.5
        for attempt in range(1, max_retries + 1):
            res = self._mt5.order_send(req_dict)
            if res is None:
                raise TradeExecutionError("order_send returned None")

            if res.retcode in (
                TRADE_RETCODE_DONE,
                TRADE_RETCODE_DONE_PARTIAL,
                TRADE_RETCODE_PLACED,
            ):
                return self._build_result(res, req_dict)

            if res.retcode in (
                TRADE_RETCODE_REQUOTE,
                TRADE_RETCODE_PRICE_CHANGED,
                TRADE_RETCODE_TIMEOUT,
            ):
                if attempt < max_retries:
                    self._sleep(delay)
                    delay *= 1.5
                    continue

            raise TradeExecutionError(f"Order action failed ({res.retcode}): {res.comment}")

        raise TradeExecutionError(f"Order action exhausted {max_retries} retries")

    def _build_result(self, res: Any, req_dict: dict[str, Any]) -> OrderResult:
        """Construct immutable OrderResult."""
        return OrderResult(
            retcode=int(res.retcode),
            deal=int(getattr(res, "deal", 0)),
            order=int(getattr(res, "order", 0)),
            volume=float(getattr(res, "volume", 0.0)),
            price=float(getattr(res, "price", 0.0)),
            comment=str(getattr(res, "comment", "")),
            request=req_dict,
        )
