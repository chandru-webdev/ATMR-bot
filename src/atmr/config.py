"""
Load `.env` and `config/config.yaml`, validate them, and fail at startup.

Strategy and risk numbers come from YAML. Secrets come from the environment
only. Config may tighten hard safety caps; it may never loosen them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, load_dotenv

from atmr.exceptions import ConfigError, LiveModeGuardError

# Values that must never be configurable (CODE_STYLE.md section 9).
EXIT_KILLED = 10
CONFIRM_LIVE_PHRASE = "YES_I_UNDERSTAND"
HARD_MAX_DAILY_LOSS_PCT = 2.0
HARD_MAX_WEEKLY_LOSS_PCT = 5.0
HARD_MAX_CONCURRENT_TRADES = 2
HARD_MAX_RISK_PCT_LIVE = 1.0
HARD_MAX_RISK_PCT_DEMO = 1.0
MIN_API_KEY_LENGTH = 32

ALLOWED_MODES = frozenset({"demo", "live"})
ALLOWED_TIMEFRAMES = frozenset({"M1", "M15", "H1", "H4", "D1"})
ALLOWED_KILL_SCOPES = frozenset({"account", "bot"})
REQUIRED_ENV = (
    "MT5_LOGIN",
    "MT5_PASSWORD",
    "MT5_SERVER",
    "TRADING_MODE",
    "N8N_BASE_URL",
    "X_BOT_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)
_CANONICAL_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_ENV_PATH = _PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class Secrets:
    """Credentials loaded from the environment. Never log this object."""

    mt5_login: int = field(repr=False)
    mt5_password: str = field(repr=False)
    mt5_server: str
    n8n_base_url: str
    x_bot_api_key: str = field(repr=False)
    telegram_bot_token: str = field(repr=False)
    telegram_chat_id: str = field(repr=False)
    confirm_live: str = field(repr=False, default="")
    live_server: str = field(repr=False, default="")
    email_user: str = field(repr=False, default="")
    email_pass: str = field(repr=False, default="")
    email_to: str = field(repr=False, default="")

    def __repr__(self) -> str:
        """Return a redacted representation that omits credentials."""
        login = str(self.mt5_login)
        masked = f"***{login[-4:]}" if len(login) >= 4 else "****"
        return (
            f"Secrets(mt5_login={masked!r}, mt5_server={self.mt5_server!r}, "
            f"n8n_base_url={self.n8n_base_url!r})"
        )


@dataclass(frozen=True)
class ModeConfig:
    """Runtime mode and polling intervals."""

    trading_mode: str
    primary_timeframe: str
    poll_seconds: int
    command_poll_seconds: int
    server_utc_offset_hours: float = 0.0


@dataclass(frozen=True)
class SymbolConfig:
    """One tradable symbol and its broker mapping (D8)."""

    canonical: str
    broker_symbol: str
    max_spread_points: int


@dataclass(frozen=True)
class IndicatorConfig:
    """Indicator lookbacks from YAML."""

    ema_period: int
    rsi_period: int
    bb_period: int
    bb_stddev: float
    atr_period: int


@dataclass(frozen=True)
class StrategyConfig:
    """Entry thresholds from YAML (D2)."""

    rsi_oversold: float
    rsi_overbought: float
    touch_lookback: int


@dataclass(frozen=True)
class ExitsConfig:
    """Full-exit RSI band and time stop. 1.5R is not a full TP (D1)."""

    tp_rsi_low: float
    tp_rsi_high: float
    time_stop_candles: int


@dataclass(frozen=True)
class TrailingConfig:
    """Staged trailing: breakeven, 50% partial, ATR trail (D1)."""

    breakeven_r: float
    partial_close_r: float
    partial_close_pct: float
    trail_atr_multiplier: float


@dataclass(frozen=True)
class LimitsConfig:
    """Loss limits and max concurrent trades. Caps cannot be loosened."""

    max_concurrent_trades: int
    daily_loss_pct: float
    weekly_loss_pct: float


@dataclass(frozen=True)
class RiskConfig:
    """Position sizing and trailing parameters."""

    risk_pct_demo: float
    risk_pct_live: float
    risk_pct_live_initial: float
    sl_atr_multiplier: float
    min_reward_risk: float
    trailing: TrailingConfig
    limits: LimitsConfig
    correlation_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class SessionWindow:
    """A UTC trading window in HH:MM."""

    start: str
    end: str


@dataclass(frozen=True)
class SessionsConfig:
    """Optional session filter. Disabled means trade 24/5."""

    enabled: bool
    windows: tuple[SessionWindow, ...]


@dataclass(frozen=True)
class KillSwitchConfig:
    """Panic close scope and Telegram confirmation timing."""

    scope: str
    confirm_timeout_s: int
    command_ttl_s: int


@dataclass(frozen=True)
class CommandsConfig:
    """TTL for non-kill remote commands."""

    ttl_s: int


@dataclass(frozen=True)
class NotificationsConfig:
    """Display timezone and heartbeat cadence. Storage stays UTC."""

    timezone: str
    heartbeat_minutes: int
    daily_summary_hour_local: int


@dataclass(frozen=True)
class Config:
    """Fully validated bot configuration."""

    secrets: Secrets
    mode: ModeConfig
    symbols: tuple[SymbolConfig, ...]
    indicators: IndicatorConfig
    strategy: StrategyConfig
    exits: ExitsConfig
    risk: RiskConfig
    sessions: SessionsConfig
    kill_switch: KillSwitchConfig
    commands: CommandsConfig
    notifications: NotificationsConfig
    magic_number: int

    def is_live(self) -> bool:
        """Return True when the resolved trading mode is live."""
        return self.mode.trading_mode == "live"

    def canonical_symbols(self) -> tuple[str, ...]:
        """Return canonical symbol names in config order."""
        return tuple(symbol.canonical for symbol in self.symbols)


def load_config(
    config_path: Path | str | None = None,
    env_path: Path | str | None = None,
) -> Config:
    """
    Load and validate `.env` plus YAML config.

    Args:
        config_path: Path to `config.yaml`. Defaults to `config/config.yaml`.
        env_path: Path to `.env`. Defaults to the project `.env`.

    Returns:
        Config: Immutable validated settings.

    Raises:
        ConfigError: A file, field, or secret is missing or invalid.
        LiveModeGuardError: Live mode lacks the confirm phrase or `LIVE_SERVER`.
    """
    yaml_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    dotenv_path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    raw = _load_yaml(yaml_path)
    secrets = _load_secrets(dotenv_path)
    trading_mode = _resolve_trading_mode(raw)
    _enforce_live_guard(trading_mode, secrets)
    return _build_config(raw, secrets, trading_mode)


def _load_yaml(path: Path) -> dict[str, Any]:
    """
    Read YAML with SafeLoader.

    Args:
        path: Config file path.

    Returns:
        dict[str, Any]: Top-level mapping.

    Raises:
        ConfigError: The file is missing or is not a mapping.
    """
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config root must be a mapping: {path}")
    return loaded


def _load_secrets(env_path: Path) -> Secrets:
    """
    Load required environment variables from `.env`.

    Args:
        env_path: Path to the dotenv file.

    Returns:
        Secrets: Validated credentials.

    Raises:
        ConfigError: The file or a required variable is missing or invalid.
    """
    if not env_path.is_file():
        raise ConfigError(f".env file not found: {env_path}")
    file_vals = _read_dotenv(env_path)
    load_dotenv(env_path, override=True)
    missing = [name for name in REQUIRED_ENV if not file_vals.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
    login_raw = file_vals["MT5_LOGIN"]
    try:
        mt5_login = int(login_raw)
    except ValueError as exc:
        raise ConfigError("MT5_LOGIN must be an integer account number") from exc
    api_key = file_vals["X_BOT_API_KEY"]
    if len(api_key) < MIN_API_KEY_LENGTH:
        raise ConfigError(f"X_BOT_API_KEY must be at least {MIN_API_KEY_LENGTH} characters")
    n8n_url = file_vals["N8N_BASE_URL"].rstrip("/")
    if not n8n_url.lower().startswith("https://"):
        raise ConfigError("N8N_BASE_URL must use HTTPS")
    email_user = file_vals.get("EMAIL_USER", "")
    email_pass = file_vals.get("EMAIL_PASS", "")
    email_to = file_vals.get("EMAIL_TO", "")
    email_fields = (email_user, email_pass, email_to)
    if any(email_fields) and not all(email_fields):
        raise ConfigError("EMAIL_USER, EMAIL_PASS and EMAIL_TO must all be set together")
    return Secrets(
        mt5_login=mt5_login,
        mt5_password=file_vals["MT5_PASSWORD"],
        mt5_server=file_vals["MT5_SERVER"],
        n8n_base_url=n8n_url,
        x_bot_api_key=api_key,
        telegram_bot_token=file_vals["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=file_vals["TELEGRAM_CHAT_ID"],
        confirm_live=file_vals.get("CONFIRM_LIVE", ""),
        live_server=file_vals.get("LIVE_SERVER", ""),
        email_user=email_user,
        email_pass=email_pass,
        email_to=email_to,
    )


def _read_dotenv(env_path: Path) -> dict[str, str]:
    """
    Read key/value pairs from a dotenv file.

    Args:
        env_path: Path to `.env`.

    Returns:
        dict[str, str]: Non-empty stripped values from the file only.
    """
    parsed = dotenv_values(env_path)
    return {key: value.strip() for key, value in parsed.items() if value and value.strip()}


def _resolve_trading_mode(raw: Mapping[str, Any]) -> str:
    """
    Resolve mode from `.env` (overrides YAML). Default is never live.

    Args:
        raw: Parsed YAML root.

    Returns:
        str: `demo` or `live`.

    Raises:
        ConfigError: The value is not `demo` or `live`.
    """
    env_mode = os.getenv("TRADING_MODE", "").strip().lower()
    yaml_mode = str(_mapping(raw, "mode").get("trading_mode", "demo")).strip().lower()
    trading_mode = env_mode or yaml_mode or "demo"
    if trading_mode not in ALLOWED_MODES:
        raise ConfigError(f"trading_mode must be demo or live, got {trading_mode!r}")
    return trading_mode


def _enforce_live_guard(trading_mode: str, secrets: Secrets) -> None:
    """
    Refuse live mode unless the confirm phrase and live server are set.

    Args:
        trading_mode: Resolved mode.
        secrets: Loaded environment secrets.

    Raises:
        LiveModeGuardError: Live was requested without both guards.
    """
    if trading_mode != "live":
        return
    if secrets.confirm_live != CONFIRM_LIVE_PHRASE:
        raise LiveModeGuardError(
            "Live mode requires CONFIRM_LIVE=YES_I_UNDERSTAND (see SECURITY.md)"
        )
    if not secrets.live_server:
        raise LiveModeGuardError("Live mode requires LIVE_SERVER to match the broker server")


def _build_config(raw: Mapping[str, Any], secrets: Secrets, trading_mode: str) -> Config:
    """
    Parse YAML sections into a Config.

    Args:
        raw: Parsed YAML root.
        secrets: Validated secrets.
        trading_mode: Resolved demo/live mode.

    Returns:
        Config: Fully validated configuration.

    Raises:
        ConfigError: A section is missing or violates a hard cap.
    """
    mode_raw = _mapping(raw, "mode")
    risk_raw = _mapping(raw, "risk")
    trailing = _parse_trailing(_mapping(risk_raw, "trailing"))
    limits = _parse_limits(_mapping(risk_raw, "limits"))
    _validate_risk_pcts(risk_raw, limits)
    return Config(
        secrets=secrets,
        mode=ModeConfig(
            trading_mode=trading_mode,
            primary_timeframe=_timeframe(mode_raw.get("primary_timeframe")),
            poll_seconds=_positive_int(mode_raw, "poll_seconds"),
            command_poll_seconds=_positive_int(mode_raw, "command_poll_seconds"),
            server_utc_offset_hours=_offset_hours(mode_raw, "server_utc_offset_hours"),
        ),
        symbols=_parse_symbols(raw.get("symbols")),
        indicators=_parse_indicators(_mapping(raw, "indicators")),
        strategy=_parse_strategy(_mapping(raw, "strategy")),
        exits=_parse_exits(_mapping(raw, "exits")),
        risk=RiskConfig(
            risk_pct_demo=_positive_float(risk_raw, "risk_pct_demo"),
            risk_pct_live=_positive_float(risk_raw, "risk_pct_live"),
            risk_pct_live_initial=_positive_float(risk_raw, "risk_pct_live_initial"),
            sl_atr_multiplier=_positive_float(risk_raw, "sl_atr_multiplier"),
            min_reward_risk=_positive_float(risk_raw, "min_reward_risk"),
            trailing=trailing,
            limits=limits,
            correlation_groups=_parse_correlation(risk_raw.get("correlation_groups")),
        ),
        sessions=_parse_sessions(_mapping(raw, "sessions")),
        kill_switch=_parse_kill(_mapping(raw, "kill_switch")),
        commands=CommandsConfig(ttl_s=_positive_int(_mapping(raw, "commands"), "ttl_s")),
        notifications=_parse_notifications(_mapping(raw, "notifications")),
        magic_number=_positive_int(raw, "magic_number"),
    )


def _validate_risk_pcts(risk_raw: Mapping[str, Any], limits: LimitsConfig) -> None:
    """
    Enforce hard safety caps on risk and loss limits.

    Args:
        risk_raw: YAML `risk` mapping.
        limits: Parsed limit values.

    Raises:
        ConfigError: A cap is missing or looser than the hard maximum.
    """
    demo = _positive_float(risk_raw, "risk_pct_demo")
    live = _positive_float(risk_raw, "risk_pct_live")
    initial = _positive_float(risk_raw, "risk_pct_live_initial")
    if demo > HARD_MAX_RISK_PCT_DEMO:
        raise ConfigError(f"risk_pct_demo cannot exceed {HARD_MAX_RISK_PCT_DEMO}")
    if live > HARD_MAX_RISK_PCT_LIVE:
        raise ConfigError(f"risk_pct_live cannot exceed {HARD_MAX_RISK_PCT_LIVE}")
    if initial > live:
        raise ConfigError("risk_pct_live_initial cannot exceed risk_pct_live")
    if limits.daily_loss_pct > HARD_MAX_DAILY_LOSS_PCT:
        raise ConfigError(f"daily_loss_pct cannot exceed {HARD_MAX_DAILY_LOSS_PCT}")
    if limits.weekly_loss_pct > HARD_MAX_WEEKLY_LOSS_PCT:
        raise ConfigError(f"weekly_loss_pct cannot exceed {HARD_MAX_WEEKLY_LOSS_PCT}")
    if limits.max_concurrent_trades > HARD_MAX_CONCURRENT_TRADES:
        raise ConfigError(f"max_concurrent_trades cannot exceed {HARD_MAX_CONCURRENT_TRADES}")


def _parse_symbols(raw: object) -> tuple[SymbolConfig, ...]:
    """
    Parse the symbols list.

    Args:
        raw: YAML `symbols` value.

    Returns:
        tuple[SymbolConfig, ...]: At least one symbol.

    Raises:
        ConfigError: The list is empty or a row is invalid.
    """
    if not isinstance(raw, list) or not raw:
        raise ConfigError("symbols must be a non-empty list")
    symbols: list[SymbolConfig] = []
    seen: set[str] = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ConfigError(f"symbols[{index}] must be a mapping")
        canonical = str(row.get("canonical", "")).strip().upper()
        if not _CANONICAL_SYMBOL_RE.fullmatch(canonical):
            raise ConfigError(f"symbols[{index}].canonical must be uppercase without a slash")
        if canonical in seen:
            raise ConfigError(f"Duplicate canonical symbol: {canonical}")
        seen.add(canonical)
        broker = str(row.get("broker_symbol", "")).strip()
        if not broker:
            raise ConfigError(f"symbols[{index}].broker_symbol is required")
        spread = row.get("max_spread_points")
        if not isinstance(spread, int) or isinstance(spread, bool) or spread < 0:
            raise ConfigError(f"symbols[{index}].max_spread_points must be an int >= 0")
        symbols.append(
            SymbolConfig(canonical=canonical, broker_symbol=broker, max_spread_points=spread)
        )
    return tuple(symbols)


def _parse_indicators(raw: Mapping[str, Any]) -> IndicatorConfig:
    """Parse indicator periods."""
    bb_stddev = _positive_float(raw, "bb_stddev")
    return IndicatorConfig(
        ema_period=_positive_int(raw, "ema_period"),
        rsi_period=_positive_int(raw, "rsi_period"),
        bb_period=_positive_int(raw, "bb_period"),
        bb_stddev=bb_stddev,
        atr_period=_positive_int(raw, "atr_period"),
    )


def _parse_strategy(raw: Mapping[str, Any]) -> StrategyConfig:
    """Parse RSI entry thresholds and touch lookback."""
    oversold = _float_in_range(raw, "rsi_oversold", 0.0, 100.0)
    overbought = _float_in_range(raw, "rsi_overbought", 0.0, 100.0)
    if oversold >= overbought:
        raise ConfigError("rsi_oversold must be less than rsi_overbought")
    lookback = _positive_int(raw, "touch_lookback")
    return StrategyConfig(
        rsi_oversold=oversold,
        rsi_overbought=overbought,
        touch_lookback=lookback,
    )


def _parse_exits(raw: Mapping[str, Any]) -> ExitsConfig:
    """Parse remainder full-exit RSI band and time stop."""
    low = _float_in_range(raw, "tp_rsi_low", 0.0, 100.0)
    high = _float_in_range(raw, "tp_rsi_high", 0.0, 100.0)
    if low > high:
        raise ConfigError("tp_rsi_low cannot exceed tp_rsi_high")
    return ExitsConfig(
        tp_rsi_low=low,
        tp_rsi_high=high,
        time_stop_candles=_positive_int(raw, "time_stop_candles"),
    )


def _parse_trailing(raw: Mapping[str, Any]) -> TrailingConfig:
    """Parse staged trailing parameters."""
    pct = _positive_float(raw, "partial_close_pct")
    if pct > 100.0:
        raise ConfigError("partial_close_pct cannot exceed 100")
    breakeven = _positive_float(raw, "breakeven_r")
    partial_r = _positive_float(raw, "partial_close_r")
    if breakeven >= partial_r:
        raise ConfigError("breakeven_r must be less than partial_close_r")
    return TrailingConfig(
        breakeven_r=breakeven,
        partial_close_r=partial_r,
        partial_close_pct=pct,
        trail_atr_multiplier=_positive_float(raw, "trail_atr_multiplier"),
    )


def _parse_limits(raw: Mapping[str, Any]) -> LimitsConfig:
    """Parse concurrent-trade and drawdown limits."""
    max_trades = _positive_int(raw, "max_concurrent_trades")
    daily = _positive_float(raw, "daily_loss_pct")
    weekly = _positive_float(raw, "weekly_loss_pct")
    if daily > weekly:
        raise ConfigError("daily_loss_pct cannot exceed weekly_loss_pct")
    return LimitsConfig(
        max_concurrent_trades=max_trades,
        daily_loss_pct=daily,
        weekly_loss_pct=weekly,
    )


def _parse_correlation(raw: object) -> tuple[tuple[str, ...], ...]:
    """Parse correlation groups. Members may be inactive until those symbols return."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("correlation_groups must be a list of symbol lists")
    groups: list[tuple[str, ...]] = []
    for index, group in enumerate(raw):
        if not isinstance(group, list) or len(group) < 2:
            raise ConfigError(f"correlation_groups[{index}] must contain at least two symbols")
        names = tuple(str(item).strip().upper() for item in group)
        if any(not _CANONICAL_SYMBOL_RE.fullmatch(name) for name in names):
            raise ConfigError(f"correlation_groups[{index}] has an invalid canonical symbol")
        groups.append(names)
    return tuple(groups)


def _parse_sessions(raw: Mapping[str, Any]) -> SessionsConfig:
    """Parse optional UTC session windows."""
    enabled = bool(raw.get("enabled", False))
    windows_raw = raw.get("windows", [])
    if windows_raw is None:
        windows_raw = []
    if not isinstance(windows_raw, list):
        raise ConfigError("sessions.windows must be a list")
    windows: list[SessionWindow] = []
    for index, row in enumerate(windows_raw):
        if not isinstance(row, dict):
            raise ConfigError(f"sessions.windows[{index}] must be a mapping")
        start = str(row.get("start", "")).strip()
        end = str(row.get("end", "")).strip()
        if not _HHMM_RE.fullmatch(start) or not _HHMM_RE.fullmatch(end):
            raise ConfigError(f"sessions.windows[{index}] start/end must be HH:MM UTC")
        windows.append(SessionWindow(start=start, end=end))
    if enabled and not windows:
        raise ConfigError("sessions.enabled is true but no windows are defined")
    return SessionsConfig(enabled=enabled, windows=tuple(windows))


def _parse_kill(raw: Mapping[str, Any]) -> KillSwitchConfig:
    """Parse kill-switch scope and TTLs."""
    scope = str(raw.get("scope", "account")).strip().lower()
    if scope not in ALLOWED_KILL_SCOPES:
        raise ConfigError(f"kill_switch.scope must be account or bot, got {scope!r}")
    return KillSwitchConfig(
        scope=scope,
        confirm_timeout_s=_positive_int(raw, "confirm_timeout_s"),
        command_ttl_s=_positive_int(raw, "command_ttl_s"),
    )


def _parse_notifications(raw: Mapping[str, Any]) -> NotificationsConfig:
    """Parse notification timezone and cadence."""
    timezone = str(raw.get("timezone", "")).strip()
    if not timezone:
        raise ConfigError("notifications.timezone is required")
    hour = raw.get("daily_summary_hour_local")
    if not isinstance(hour, int) or isinstance(hour, bool) or hour < 0 or hour > 23:
        raise ConfigError("daily_summary_hour_local must be an int 0-23")
    return NotificationsConfig(
        timezone=timezone,
        heartbeat_minutes=_positive_int(raw, "heartbeat_minutes"),
        daily_summary_hour_local=hour,
    )


def _mapping(raw: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return a nested mapping or raise ConfigError."""
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"Missing or invalid config section: {key}")
    return value


def _timeframe(value: object) -> str:
    """Validate the primary timeframe."""
    text = str(value or "").strip().upper()
    if text not in ALLOWED_TIMEFRAMES:
        raise ConfigError(f"primary_timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}")
    return text


def _positive_int(raw: Mapping[str, Any], key: str) -> int:
    """Return a required int > 0."""
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} must be an integer > 0")
    return value


def _positive_float(raw: Mapping[str, Any], key: str) -> float:
    """Return a required number > 0."""
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or float(value) <= 0:
        raise ConfigError(f"{key} must be a number > 0")
    return float(value)


def _float_in_range(raw: Mapping[str, Any], key: str, low: float, high: float) -> float:
    """Return a required number inside [low, high]."""
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} must be a number")
    number = float(value)
    if number < low or number > high:
        raise ConfigError(f"{key} must be between {low} and {high}")
    return number


def _offset_hours(raw: Mapping[str, Any], key: str = "server_utc_offset_hours") -> float:
    """Return an optional server UTC offset in hours inside [-14.0, 14.0], default 0.0."""
    value = raw.get(key, 0.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} must be a number")
    number = float(value)
    if number < -14.0 or number > 14.0:
        raise ConfigError(f"{key} must be between -14 and 14")
    return number

