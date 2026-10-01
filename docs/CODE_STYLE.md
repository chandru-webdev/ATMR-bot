# Code Style Guide: ATMR-Bot

**Version:** 2.0.0 | **Status:** Draft | **Last updated:** 2026-09-28

## 1. Core Philosophy
The ATMR-Bot codebase must prioritise **Readability, Traceability and Type Safety**. In algorithmic trading, a hidden bug is a financial liability. When in doubt, the code **fails safe**: it does not open a trade.

## 2. Tooling
- Python 3.11+
- Formatter `black` (line length 100), linter `ruff`, type checker `mypy` (strict on `engine/`), tests `pytest`.
- Run before every commit: `ruff check . && black . && mypy src && pytest`.

## 3. Naming Conventions
We follow PEP 8, with specific rules for trading logic:

- **Variables and functions:** `snake_case`.
  - Good: `calculate_rsi()`, `current_price`, `is_trend_up`.
  - Bad: `CalculateRSI()`, `price_val`.
- **Classes:** `PascalCase`.
  - Good: `StrategyEngine`, `RiskManager`, `MT5Connector`.
- **Constants:** `UPPER_SNAKE_CASE`.
  - Good: `EXIT_KILLED = 10`, `HARD_MAX_DAILY_LOSS = 0.02`.
- **Private methods:** prefix internal class methods with a single underscore.
  - Good: `_update_indicators()`.
- **Booleans read as questions:** `is_new_candle`, `has_open_trade`.
- **Symbols** are uppercase strings without a slash (`"EURUSD"`); broker suffixes (such as `.m`) live in config, not in logic.

**Canonical class names**
| Agent | Module | Class |
|---|---|---|
| Ingestor | `connectors/data.py` | `DataIngestor` |
| Strategy | `engine/strategy.py` | `StrategyEngine` |
| Risk | `engine/risk.py` | `RiskManager` |
| Execution | `connectors/executor.py` | `ExecutionEngine` |
| MT5 access | `connectors/mt5_client.py` | `MT5Connector` |
| Command | `connectors/command_poller.py` | `CommandPoller` |
| Notifier | `connectors/notifier.py` | `Notifier` |
| State | `connectors/state.py` | `StateStore` |

## 4. Type Hinting (Mandatory)
To prevent type errors (for example multiplying a `str` price by a `float` lot size), **all function signatures must use type hints**.

- Bad: `def get_position_size(balance, risk):`
- Good:
```python
def get_position_size(balance: float, risk_percent: float) -> float:
    ...
```
- The real sizing function takes more inputs (SL distance, tick value, tick size, volume step); the rule is the same.
- Use `@dataclass(frozen=True)` for data passed between modules (`Signal`, `TradeRequest`, `Position`, `RiskDecision`, defined in `engine/models.py`).
- Round prices to the symbol's `digits` and volumes to `volume_step` in **one** helper, never inline.

## 5. Error Handling and Exceptions
Never use bare excepts (`except:`). They hide bugs. Every error is specific and handled.

- **Custom exceptions** live in `exceptions.py`:
  - `InsufficientMarginError`
  - `TradeExecutionError`
  - `ConnectivityError`
  - `ConfigError` (invalid config or missing secret, fail at startup)
  - `LiveModeGuardError` (live mode without the confirm flag)
  - `CommandValidationError` (malformed or unknown remote command)
  - `LimitLockedError` (action blocked by a loss-limit or kill lock)
- **The Rule of Recovery.** Every `try/except` must decide:
  1. Does the bot **Retry**?
  2. Does the bot **Alert** (through the Notifier, so n8n and Telegram hear about it)?
  3. Does the bot **Halt**? (stop opening new trades; open trades keep their SL. Closing positions happens only in the defined cases: the daily/weekly limits and `/kill_all`.)

| Situation | Retry | Alert | Halt |
|---|---|---|---|
| MT5 disconnect | Yes (backoff) | Warn, then critical if it persists | If not recovered |
| Requote / price changed | Yes (max 3) | Warn | No |
| Insufficient margin | No | Yes | No (skip the trade) |
| Invalid config at start | No | Log and exit | Yes (do not start) |
| Unexpected exception in the main loop | No | Yes (critical) | **Yes** (fail safe) |

- Never swallow an exception silently: at minimum log it with context.

## 6. Logging and Traceability
The bot must leave a "paper trail". Use the standard `logging` module.

- **Log levels**
  - `DEBUG`: detailed info for developers (`RSI is 34.2`).
  - `INFO`: normal operations (`Trade Opened: EURUSD`).
  - `WARNING`: non-critical issues (`Connection latency high`).
  - `ERROR`: failures (`Order Rejected by Broker`).
  - `CRITICAL`: the bot is halting (`Daily loss limit hit`, `Kill switch executed`).
- **Format:** every log line has a UTC timestamp, the module or class name and the level.
  - Example: `2026-09-28 14:05:01Z - [StrategyEngine] - INFO - Signal detected: LONG`
- **Never log secrets:** passwords, API keys and the Telegram token (it appears inside request URLs) must be redacted. Use `utils/redact.py` for every exception message that comes from an HTTP library. Mask account numbers (last 4 digits only).
- Logging is set up once in `utils/logging_setup.py` (rotation weekly with a size cap).

## 7. Documentation (Docstrings)
Every class and function must have a Google-style docstring explaining:
1. What the function does.
2. **Args:** the inputs and their types.
3. **Returns:** the output and its type.
4. **Raises:** any errors the function might throw.

Example:
```python
def calculate_atr(data: pd.DataFrame, period: int) -> float:
    """
    Calculates the Average True Range (ATR) for a given period.

    Args:
        data (pd.DataFrame): DataFrame containing OHLC price data.
        period (int): The lookback period for the ATR calculation.

    Returns:
        float: The calculated ATR value.

    Raises:
        ValueError: If the period is greater than the available data length.
    """
```
Comments explain **why**, not what. When behaviour changes, update the matching doc in `docs/` in the same commit.

## 8. Modular Structure
The code follows `ARCHITECTURE.md`. **No single file should exceed 500 lines**, and functions should stay under about 50 lines.

```
src/atmr/
  main.py                  # entry point (the loop)
  check_connection.py      # MT5 connection test script
  config.py                # loads config.yaml + .env
  exceptions.py
  engine/                  # pure logic
    models.py, indicators.py, strategy.py, risk.py
  connectors/              # everything that touches the outside world
    mt5_client.py, data.py, executor.py, state.py, notifier.py, command_poller.py
  backtest/                # custom backtest runner
  utils/                   # math helpers, logging_setup.py, redact.py, time helpers
```

**Dependency rules**
- `engine/` is **pure**: no file, network or MT5 access, no `datetime.now()` (time is passed in), no reading of `.env`. Configuration reaches it as plain parameters.
- `engine/` never imports from `connectors/`.
- `connectors/` may import `engine/` types and `utils/`.
- `utils/` imports nothing from the project.
- Only `connectors/mt5_client.py` imports `MetaTrader5`.
- `main.py` wires everything together.

**Concurrency:** the command poller runs in a background thread that only puts validated commands on a queue. The main loop applies them. No other shared mutable state between threads.

## 9. Configuration and Constants
- Strategy and risk values (RSI levels, risk %, ATR multipliers, limits, spreads, sessions) come from `config/config.yaml`. **No magic numbers in logic.**
- **Constants** are for values that must never be configurable: exit codes (`EXIT_KILLED = 10`), protocol version, event names, and **hard safety caps** (for example `HARD_MAX_DAILY_LOSS = 0.02`, `HARD_MAX_TRADES = 2`). Config may make a cap stricter, never looser; startup fails with `ConfigError` if it tries.

## 10. Testing Conventions
- One test file per module: `tests/test_<module>.py`; integration tests are marked `@pytest.mark.integration` and run on demo only.
- Write tests first for `risk.py`, `strategy.py` and command validation.
- Never send real orders from tests; mock `MT5Connector`.

## 11. Git
- Branches: `main` (stable), `feat/<name>`, `fix/<name>`.
- Commit messages follow Conventional Commits: `feat(risk): add weekly loss limit`, `fix(executor): retry on requote`.
- Small commits, with tests included.
- Never commit `.env`, logs, `venv/`, `*.db` or historical data.

## 12. Changelog
- **v2.0.0:** Merged your style guide. Kept: philosophy, PEP 8 naming, mandatory type hints, no bare excepts, custom exceptions, the Rule of Recovery, log levels and format, Google docstrings, 500-line limit, `engine/` `connectors/` `utils/` layout, your class names. Changed: constants split into config values and hard safety caps; log timestamps in UTC; "Kill-Switch" in the recovery rule became "Halt" (full close only in defined cases). Added: tooling, more exceptions, dependency and purity rules, concurrency rule, secret redaction, testing and Git conventions.
- **v1.0.0:** Original code style guide.
