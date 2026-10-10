"""Unit tests for MT5Connector.

Validates lazy/injectable MT5 module, connection with exponential backoff,
symbol/account info retrieval, timeframe mapping, order execution retries,
timeout position verification, live-mode safety guards, and log sanitization.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

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
from atmr.connectors.mt5_client import (
    ACCOUNT_TRADE_MODE_DEMO,
    ACCOUNT_TRADE_MODE_REAL,
    ORDER_TYPE_BUY,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_MARKET_CLOSED,
    TRADE_RETCODE_NO_MONEY,
    TRADE_RETCODE_PRICE_CHANGED,
    TRADE_RETCODE_REQUOTE,
    TRADE_RETCODE_TIMEOUT,
    AccountInfo,
    MT5Connector,
    OrderResult,
    SymbolInfo,
)
from atmr.engine.models import Direction, TradeRequest
from atmr.exceptions import (
    ConnectivityError,
    InsufficientMarginError,
    LiveModeGuardError,
    TradeExecutionError,
)


def _make_config(*, trading_mode: str = "demo", password: str = "super_secret_pw") -> Config:
    """Build a test Config instance."""
    return Config(
        secrets=Secrets(
            mt5_login=12345678,
            mt5_password=password,
            mt5_server="Demo-Server",
            n8n_base_url="https://n8n.example.com",
            x_bot_api_key="a" * 32,
            telegram_bot_token="token:secret",
            telegram_chat_id="chat123",
            confirm_live="YES_I_UNDERSTAND" if trading_mode == "live" else "",
            live_server="Demo-Server" if trading_mode == "live" else "",
        ),
        mode=ModeConfig(trading_mode, "H1", 30, 5),
        symbols=(SymbolConfig("XAUUSD", "XAUUSD", 50), SymbolConfig("EURUSD", "EURUSD.m", 20)),
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
            (),
        ),
        sessions=SessionsConfig(False, ()),
        kill_switch=KillSwitchConfig("account", 60, 120),
        commands=CommandsConfig(600),
        notifications=NotificationsConfig("Asia/Kolkata", 5, 23),
        magic_number=20260928,
    )


def _mock_account_info(trade_mode: int = ACCOUNT_TRADE_MODE_DEMO) -> Any:
    """Create a mock MT5 account_info object."""
    mock = MagicMock()
    mock.login = 12345678
    mock.server = "Demo-Server"
    mock.balance = 10000.0
    mock.equity = 10000.0
    mock.currency = "USD"
    mock.margin_free = 9500.0
    mock.leverage = 100
    mock.trade_mode = trade_mode
    return mock


def _mock_symbol_info(name: str = "XAUUSD") -> Any:
    """Create a mock MT5 symbol_info object."""
    mock = MagicMock()
    mock.name = name
    mock.digits = 2
    mock.point = 0.01
    mock.spread = 25
    mock.trade_tick_value = 1.0
    mock.trade_tick_size = 0.01
    mock.volume_min = 0.01
    mock.volume_max = 100.0
    mock.volume_step = 0.01
    mock.visible = True
    mock.filling_mode = 1  # IOC
    mock.bid = 2650.0
    mock.ask = 2650.25
    return mock


def test_connect_success() -> None:
    """Test successful connection using injected mock MT5."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.initialize.return_value = True
    mock_mt5.account_info.return_value = _mock_account_info()

    connector = MT5Connector(config, mt5_module=mock_mt5)
    connector.connect()

    mock_mt5.initialize.assert_called_once_with(
        login=12345678,
        password="super_secret_pw",
        server="Demo-Server",
    )
    assert connector.is_connected() is True


def test_connect_backoff_and_exhaustion() -> None:
    """Test exponential backoff on initialize failure and ConnectivityError."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.initialize.return_value = False
    mock_mt5.last_error.return_value = (-10001, "Connection failed")

    sleep_mock = MagicMock()
    connector = MT5Connector(config, mt5_module=mock_mt5, sleep_fn=sleep_mock)

    with pytest.raises(ConnectivityError, match="Failed to connect to MT5 after 3 attempts"):
        connector.connect(max_attempts=3, initial_backoff_s=0.1, backoff_factor=2.0)

    assert mock_mt5.initialize.call_count == 3
    assert sleep_mock.call_count == 2
    assert sleep_mock.call_args_list[0][0][0] == pytest.approx(0.1)
    assert sleep_mock.call_args_list[1][0][0] == pytest.approx(0.2)


def test_secrets_never_logged_during_connection(caplog: pytest.LogCaptureFixture) -> None:
    """Verify passwords and raw account details never appear in log output."""
    pw = "SuperSecretPassword123!"
    config = _make_config(password=pw)
    mock_mt5 = MagicMock()
    mock_mt5.initialize.return_value = False
    mock_mt5.last_error.return_value = (-1, "Auth failure")

    connector = MT5Connector(config, mt5_module=mock_mt5, sleep_fn=lambda _: None)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ConnectivityError):
            connector.connect(max_attempts=1)

    log_text = caplog.text
    assert pw not in log_text
    assert "12345678" not in log_text
    assert "***5678" in log_text or "5678" in log_text


def test_live_mode_guard_blocks_real_account() -> None:
    """Refuse trading on a real MT5 account if TRADING_MODE is demo."""
    config = _make_config(trading_mode="demo")
    mock_mt5 = MagicMock()
    mock_mt5.initialize.return_value = True
    mock_mt5.account_info.return_value = _mock_account_info(trade_mode=ACCOUNT_TRADE_MODE_REAL)

    connector = MT5Connector(config, mt5_module=mock_mt5)

    with pytest.raises(LiveModeGuardError, match="Real trading account detected in demo mode"):
        connector.connect()

    mock_mt5.shutdown.assert_called_once()


def test_timeframe_mapping_valid_and_invalid() -> None:
    """Verify string to MT5 timeframe constant conversions."""
    config = _make_config()
    mock_mt5 = MagicMock()
    connector = MT5Connector(config, mt5_module=mock_mt5)

    assert connector.map_timeframe("M1") == 1
    assert connector.map_timeframe("M15") == 15
    assert connector.map_timeframe("H1") == 16385
    assert connector.map_timeframe("H4") == 16388
    assert connector.map_timeframe("D1") == 16408

    with pytest.raises(ValueError, match="Unsupported timeframe: M5"):
        connector.map_timeframe("M5")


def test_get_account_and_symbol_info() -> None:
    """Verify frozen dataclass outputs for account and symbol info."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.account_info.return_value = _mock_account_info()
    mock_mt5.symbol_info.return_value = _mock_symbol_info("XAUUSD")

    connector = MT5Connector(config, mt5_module=mock_mt5)
    acc = connector.get_account_info()
    assert isinstance(acc, AccountInfo)
    assert acc.balance == 10000.0
    assert acc.currency == "USD"

    sym = connector.get_symbol_info("XAUUSD")
    assert isinstance(sym, SymbolInfo)
    assert sym.name == "XAUUSD"
    assert sym.digits == 2
    assert sym.volume_step == 0.01


def test_get_symbol_info_handles_hidden_symbol() -> None:
    """Verify that a hidden symbol is selected in Market Watch."""
    config = _make_config()
    mock_mt5 = MagicMock()

    hidden_mock = _mock_symbol_info("EURUSD.m")
    hidden_mock.visible = False
    visible_mock = _mock_symbol_info("EURUSD.m")
    visible_mock.visible = True

    mock_mt5.symbol_info.side_effect = [hidden_mock, visible_mock]
    mock_mt5.symbol_select.return_value = True

    connector = MT5Connector(config, mt5_module=mock_mt5)
    sym = connector.get_symbol_info("EURUSD")

    mock_mt5.symbol_select.assert_called_once_with("EURUSD.m", True)
    assert sym.visible is True


def test_order_send_success_first_try() -> None:
    """Test successful market order execution."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.symbol_info.return_value = _mock_symbol_info()

    res_mock = MagicMock()
    res_mock.retcode = TRADE_RETCODE_DONE
    res_mock.deal = 99901
    res_mock.order = 88801
    res_mock.volume = 0.10
    res_mock.price = 2650.25
    res_mock.comment = "Request executed"
    mock_mt5.order_send.return_value = res_mock

    connector = MT5Connector(config, mt5_module=mock_mt5)
    req = TradeRequest("XAUUSD", Direction.LONG, 0.10, 2650.0, 2640.0, 0.5)

    result = connector.send_market_order(req)
    assert isinstance(result, OrderResult)
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.deal == 99901
    assert mock_mt5.order_send.call_count == 1


def test_order_send_retries_on_requote_and_price_changed() -> None:
    """Test that requote and price changed trigger retries with backoff."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.symbol_info.return_value = _mock_symbol_info()

    r1 = MagicMock(retcode=TRADE_RETCODE_REQUOTE, comment="Requote")
    r2 = MagicMock(retcode=TRADE_RETCODE_PRICE_CHANGED, comment="Price changed")
    r3 = MagicMock(
        retcode=TRADE_RETCODE_DONE,
        deal=123,
        order=456,
        volume=0.1,
        price=2650.5,
        comment="Done",
    )
    mock_mt5.order_send.side_effect = [r1, r2, r3]

    sleep_mock = MagicMock()
    connector = MT5Connector(config, mt5_module=mock_mt5, sleep_fn=sleep_mock)
    req = TradeRequest("XAUUSD", Direction.LONG, 0.10, 2650.0, 2640.0, 0.5)

    result = connector.send_market_order(req, max_retries=3)
    assert result.retcode == TRADE_RETCODE_DONE
    assert mock_mt5.order_send.call_count == 3
    assert sleep_mock.call_count == 2


def test_order_send_fails_fast_on_insufficient_margin() -> None:
    """No retry on insufficient margin; immediately raises InsufficientMarginError."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.symbol_info.return_value = _mock_symbol_info()

    r = MagicMock(retcode=TRADE_RETCODE_NO_MONEY, comment="Not enough money")
    mock_mt5.order_send.return_value = r

    connector = MT5Connector(config, mt5_module=mock_mt5)
    req = TradeRequest("XAUUSD", Direction.LONG, 1.0, 2650.0, 2640.0, 0.5)

    with pytest.raises(InsufficientMarginError, match="Not enough money"):
        connector.send_market_order(req)

    assert mock_mt5.order_send.call_count == 1


def test_order_send_fails_fast_on_market_closed() -> None:
    """No retry on market closed; raises TradeExecutionError immediately."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.symbol_info.return_value = _mock_symbol_info()

    r = MagicMock(retcode=TRADE_RETCODE_MARKET_CLOSED, comment="Market closed")
    mock_mt5.order_send.return_value = r

    connector = MT5Connector(config, mt5_module=mock_mt5)
    req = TradeRequest("XAUUSD", Direction.LONG, 0.1, 2650.0, 2640.0, 0.5)

    with pytest.raises(TradeExecutionError, match="Market closed"):
        connector.send_market_order(req)

    assert mock_mt5.order_send.call_count == 1


def test_timeout_recovers_without_duplicate_order() -> None:
    """Verify position check on timeout prevents sending duplicate orders."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.symbol_info.return_value = _mock_symbol_info()

    r_timeout = MagicMock(retcode=TRADE_RETCODE_TIMEOUT, comment="Request timeout")
    mock_mt5.order_send.return_value = r_timeout

    # Simulate position existing on the broker matching the request
    pos_mock = MagicMock()
    pos_mock.ticket = 77701
    pos_mock.symbol = "XAUUSD"
    pos_mock.type = ORDER_TYPE_BUY
    pos_mock.volume = 0.10
    pos_mock.price_open = 2650.25
    pos_mock.sl = 2640.0
    pos_mock.tp = 0.0
    pos_mock.price_current = 2650.30
    pos_mock.profit = 0.50
    pos_mock.magic = 20260928
    pos_mock.time = int(datetime.now(UTC).timestamp())

    mock_mt5.positions_get.return_value = (pos_mock,)

    connector = MT5Connector(config, mt5_module=mock_mt5)
    req = TradeRequest("XAUUSD", Direction.LONG, 0.10, 2650.0, 2640.0, 0.5)

    result = connector.send_market_order(req)
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.deal == 77701
    assert "timeout" in result.comment.lower()
    # Order was only sent once; positions_get prevented the duplicate
    assert mock_mt5.order_send.call_count == 1


def test_modify_position_and_close_position() -> None:
    """Test position modification and partial/full close operations."""
    config = _make_config()
    mock_mt5 = MagicMock()
    mock_mt5.symbol_info.return_value = _mock_symbol_info()

    # Setup modify SL
    mod_res = MagicMock(
        retcode=TRADE_RETCODE_DONE,
        deal=0,
        order=101,
        volume=0.0,
        price=0.0,
        comment="SL modified",
    )
    mock_mt5.order_send.return_value = mod_res

    connector = MT5Connector(config, mt5_module=mock_mt5)
    res_mod = connector.modify_position(ticket=101, sl=2645.0)
    assert res_mod.retcode == TRADE_RETCODE_DONE

    # Setup close position
    pos_mock = MagicMock()
    pos_mock.ticket = 101
    pos_mock.symbol = "XAUUSD"
    pos_mock.type = ORDER_TYPE_BUY
    pos_mock.volume = 0.20
    pos_mock.price_open = 2650.0
    mock_mt5.positions_get.return_value = (pos_mock,)

    close_res = MagicMock(
        retcode=TRADE_RETCODE_DONE,
        deal=202,
        order=101,
        volume=0.10,
        price=2660.0,
        comment="Partial close",
    )
    mock_mt5.order_send.return_value = close_res

    res_close = connector.close_position(ticket=101, lot=0.10)
    assert res_close.retcode == TRADE_RETCODE_DONE
    assert res_close.deal == 202


def test_get_server_utc_offset_derived_and_fallback() -> None:
    """Test deriving broker server offset from tick time vs UTC now, or fallback to config."""
    config = _make_config()
    mock_mt5 = MagicMock()
    connector = MT5Connector(config, mt5_module=mock_mt5)

    # When disconnected, returns configured offset
    assert connector.get_server_utc_offset() == 0.0

    # Connect with a tick 2 hours ahead of UTC
    connector.connect()
    now_utc_ts = datetime.now(UTC).timestamp()
    tick_mock = MagicMock()
    tick_mock.time = now_utc_ts + 7200  # +2 hours in broker time
    mock_mt5.symbol_info_tick.return_value = tick_mock

    assert connector.get_server_utc_offset("XAUUSD") == 2.0


def test_get_currency_conversion_rate() -> None:
    """Test currency conversion rate for direct and inverse currency pairs."""
    config = _make_config()
    mock_mt5 = MagicMock()
    connector = MT5Connector(config, mt5_module=mock_mt5)
    connector.connect()

    # Same currency returns 1.0 immediately
    assert connector.get_currency_conversion_rate("GBP", "GBP") == 1.0

    # Inverse pair lookup: USD -> GBP uses GBPUSD ask
    mock_mt5.symbol_select.side_effect = lambda sym, select: sym == "GBPUSD"
    tick_mock = MagicMock(ask=1.25, bid=1.249)
    mock_mt5.symbol_info_tick.return_value = tick_mock

    rate = connector.get_currency_conversion_rate("USD", "GBP")
    assert rate == pytest.approx(1.0 / 1.25, abs=1e-5)


def test_get_positions_normalizes_open_time_to_utc() -> None:
    """Test that open position times are converted to UTC using server offset."""
    config = _make_config()
    mock_mt5 = MagicMock()
    connector = MT5Connector(config, mt5_module=mock_mt5)
    connector.connect()

    # Broker tick indicates +2h server offset
    now_utc_ts = datetime.now(UTC).timestamp()
    mock_mt5.symbol_info_tick.return_value = MagicMock(time=now_utc_ts + 7200)

    # Position opened at broker timestamp 1791644400 (15:00 broker time)
    pos_mock = MagicMock()
    pos_mock.ticket = 101
    pos_mock.symbol = "XAUUSD"
    pos_mock.type = ORDER_TYPE_BUY
    pos_mock.volume = 0.20
    pos_mock.price_open = 2650.0
    pos_mock.sl = 2640.0
    pos_mock.tp = 2670.0
    pos_mock.time = 1791644400  # 15:00 in broker time
    mock_mt5.positions_get.return_value = (pos_mock,)

    positions = connector.get_positions()
    assert len(positions) == 1
    # 15:00 broker time - 2h offset = 13:00 UTC
    assert positions[0].time.hour == 13
    assert positions[0].time.tzinfo == UTC

