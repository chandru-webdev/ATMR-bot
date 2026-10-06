"""Logging configuration and UTC formatting.

Per CODE_STYLE.md and DESIGN_SYSTEM.md:
- Standard log format:
  `2026-09-28 14:05:01Z - [StrategyEngine] - INFO - Signal detected: LONG`
- Timestamps in UTC with trailing 'Z'.
- Weekly log rotation keeping 8 weeks, with file size cap.
- Redacting filter attached to all handlers to prevent secret leakage.
- Startup console banner.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from atmr.utils.redact import RedactingFilter, mask_account, redact_text

DEFAULT_LOG_FORMAT = "%(asctime)sZ - [%(name)s] - %(levelname)s - %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB size cap
DEFAULT_BACKUP_COUNT = 8  # 8 weeks retention


class UTCFormatter(logging.Formatter):
    """Log formatter that always uses UTC time and appends 'Z' to timestamps."""

    def __init__(
        self,
        fmt: str = DEFAULT_LOG_FORMAT,
        datefmt: str = DEFAULT_DATE_FORMAT,
    ) -> None:
        """Initialize formatter with UTC time conversion.

        Args:
            fmt: Log line template string.
            datefmt: Date format string (omits trailing Z, which is in fmt).
        """
        super().__init__(fmt=fmt, datefmt=datefmt)

    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Format record creation time in UTC.

        Args:
            record: LogRecord being formatted.
            datefmt: Optional date format override.

        Returns:
            str: Formatted UTC timestamp.
        """
        utc_struct = time.gmtime(record.created)
        fmt = datefmt or self.datefmt or DEFAULT_DATE_FORMAT
        return time.strftime(fmt, utc_struct)

    def format(self, record: logging.LogRecord) -> str:
        """Format record and sanitize exception information if present.

        Args:
            record: LogRecord being formatted.

        Returns:
            str: Formatted log line.
        """
        formatted = super().format(record)
        return redact_text(formatted)


class WeeklySizeRotatingFileHandler(TimedRotatingFileHandler):
    """Timed rotating file handler with weekly rollover and a max size cap.

    Per SECURITY.md: rotate weekly, with a size cap, and keep 8 weeks.
    """

    def __init__(
        self,
        filename: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        encoding: str = "utf-8",
        when: str = "W0",  # Monday midnight
    ) -> None:
        """Initialize weekly rotating file handler with size cap.

        Args:
            filename: Path to log file.
            max_bytes: Maximum file size before rollover (default 10 MB).
            backup_count: Number of weekly backup files to keep (default 8).
            encoding: Text encoding for log file.
            when: Day/time interval for timed rollover ('W0' = Monday).
        """
        super().__init__(
            filename=filename,
            when=when,
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            utc=True,
        )
        self.max_bytes = max_bytes

    def shouldRollover(self, record: logging.LogRecord) -> int:
        """Check if time interval or file size triggers rollover.

        Args:
            record: LogRecord about to be emitted.

        Returns:
            int: 1 if rollover should happen, 0 otherwise.
        """
        # Time-based rollover check
        if super().shouldRollover(record):
            return 1

        # Size-based rollover check
        if self.max_bytes > 0:
            msg = f"{self.format(record)}\n"
            if self.stream is not None:
                self.stream.seek(0, 2)  # Seek to end of file
                if self.stream.tell() + len(msg.encode(self.encoding or "utf-8")) >= self.max_bytes:
                    return 1

        return 0


def setup_logging(
    logger_name: str | None = None,
    log_dir: str | Path | None = "logs",
    log_file: str = "atmr.log",
    level: int | str = "INFO",
    console: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    secrets: Iterable[str | int | None] | None = None,
) -> logging.Logger:
    """Configure structured UTC logging with secret redaction and rotation.

    Args:
        logger_name: Name of logger to configure, or None for root logger.
        log_dir: Directory where log files are stored, or None to disable file logs.
        log_file: Name of active log file.
        level: Minimum log level (e.g. 'DEBUG', 'INFO', logging.INFO).
        console: Whether to attach a console StreamHandler.
        max_bytes: Size cap for file rotation in bytes.
        backup_count: Number of rotated log archives to retain.
        secrets: Sensitive values to redact from log records.

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(logger_name)
    numeric_level = (
        getattr(logging, level.upper(), logging.INFO) if isinstance(level, str) else level
    )
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    logger.handlers.clear()

    redacting_filter = RedactingFilter(secrets=secrets)
    formatter = UTCFormatter()

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redacting_filter)
        logger.addHandler(console_handler)

    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_dest = log_path / log_file

        file_handler = WeeklySizeRotatingFileHandler(
            filename=str(file_dest),
            max_bytes=max_bytes,
            backup_count=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redacting_filter)
        logger.addHandler(file_handler)

    return logger


def format_startup_banner(
    version: str,
    mode: str,
    symbols: Sequence[str],
    timeframe: str,
    account: int | str | None,
    is_live: bool = False,
    kill_switch_active: bool = False,
) -> str:
    """Format standard startup banner matching DESIGN_SYSTEM.md section 7.

    Args:
        version: Project version, e.g. '2.0.0'.
        mode: Trading mode, 'demo' or 'live'.
        symbols: Tradable canonical symbols.
        timeframe: Primary timeframe, e.g. 'H1'.
        account: MT5 login/account number.
        is_live: True if running in live trading mode.
        kill_switch_active: True if kill switch state is triggered.

    Returns:
        str: Multi-line startup banner string.
    """
    masked_acc = mask_account(account)
    sym_str = ", ".join(symbols)
    ks_state = "on" if kill_switch_active else "off"

    banner_header = (
        f"ATMR-Bot version {version} | mode={mode} | symbols={sym_str} | "
        f"timeframe={timeframe} | account={masked_acc} | kill switch: {ks_state}"
    )
    lines = [banner_header]

    if is_live:
        lines.append(
            "=" * 70
            + "\n*** WARNING: RUNNING IN LIVE TRADING MODE - REAL CAPITAL AT RISK ***\n"
            + "=" * 70
        )

    return "\n".join(lines)
