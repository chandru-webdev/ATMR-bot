"""Secret redaction and account masking utilities.

Per SECURITY.md and CODE_STYLE.md:
- Never log passwords, API keys, or Telegram bot tokens.
- Mask account numbers showing only the last 4 digits.
- Sanitize exception strings before logging.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

# Match Telegram bot token in URLs: https://api.telegram.org/bot<token>/...
_TELEGRAM_URL_TOKEN_RE = re.compile(
    r"(https?://api\.telegram\.org/bot)[0-9]+:[A-Za-z0-9_-]+",
    re.IGNORECASE,
)

# Match standalone Telegram bot tokens (e.g., 123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789)
_TELEGRAM_TOKEN_RE = re.compile(
    r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,50}\b",
)

# Match credentials embedded in URLs: https://user:password@host
_URL_CREDENTIALS_RE = re.compile(
    r"(https?://[^:\s]+:)([^@\s]+)(@[^\s]+)",
    re.IGNORECASE,
)

# Match Authorization: Bearer <token>
_BEARER_TOKEN_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}",
    re.IGNORECASE,
)

# Match common sensitive query parameters or key-value pairs
_SENSITIVE_PARAM_RE = re.compile(
    r"((?:api[_-]?key|secret|token|password|pass)=)[^&\s'\"]+",
    re.IGNORECASE,
)

REDACTED_STR = "[REDACTED]"
MIN_SECRET_LENGTH = 4


def mask_account(account: int | str | None) -> str:
    """Mask account number to reveal only the last 4 digits.

    Args:
        account: Account number as int or string.

    Returns:
        str: Masked account number, e.g. '***5678' or '****'.
    """
    if account is None:
        return "****"
    digits = str(account).strip()
    if len(digits) < 4:
        return "****"
    return f"***{digits[-4:]}"


def redact_text(
    text: str,
    secrets: Iterable[str | int | None] | None = None,
) -> str:
    """Redact known secret patterns and explicit secret values from text.

    Args:
        text: Input string that may contain sensitive credentials.
        secrets: Optional iterable of specific secret strings/integers to scrub.

    Returns:
        str: Sanitized string with sensitive information redacted.
    """
    if not text:
        return text

    # Redact Telegram bot tokens in URLs
    sanitized = _TELEGRAM_URL_TOKEN_RE.sub(r"\1[REDACTED]", text)

    # Redact standalone Telegram tokens
    sanitized = _TELEGRAM_TOKEN_RE.sub(REDACTED_STR, sanitized)

    # Redact basic auth URL credentials
    sanitized = _URL_CREDENTIALS_RE.sub(r"\1[REDACTED]\3", sanitized)

    # Redact Authorization: Bearer tokens
    sanitized = _BEARER_TOKEN_RE.sub(r"\1[REDACTED]", sanitized)

    # Redact query params / key-values
    sanitized = _SENSITIVE_PARAM_RE.sub(r"\1[REDACTED]", sanitized)

    # Redact explicitly provided secret values
    if secrets:
        for secret in secrets:
            if secret is None:
                continue
            secret_str = str(secret).strip()
            if len(secret_str) < MIN_SECRET_LENGTH:
                continue
            if secret_str in sanitized:
                sanitized = sanitized.replace(secret_str, REDACTED_STR)

    return sanitized


def redact_exception(
    exc: BaseException,
    secrets: Iterable[str | int | None] | None = None,
) -> str:
    """Convert an exception to a string and sanitize any secrets.

    Args:
        exc: Exception instance.
        secrets: Optional iterable of specific secret values to scrub.

    Returns:
        str: Redacted exception message.
    """
    msg = str(exc)
    return redact_text(msg, secrets=secrets)


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive credentials from log records.

    Inspects and sanitizes record.msg and record.args before formatting.
    """

    def __init__(
        self,
        name: str = "",
        secrets: Iterable[str | int | None] | None = None,
    ) -> None:
        """Initialize filter with optional known secrets.

        Args:
            name: Filter name.
            secrets: Optional collection of secret values to scrub.
        """
        super().__init__(name)
        self._secrets: set[str] = set()
        if secrets:
            for s in secrets:
                if s is not None and len(str(s).strip()) >= MIN_SECRET_LENGTH:
                    self._secrets.add(str(s).strip())

    def add_secret(self, secret: str | int | None) -> None:
        """Add a secret string to the redaction list.

        Args:
            secret: Sensitive string or int to register for redaction.
        """
        if secret is not None:
            val = str(secret).strip()
            if len(val) >= MIN_SECRET_LENGTH:
                self._secrets.add(val)

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize message and arguments in the log record.

        Args:
            record: LogRecord to inspect and sanitize.

        Returns:
            bool: Always True (does not drop records, only scrubs them).
        """
        # If record has args, format them first so secrets in args get scrubbed
        if record.args:
            try:
                record.msg = str(record.msg) % record.args
                record.args = ()
            except Exception:
                # If formatting fails, fallback to stringifying
                record.msg = str(record.msg)

        record.msg = redact_text(str(record.msg), secrets=self._secrets)

        if record.exc_text:
            record.exc_text = redact_text(record.exc_text, secrets=self._secrets)

        return True
