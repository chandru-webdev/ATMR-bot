# System Architecture: ATMR-Bot Ecosystem

**Version:** 2.0.4 | **Status:** Draft | **Last updated:** 2026-09-28
Implements PRD v1.1.0.

## 1. System Overview
ATMR-Bot is a three-layer system:

1. **Execution Layer (Python + MT5):** indicator math, signals, risk checks, order execution, state. Runs on Windows next to the MT5 terminal.
2. **Orchestration Layer (n8n):** middleware. Receives events from the bot and routes them to Telegram; receives Telegram commands and queues them for the bot.
3. **Interface Layer (Telegram):** phone dashboard for alerts and a few safe remote commands.

```
                 (outbound only: bot -> n8n)
[MT5] <-> [Python ATMR-Bot] --POST events--> [n8n] --> [Telegram] --> Phone
               |    ^                          ^   |
               |    +---- GET commands --------+   |
               |         (bot polls the queue)     |
            [SQLite]                     Phone --> Telegram --> n8n (queues command)
```
The bot never opens a port to the internet. Every connection starts from the bot.

## 2. Modules (`src/atmr/`)
Dependency rule: `engine/` is pure and never imports from `connectors/`; `connectors/` do all I/O; `utils/` imports nothing from the project; `main.py` wires everything (see CODE_STYLE.md).

| Module | Main class | Role | Notes |
|---|---|---|---|
| `main.py` | | Entry point, main loop, wiring, shutdown, kill switch | Applies commands |
| `check_connection.py` | | MT5 connection test script | Run before the bot |
| `config.py` | `Config` | Loads `config.yaml` + `.env`, validates | Fails fast; hard caps enforced |
| `exceptions.py` | | Trading-specific errors | No I/O |
| `engine/models.py` | | Dataclasses: Signal, TradeRequest, Position, RiskDecision | No I/O |
| `engine/indicators.py` | | EMA, RSI, Bollinger Bands, ATR | Pure functions |
| `engine/strategy.py` | `StrategyEngine` | Entry/exit signal evaluation | Pure: DataFrame in, Signal out |
| `engine/risk.py` | `RiskManager` | Sizing, daily/weekly limits, correlation, max trades, trailing stages, veto | Pure, no MT5 calls |
| `connectors/mt5_client.py` | `MT5Connector` | Connect/reconnect, account and symbol info, `order_send` wrapper | Only file importing `MetaTrader5` |
| `connectors/data.py` | `DataIngestor` | Fetch OHLCV, drop the forming candle | Returns pandas DataFrames |
| `connectors/executor.py` | `ExecutionEngine` | Places, modifies, closes orders; retries; kill sequence | Uses `MT5Connector` |
| `connectors/state.py` | `StateStore` | SQLite: trades, stage, P/L stats, paused flag, outbox, seen commands | Survives restarts |
| `connectors/notifier.py` | `Notifier` | Sends events to n8n; outbox retry; direct Telegram fallback | Never blocks trading |
| `connectors/command_poller.py` | `CommandPoller` | Polls n8n for pending commands, validates, queues for the main loop | Background thread |
| `utils/` | | `logging_setup.py`, `redact.py`, time and math helpers | |
| `backtest/` | | Custom candle-by-candle backtest runner with a simulated broker | Reuses `strategy.py` and `risk.py` unchanged |

Keeping `engine/` free of MT5 and network calls is deliberate: it can be tested and backtested without a terminal.

## 3. Main Loop
1. Ensure MT5 is connected (reconnect with backoff).
2. Every `poll_seconds` (default 30) check for a **new closed candle** on the primary timeframe.
3. On a new candle: fetch data, compute indicators, evaluate signals per symbol.
4. `risk.py` validates: not paused, no limit lock, max trades, correlation (same direction), spread, session, kill switch.
5. `executor.py` sends the order with SL set at order time; save to SQLite; emit `TRADE_OPEN`.
6. For open trades every loop: check TP conditions, trailing stages, time stop; act and emit events. **These continue while paused.**
7. Process commands received from the poller (section 5).
8. Emit `HEARTBEAT` every 5 minutes; flush the outbox.

## 4. Layer Details
### 4.1 Execution Layer
- **Data Ingestor / Strategy Engine / Risk Manager / Executor** as in section 2.
- **Circuit breaker:** when the daily 2% limit is hit, the bot closes all trades, locks new entries until the next day, and emits `LIMIT_HIT`. Weekly 5% locks until the following week. A limit lock cannot be lifted by `/resume`.
- **Event Dispatcher (`notifier.py`):** HTTP POST of JSON events to n8n, with a persistent outbox for retries.

### 4.2 Orchestration Layer (n8n)
Four workflows:

| Workflow | Trigger | What it does |
|---|---|---|
| **W1 Event Receiver** | Webhook `POST /atmr/events` | Check `X-API-Key`; Switch on `event`; format text; send to Telegram. Save last heartbeat time. |
| **W2 Command Listener** | Telegram Trigger | Accept only your chat ID; parse `/status`, `/risk_check`, `/pause`, `/resume`, `/kill_all`; add command to the queue with an expiry; reply "queued". For `/kill_all`, first ask for `YES` within 60 seconds and queue `KILL_ALL` only after it. |
| **W3 Command Queue** | Webhook `GET /atmr/commands` | Check `X-API-Key`; return pending, unexpired commands. |
| **W4 Heartbeat Watchdog** | Schedule, every minute | If no heartbeat for 15 minutes, send "BOT OFFLINE" to Telegram (once, then repeat every 30 minutes). Also alerts if a `KILL_ALL` is not acknowledged within 2 minutes ("NOT executed, use the MT5 app"). |

The queue can be an n8n Data Table (or workflow static data). Commands are removed when the bot acknowledges them, or when they expire.

### 4.3 Interface Layer (Telegram)
**Alerts:** `TRADE_OPEN`, `TRADE_CLOSE`, `SL_MOVED`, `PARTIAL_CLOSE`, `LIMIT_HIT`, `ERROR`, `DAILY_SUMMARY`, `BOT_STATE`. Message formats are in DESIGN_SYSTEM.md.

**Commands (allowed set):**
| Command | Effect |
|---|---|
| `/status` | Reply with equity, balance, open positions, mode, paused state |
| `/risk_check` | Reply with daily and weekly drawdown %, limits, remaining room |
| `/pause` | Stop opening **new** trades. Open trades keep being managed (SL, trailing, time stop). |
| `/resume` | Allow new trades again (refused if a limit lock is active). This is how you "start" trading again after a pause. |
| `/kill_all` | Panic. n8n asks you to reply `YES` within 60 seconds; then the bot closes all positions, cancels pending orders, alerts you, and **stops the bot process**. See SECURITY.md section 4.2. |

Not allowed remotely: opening trades, changing risk values or limits. `/kill_all` is the only command that closes trades, and it needs the `YES` confirmation. If the bot or n8n is down, use the MT5 mobile app to close everything.

## 5. Command Flow (bot polls n8n)
```
You: /pause -> Telegram -> n8n W2 (checks chat ID, queues command, replies "queued")
Bot (every 5s): GET /atmr/commands -> receives [PAUSE]
Bot: validates, sets paused=true in SQLite, sends COMMAND_ACK + BOT_STATE
n8n W1 -> Telegram: "Bot paused. Open trades still managed."
```
Rules:
- **Delay:** up to `command_poll_seconds` (default 5).
- **Expiry (TTL):** each command expires after 10 minutes, so a stale `/resume` cannot fire hours later if the bot was offline.
- **Idempotency:** the bot stores processed `command_id`s; a repeated command is ignored.
- **Acknowledgement:** every command gets a `COMMAND_ACK` (`EXECUTED` or `REJECTED` with a reason).
- **The bot process itself must already be running.** Polling cannot start a stopped process. Run the bot as a Windows service (or startup task) with auto-restart on failure, and use `/pause` / `/resume` to control trading.
- **`/kill_all`:** after the YES confirmation the bot cancels pending orders, closes all positions, sends `KILL_RESULT`, saves `KILLED` in SQLite and exits with code 10. The service must take **no action** on exit code 10, and the next start needs `--acknowledge-kill` before trading resumes.

## 6. Communication Protocol
All messages are JSON over HTTPS with header `X-API-Key: <key>`, timeout 5 seconds. Timestamps are UTC ISO 8601.

### 6.1 Endpoints (n8n base URL from `.env`)
| Direction | Method + path | Purpose |
|---|---|---|
| Bot -> n8n | `POST /webhook/atmr/events` | Send an event |
| Bot -> n8n | `GET /webhook/atmr/commands` | Fetch pending commands |

### 6.2 Common Fields (every event)
| Field | Type | Meaning |
|---|---|---|
| `event` | string | Event name (table 6.4) |
| `event_id` | string | UUID, for de-duplication in n8n |
| `timestamp` | string | UTC ISO time |
| `mode` | string | `DEMO` or `LIVE` |
| `schema_version` | int | Currently `1` |

### 6.3 Example: TRADE_OPEN
```json
{
  "schema_version": 1,
  "event": "TRADE_OPEN",
  "event_id": "7b0c9d1e-4f6a-4c1b-9a53-2f1d6e8a1c10",
  "timestamp": "2026-09-28T10:00:00Z",
  "mode": "DEMO",
  "ticket": 123456789,
  "symbol": "XAUUSD",
  "type": "BUY",
  "lot": 0.10,
  "price": 2035.50,
  "sl": 2025.00,
  "tp": null,
  "exit_rule": "RSI 50-55 / middle BB / 1.5R",
  "risk_pct": 0.5
}
```
`tp` is optional because exits are dynamic (RSI, middle band, staged rules); it is only set if a fixed target exists.

### 6.4 Event Types (bot -> n8n)
| Event | Extra fields |
|---|---|
| `TRADE_OPEN` | ticket, symbol, type, lot, price, sl, tp?, exit_rule, risk_pct |
| `TRADE_CLOSE` | ticket, symbol, pnl, r_multiple, reason (`TP_RSI`, `TP_MID_BB`, `SL`, `TIME_STOP`, `LIMIT`, `KILL`, `MANUAL`), candles_held — note: 1.5R (D1a) triggers `PARTIAL_CLOSE`, not a `TRADE_CLOSE` reason, since the remainder keeps trailing |
| `SL_MOVED` | ticket, symbol, new_sl, stage |
| `PARTIAL_CLOSE` | ticket, symbol, lot_closed, pnl |
| `LIMIT_HIT` | limit (`DAILY`/`WEEKLY`), drawdown_pct, locked_until |
| `ERROR` | severity (`WARN`/`CRITICAL`), component, message |
| `HEARTBEAT` | paused, open_positions, mt5_connected, uptime_s |
| `DAILY_SUMMARY` | balance, equity, pnl, pnl_pct, trades, wins, losses |
| `BOT_STATE` | state (`PAUSED`/`RESUMED`/`LOCKED`/`STARTED`/`STOPPED`), reason |
| `STATUS_REPORT` | command_id, balance, equity, positions[], paused |
| `RISK_REPORT` | command_id, daily_dd_pct, weekly_dd_pct, daily_limit, weekly_limit, locked |
| `KILL_RESULT` | command_id, orders_cancelled, positions_closed, positions_failed, scope |
| `COMMAND_ACK` | command_id, status (`EXECUTED`/`REJECTED`), message |

### 6.5 Command Format (n8n -> bot, response of `GET /commands`)
```json
{
  "commands": [
    {
      "id": "cmd-2f9a1c",
      "command": "PAUSE",
      "issued_at": "2026-09-28T10:02:00Z",
      "expires_at": "2026-09-28T10:12:00Z",
      "source": "telegram"
    }
  ]
}
```
Allowed `command` values: `PAUSE`, `RESUME`, `STATUS`, `RISK_CHECK`, `KILL_ALL`. `KILL_ALL` is only queued after the `YES` confirmation, carries `confirmed_at`, and expires after 2 minutes. Anything else is rejected and reported with `COMMAND_ACK` / `REJECTED`.

## 7. Resilience
- **Heartbeat:** every 5 minutes; n8n W4 alerts "BOT OFFLINE" after 15 minutes of silence.
- **Outbox:** if a POST to n8n fails, the event is stored in SQLite and retried with backoff; order is preserved.
- **Direct Telegram fallback:** `ERROR` (critical) and `LIMIT_HIT` are also sent straight to the Telegram API, so an n8n outage cannot hide them.
- **SL on every order** at send time, so a bot crash never leaves an unprotected position.
- **Reconnect:** MT5 disconnects trigger reconnect with backoff and a `WARN` event.
- **Restart safety:** trades, stage, paused flag and processed command IDs are in SQLite; a restart never double-enters.

## 8. State (SQLite, local to the bot)
- `trades`: ticket, symbol, side, lot, entry, sl, stage, opened_at, closed_at, pnl, exit_reason, candles_open
- `daily_stats`: date, start_equity, pnl, trades
- `bot_state`: paused, lock_type (`DAILY`/`WEEKLY`/`KILLED`), locked_until
- `outbox`: event_id, payload, attempts, next_try_at
- `commands_seen`: command_id, status, processed_at
- `events`: timestamp, level, message (audit log)

SQLite is private to the bot. n8n never reads it; all data reaches n8n through events (n8n can keep history in a Google Sheet if wanted).

## 9. Key Design Decisions
- **Outbound-only networking:** no inbound port on the trading machine; commands are pulled, not pushed.
- **Closed candles only** for signals.
- **Magic number** tags bot orders; the bot manages only its own positions.
- **Position sizing** uses `trade_tick_value` / `trade_tick_size` and `volume_step`, not fixed pip values (needed for XAUUSD).
- **Retries:** on requote / price changed / timeout, up to 3 with backoff; never blindly on "no money" or "market closed".
- **Trading mode:** `TRADING_MODE=demo|live`, live needs the confirm flag (SECURITY.md).
- **Remote control is deliberately limited** to pause/resume and read-only reports.

## 10. Technology Stack
- Python 3.11+, `MetaTrader5`, `pandas`, `pandas_ta` (or TA-Lib), `requests` (or `httpx`), `python-dotenv`, `PyYAML`, `pytest`
- n8n (self-hosted on a Linux VPS with Docker, or n8n Cloud). The Telegram Trigger needs n8n reachable over public HTTPS.
- Telegram Bot API
- SQLite
- FastAPI is **not** needed in this design (no inbound API on the bot).

## 11. Deployment
- **Windows VPS:** MT5 + Python + bot. Auto-login MT5, auto-start on boot. Run the bot as a Windows service (NSSM) or startup task with restart on failure.
- **n8n host:** separate server or n8n Cloud.
- Logs in `logs/` with rotation.

## 12. Repo Layout
```
atmr-bot/
  docs/
  src/atmr/
    main.py
    check_connection.py
    config.py
    exceptions.py
    engine/        models.py, indicators.py, strategy.py, risk.py
    connectors/    mt5_client.py, data.py, executor.py, state.py, notifier.py, command_poller.py
    backtest/
    utils/         logging_setup.py, redact.py, helpers
  tests/
  config/config.yaml
  data/            (historical data, git-ignored)
  .env.example
  .gitignore
  requirements.txt
  pyproject.toml
  README.md
```

## 13. Changelog
- **v2.0.4:** Removed `TP_RR` as a `TRADE_CLOSE` reason (D1a makes 1.5R a `PARTIAL_CLOSE`, not a full exit); noted in docs/DECISIONS.md.
- **v2.0.3:** Adopted the `engine/` `connectors/` `utils/` folder layout and canonical class names from CODE_STYLE.md; added `check_connection.py`, `models.py`, `exceptions.py`.
- **v2.0.2:** Added the custom `backtest/` runner (reuses `strategy.py` and `risk.py`) to modules and repo layout.
- **v2.0.1:** Added `/kill_all` (YES confirmation, close all, cancel pending, stop process), `KILL_ALL` command and `KILL_RESULT` event, `KILLED` lock, no-restart rule.
- **v2.0.0:** Merged your ecosystem architecture. Added: three-layer model, n8n workflows W1-W4, Telegram commands (`/status`, `/risk_check`, `/pause`, `/resume`), communication protocol (section 6), command TTL/ack/idempotency, outbox, SQLite tables for bot state. Changed: commands use **bot-polls-n8n** (no open port), FastAPI dropped, "1% risk" replaced by configured risk from the PRD, `tp` made optional, SQLite kept private to the bot. `/stop_all` intentionally not included.
- **v1.0.0:** Original module breakdown and data flow.
