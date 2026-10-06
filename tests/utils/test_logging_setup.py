"""Unit tests for atmr.utils.logging_setup."""

from __future__ import annotations

import logging
from pathlib import Path

from atmr.utils.logging_setup import (
    UTCFormatter,
    WeeklySizeRotatingFileHandler,
    format_startup_banner,
    setup_logging,
)


def test_utc_formatter_structure_and_utc_time() -> None:
    """Formatter must format timestamp in UTC with trailing 'Z' and exact format."""
    formatter = UTCFormatter()
    # 2026-09-28 14:05:01 UTC is timestamp 1790604301
    epoch = 1790604301.0
    record = logging.LogRecord(
        name="StrategyEngine",
        level=logging.INFO,
        pathname="engine/strategy.py",
        lineno=42,
        msg="Signal detected: LONG",
        args=(),
        exc_info=None,
    )
    record.created = epoch
    formatted = formatter.format(record)

    expected = "2026-09-28 14:05:01Z - [StrategyEngine] - INFO - Signal detected: LONG"
    assert formatted == expected


def test_format_startup_banner_demo() -> None:
    """Banner for demo mode must contain version, mode, symbols, timeframe, account, KS."""
    banner = format_startup_banner(
        version="2.0.0",
        mode="demo",
        symbols=("XAUUSD", "EURUSD"),
        timeframe="H1",
        account=12345678,
        is_live=False,
        kill_switch_active=False,
    )
    assert "version 2.0.0" in banner
    assert "mode=demo" in banner
    assert "XAUUSD, EURUSD" in banner
    assert "timeframe=H1" in banner
    assert "account=***5678" in banner
    assert "kill switch: off" in banner
    assert "LIVE TRADING" not in banner


def test_format_startup_banner_live_warning() -> None:
    """Banner for live mode must display prominent live warning."""
    banner = format_startup_banner(
        version="2.0.0",
        mode="live",
        symbols=("XAUUSD",),
        timeframe="H1",
        account="87654321",
        is_live=True,
        kill_switch_active=False,
    )
    assert "mode=live" in banner
    assert "account=***4321" in banner
    assert "WARNING: RUNNING IN LIVE TRADING MODE - REAL CAPITAL AT RISK" in banner


def test_setup_logging_creates_file_and_logs(tmp_path: Path) -> None:
    """setup_logging should configure logger, write formatted logs to file, and redact secrets."""
    log_dir = tmp_path / "logs"
    secret_pass = "UltraSecretPassword999"

    logger = setup_logging(
        logger_name="test_logger",
        log_dir=log_dir,
        log_file="test_bot.log",
        level="INFO",
        console=False,
        secrets=[secret_pass],
    )

    logger.info("Bot starting with password %s", secret_pass)

    # Flush handlers
    for handler in logger.handlers:
        handler.flush()
        handler.close()

    log_file = log_dir / "test_bot.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert secret_pass not in content
    assert "[REDACTED]" in content
    assert "INFO - Bot starting with password [REDACTED]" in content


def test_weekly_size_rotating_handler_rollover_on_size(tmp_path: Path) -> None:
    """Handler should rollover when file size exceeds max_bytes."""
    log_file = tmp_path / "rollover.log"
    # Small size limit of 100 bytes to test size rollover quickly
    handler = WeeklySizeRotatingFileHandler(
        filename=str(log_file),
        max_bytes=100,
        backup_count=3,
        encoding="utf-8",
    )
    formatter = UTCFormatter()
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("rollover_test")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)

    # Write multiple lines to trigger size rollover
    for i in range(10):
        test_logger.info("Message line number %d exceeding buffer threshold", i)

    handler.flush()
    handler.close()
    test_logger.removeHandler(handler)

    # Rollover should have created at least one backup file
    backups = list(tmp_path.glob("rollover.log*"))
    assert len(backups) > 1
