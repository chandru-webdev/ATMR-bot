"""Trading-specific exceptions. No I/O lives in this module."""


class ATMRError(Exception):
    """Base error for ATMR-Bot."""


class ConfigError(ATMRError):
    """Invalid configuration or a missing secret; the process must not start."""


class LiveModeGuardError(ConfigError):
    """Live mode was requested without the confirm flag or a matching live server."""


class InsufficientMarginError(ATMRError):
    """The broker rejected an order because of insufficient margin."""


class TradeExecutionError(ATMRError):
    """An order send, modify, or close failed after allowed retries."""


class ConnectivityError(ATMRError):
    """MT5, n8n, or another required connection is unavailable."""


class CommandValidationError(ATMRError):
    """A remote command is unknown, expired, malformed, or not confirmed."""


class LimitLockedError(ATMRError):
    """An action is blocked by a daily, weekly, or kill lock."""
