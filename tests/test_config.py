"""Unit tests for config loading. Never touches a live .env or MT5."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from atmr.config import HARD_MAX_DAILY_LOSS_PCT, load_config
from atmr.exceptions import ConfigError, LiveModeGuardError

VALID_ENV = """\
MT5_LOGIN=12345678
MT5_PASSWORD=demo-password
MT5_SERVER=MetaQuotes-Demo
TRADING_MODE=demo
N8N_BASE_URL=https://example.n8n.host/webhook
X_BOT_API_KEY=abcdefghijklmnopqrstuvwxyz012345
TELEGRAM_BOT_TOKEN=123456:fake-telegram-token
TELEGRAM_CHAT_ID=999999
"""


def _write_env(path: Path, extra: str = "") -> Path:
    path.write_text(VALID_ENV + extra, encoding="utf-8")
    return path


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _base_yaml() -> dict:
    return {
        "mode": {
            "trading_mode": "demo",
            "primary_timeframe": "H1",
            "poll_seconds": 30,
            "command_poll_seconds": 5,
        },
        "symbols": [
            {
                "canonical": "XAUUSD",
                "broker_symbol": "XAUUSD",
                "max_spread_points": 50,
            }
        ],
        "indicators": {
            "ema_period": 200,
            "rsi_period": 14,
            "bb_period": 20,
            "bb_stddev": 2,
            "atr_period": 14,
        },
        "strategy": {
            "rsi_oversold": 35,
            "rsi_overbought": 70,
            "touch_lookback": 1,
        },
        "exits": {
            "tp_rsi_low": 50,
            "tp_rsi_high": 55,
            "time_stop_candles": 15,
        },
        "risk": {
            "risk_pct_demo": 0.5,
            "risk_pct_live": 1.0,
            "risk_pct_live_initial": 0.25,
            "sl_atr_multiplier": 1.5,
            "min_reward_risk": 1.5,
            "trailing": {
                "breakeven_r": 1.0,
                "partial_close_r": 1.5,
                "partial_close_pct": 50,
                "trail_atr_multiplier": 2.0,
            },
            "limits": {
                "max_concurrent_trades": 2,
                "daily_loss_pct": 2.0,
                "weekly_loss_pct": 5.0,
            },
            "correlation_groups": [["EURUSD", "AUDUSD"]],
        },
        "sessions": {"enabled": False, "windows": []},
        "kill_switch": {
            "scope": "account",
            "confirm_timeout_s": 60,
            "command_ttl_s": 120,
        },
        "commands": {"ttl_s": 600},
        "notifications": {
            "timezone": "Asia/Kolkata",
            "heartbeat_minutes": 5,
            "daily_summary_hour_local": 23,
        },
        "magic_number": 20260928,
    }


def test_load_valid_config(tmp_path: Path) -> None:
    yaml_path = _write_yaml(tmp_path / "config.yaml", _base_yaml())
    env_path = _write_env(tmp_path / ".env")
    config = load_config(yaml_path, env_path)
    assert config.mode.trading_mode == "demo"
    assert config.canonical_symbols() == ("XAUUSD",)
    assert config.risk.limits.daily_loss_pct == HARD_MAX_DAILY_LOSS_PCT
    assert "***5678" in repr(config.secrets)
    assert "demo-password" not in repr(config.secrets)
    assert config.is_live() is False


def test_project_yaml_loads_with_temp_env(tmp_path: Path) -> None:
    project_yaml = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
    env_path = _write_env(tmp_path / ".env")
    config = load_config(project_yaml, env_path)
    assert config.canonical_symbols() == ("XAUUSD",)
    assert config.mode.primary_timeframe == "H1"


def test_missing_env_var(tmp_path: Path) -> None:
    yaml_path = _write_yaml(tmp_path / "config.yaml", _base_yaml())
    env_path = tmp_path / ".env"
    env_path.write_text("MT5_LOGIN=1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Missing required"):
        load_config(yaml_path, env_path)


def test_short_api_key(tmp_path: Path) -> None:
    yaml_path = _write_yaml(tmp_path / "config.yaml", _base_yaml())
    env_path = tmp_path / ".env"
    env_path.write_text(
        VALID_ENV.replace("abcdefghijklmnopqrstuvwxyz012345", "short-key"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="X_BOT_API_KEY"):
        load_config(yaml_path, env_path)


def test_n8n_url_must_be_https(tmp_path: Path) -> None:
    yaml_path = _write_yaml(tmp_path / "config.yaml", _base_yaml())
    env_path = tmp_path / ".env"
    env_path.write_text(
        VALID_ENV.replace("https://", "http://"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="HTTPS"):
        load_config(yaml_path, env_path)


def test_env_overrides_yaml_mode(tmp_path: Path) -> None:
    data = _base_yaml()
    data["mode"]["trading_mode"] = "live"
    yaml_path = _write_yaml(tmp_path / "config.yaml", data)
    env_path = _write_env(tmp_path / ".env")
    config = load_config(yaml_path, env_path)
    assert config.mode.trading_mode == "demo"


def test_live_mode_without_confirm(tmp_path: Path) -> None:
    yaml_path = _write_yaml(tmp_path / "config.yaml", _base_yaml())
    env_path = tmp_path / ".env"
    env_path.write_text(
        VALID_ENV.replace("TRADING_MODE=demo", "TRADING_MODE=live"),
        encoding="utf-8",
    )
    with pytest.raises(LiveModeGuardError, match="CONFIRM_LIVE"):
        load_config(yaml_path, env_path)


def test_live_mode_with_guard(tmp_path: Path) -> None:
    yaml_path = _write_yaml(tmp_path / "config.yaml", _base_yaml())
    env_path = tmp_path / ".env"
    env_path.write_text(
        VALID_ENV.replace("TRADING_MODE=demo", "TRADING_MODE=live")
        + "CONFIRM_LIVE=YES_I_UNDERSTAND\nLIVE_SERVER=Broker-Live\n",
        encoding="utf-8",
    )
    config = load_config(yaml_path, env_path)
    assert config.is_live() is True


def test_daily_loss_cannot_exceed_hard_cap(tmp_path: Path) -> None:
    data = _base_yaml()
    data["risk"]["limits"]["daily_loss_pct"] = 3.0
    yaml_path = _write_yaml(tmp_path / "config.yaml", data)
    env_path = _write_env(tmp_path / ".env")
    with pytest.raises(ConfigError, match="daily_loss_pct"):
        load_config(yaml_path, env_path)


def test_max_trades_cannot_exceed_hard_cap(tmp_path: Path) -> None:
    data = _base_yaml()
    data["risk"]["limits"]["max_concurrent_trades"] = 3
    yaml_path = _write_yaml(tmp_path / "config.yaml", data)
    env_path = _write_env(tmp_path / ".env")
    with pytest.raises(ConfigError, match="max_concurrent_trades"):
        load_config(yaml_path, env_path)


def test_empty_symbols_rejected(tmp_path: Path) -> None:
    data = _base_yaml()
    data["symbols"] = []
    yaml_path = _write_yaml(tmp_path / "config.yaml", data)
    env_path = _write_env(tmp_path / ".env")
    with pytest.raises(ConfigError, match="symbols"):
        load_config(yaml_path, env_path)


def test_invalid_timeframe(tmp_path: Path) -> None:
    data = _base_yaml()
    data["mode"]["primary_timeframe"] = "M5"
    yaml_path = _write_yaml(tmp_path / "config.yaml", data)
    env_path = _write_env(tmp_path / ".env")
    with pytest.raises(ConfigError, match="primary_timeframe"):
        load_config(yaml_path, env_path)


def test_missing_yaml_file(tmp_path: Path) -> None:
    env_path = _write_env(tmp_path / ".env")
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml", env_path)


def test_server_utc_offset_hours_valid_and_bounds(tmp_path: Path) -> None:
    data = _base_yaml()
    data["mode"]["server_utc_offset_hours"] = 2.0
    yaml_path = _write_yaml(tmp_path / "config.yaml", data)
    env_path = _write_env(tmp_path / ".env")
    cfg = load_config(yaml_path, env_path)
    assert cfg.mode.server_utc_offset_hours == 2.0

    # Test out of bounds offset (> 14)
    data["mode"]["server_utc_offset_hours"] = 15.0
    yaml_path = _write_yaml(tmp_path / "config.yaml", data)
    with pytest.raises(ConfigError, match="server_utc_offset_hours must be between -14 and 14"):
        load_config(yaml_path, env_path)

