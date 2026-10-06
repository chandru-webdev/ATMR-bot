"""Unit tests for atmr.utils.redact."""

from __future__ import annotations

import logging

from atmr.utils.redact import (
    RedactingFilter,
    mask_account,
    redact_exception,
    redact_text,
)


def test_mask_account_valid_int() -> None:
    """mask_account should reveal only the last 4 digits of an integer account."""
    assert mask_account(12345678) == "***5678"
    assert mask_account(5544332211) == "***2211"


def test_mask_account_valid_str() -> None:
    """mask_account should reveal only the last 4 digits of a string account."""
    assert mask_account("87654321") == "***4321"


def test_mask_account_short_or_none() -> None:
    """mask_account should return **** for short, empty, or None accounts."""
    assert mask_account(123) == "****"
    assert mask_account("99") == "****"
    assert mask_account("") == "****"
    assert mask_account(None) == "****"


def test_redact_telegram_token_in_url() -> None:
    """Telegram bot token in API URL must be replaced with [REDACTED]."""
    url = "https://api.telegram.org/bot123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789/sendMessage"
    redacted = redact_text(url)
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789" not in redacted
    assert "https://api.telegram.org/bot[REDACTED]/sendMessage" in redacted


def test_redact_standalone_telegram_token() -> None:
    """Standalone Telegram token matching bot pattern should be redacted."""
    raw = "Failed with token 987654321:AAFlkmzX910293_1209381029381209381 in response."
    redacted = redact_text(raw)
    assert "987654321:AAFlkmzX910293_1209381029381209381" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_url_credentials() -> None:
    """Basic auth credentials embedded in URLs must have password redacted."""
    url = "https://user:SuperSecretPass123@n8n.internal.server/webhook"
    redacted = redact_text(url)
    assert "SuperSecretPass123" not in redacted
    assert "https://user:[REDACTED]@n8n.internal.server/webhook" in redacted


def test_redact_bearer_token() -> None:
    """Authorization Bearer tokens must be redacted."""
    header = "Authorization: Bearer my_jwt_token_secret_value_1234567890"
    redacted = redact_text(header)
    assert "my_jwt_token_secret_value_1234567890" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_redact_query_param_secrets() -> None:
    """Sensitive query params like api_key, token, password must be redacted."""
    url = "https://example.com/api?api_key=secretkey12345678&action=test"
    redacted = redact_text(url)
    assert "secretkey12345678" not in redacted
    assert "api_key=[REDACTED]" in redacted


def test_redact_provided_secret_values() -> None:
    """Explicitly provided secret values should be redacted from text."""
    secret_key = "x-bot-key-32chars-minimum-length-now"
    msg = f"Request headers: {{'X-BOT-API-KEY': '{secret_key}'}}"
    redacted = redact_text(msg, secrets=[secret_key, "other_secret"])
    assert secret_key not in redacted
    assert "[REDACTED]" in redacted


def test_redact_skips_short_or_none_secrets() -> None:
    """Short strings (<= 3 chars) or None in secrets list should not blank normal words."""
    msg = "This is a normal message."
    redacted = redact_text(msg, secrets=["a", "is", None, ""])
    assert redacted == msg


def test_redact_exception() -> None:
    """redact_exception should strip tokens and secrets from exception messages."""
    token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    exc = RuntimeError(f"HTTP connection failed to https://api.telegram.org/bot{token}/getMe")
    result = redact_exception(exc)
    assert token not in result
    assert "https://api.telegram.org/bot[REDACTED]/getMe" in result


def test_redacting_filter_sanitizes_log_record() -> None:
    """RedactingFilter should sanitize record.msg and record.args."""
    token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    rf = RedactingFilter(secrets=["custom_secret_password"])

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg=f"Calling https://api.telegram.org/bot{token}/sendMessage with custom_secret_password",
        args=(),
        exc_info=None,
    )
    assert rf.filter(record) is True
    assert token not in record.msg
    assert "custom_secret_password" not in record.msg
    assert "[REDACTED]" in record.msg
