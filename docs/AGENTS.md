# AGENTS.md: ATMR-Bot

**Version:** 2.0.1 | **Status:** Draft | **Last updated:** 2026-09-28
Two parts: **Part A** defines the agents (decoupled modules) inside the system. **Part B** gives rules for AI coding assistants (Claude Code, Cursor, Copilot) working on the code. Copy this file to the repo root when coding starts, because AI tools look for `AGENTS.md` there.

---
# Part A: System Agents

Each agent is a decoupled module with one responsibility. Agents talk only through the flow below.

```
Ingestor -> Strategy -> Risk (veto) -> Execution -> MT5
                                          |
Notifier -> n8n Orchestrator -> Telegram  |
Command Agent <- (bot polls) n8n queue <- Telegram
```

| # | Agent | Nickname | Module | Runs in |
|---|---|---|---|---|
| 1 | Ingestor | The Senses | `connectors/data.py` | Bot |
| 2 | Strategy | The Brain | `engine/strategy.py`, `engine/indicators.py` | Bot |
| 3 | Risk | The Guard | `engine/risk.py` | Bot |
| 4 | Execution | The Hands | `connectors/executor.py`, `connectors/mt5_client.py` | Bot |
| 5 | Command | The Ears (new) | `connectors/command_poller.py` | Bot |
| 6 | Notifier | The Courier (new) | `connectors/notifier.py` | Bot |
| 7 | Orchestrator | The Messenger | n8n workflows W1-W4 | n8n server |

## 1. The Ingestor Agent (The Senses)
- **Role:** Data acquisition.
- **Responsibility:** Read new candles from the MT5 terminal, clean them (drop duplicates, drop the still-forming candle, normalise to UTC) and return a Pandas DataFrame for the Strategy Agent.
- **Trigger:** Time-based. Every `poll_seconds` it checks whether a **new closed candle** exists on the primary timeframe. It is not tick-driven, because signals use closed candles only. Open trades are still monitored every loop.
- **Output:** Clean OHLCV DataFrame per symbol.
- **Must never:** return the forming candle, place orders, or hold strategy logic.
- **On failure:** MT5 disconnect triggers reconnect with backoff and a `WARN` event.

## 2. The Strategy Agent (The Brain)
- **Role:** Pattern recognition.
- **Responsibility:** Calculate EMA(200), RSI(14), Bollinger Bands(20,2) and ATR(14) and evaluate the market state.
- **Output (entries):** `SIGNAL_LONG`, `SIGNAL_SHORT`, or `SIGNAL_NONE`.
- **Output (exits, for open trades):** an exit signal with a reason: RSI 50-55, middle Bollinger Band, 1:1.5R target, or time stop (15 candles). How full exits interact with the staged trailing rules depends on PRD decision D1.
- **Must never:** call Execution, touch MT5, use the network or the clock (pure function: data in, signal out).

## 3. The Risk Agent (The Guard)
- **Role:** Compliance and safety.
- **Responsibility:** Intercept **every** signal and check it against the rulebook: not paused, no limit lock, max 2 concurrent trades, correlation (EUR/USD and AUD/USD blocked in the same direction), spread and session filters, and position sizing (a lot below the broker minimum means the trade is skipped, never rounded up).
- **Also owns:** daily 2% and weekly 5% limits (the circuit breaker: `stop_trading` and `close_all`), the trailing stages (breakeven at +1R, 50% close at +1.5R, trail at 2 x ATR), and the limit locks.
- **Output:** `TRADE_APPROVED` (with lot size and SL) or `TRADE_REJECTED` (with a reason that is logged).
- **The veto is final.** No other agent and no remote command can override it or lift a limit lock; `/resume` is refused during a lock.
- **Must never:** call MT5 or the network (pure, so it can be tested and backtested).

## 4. The Execution Agent (The Hands)
- **Role:** Order management.
- **Responsibility:** The **only** agent that trades. Sends `order_send` requests through `connectors/mt5_client.py`; modifies SL; partial closes (50% rule); closes trades; cancels pending orders; runs the `/kill_all` sequence (cancel pending, close all, verify, report, exit with code 10).
- **Error handling:** Retries on requote / price changed / timeout (max 3, with backoff). No retry on "no money" or "market closed".
- **Must never:** send an order without a stop loss, run in live mode without the confirm flag and matching `LIVE_SERVER`, or act on anything that has not been approved by the Risk Agent (the kill switch is the one authorised exception).

## 5. The Command Agent (The Ears), new
- **Role:** Remote control intake.
- **Responsibility:** Every `command_poll_seconds` (default 5) call `GET /webhook/atmr/commands` with `X-BOT-API-KEY`. Validate each command: strict enum, not expired, not already processed. Pass it to the main loop and send a `COMMAND_ACK`.
- **Allowed commands:** `PAUSE`, `RESUME`, `STATUS`, `RISK_CHECK`, `KILL_ALL` (`KILL_ALL` only with `confirmed_at`).
- **Threading:** runs in a background thread that only puts commands on a queue; the main loop applies them.
- **Must never:** trade directly, open any port, pass command text to a shell or `eval`, or lift a limit lock.

## 6. The Notifier Agent (The Courier), new
- **Role:** Outbound messages.
- **Responsibility:** Build events per the protocol (ARCHITECTURE section 6) and POST them to n8n with `X-BOT-API-KEY`. Keep an outbox in SQLite and retry with backoff. Send the 5-minute `HEARTBEAT`. Send critical events (`ERROR` critical, `LIMIT_HIT`, `KILL_RESULT`) **also directly** to the Telegram API as a fallback.
- **Must never:** block trading (5-second timeout, failures go to the outbox) or include secrets in a message.

## 7. The Orchestrator Agent (The Messenger, n8n)
- **Role:** Integration and notification, running on the n8n server.
- **Responsibility:**
  - **W1 Event Receiver:** validates the key, formats events into human-readable Telegram messages (DESIGN_SYSTEM.md).
  - **W2 Command Listener:** accepts only your chat ID, handles the `YES` confirmation for `/kill_all`, and queues commands with an expiry.
  - **W3 Command Queue:** returns pending commands to the bot when it polls (the bot pulls; n8n never calls into the bot).
  - **W4 Heartbeat Watchdog:** raises "BOT OFFLINE" and unacknowledged-kill alerts.
- **Must never:** talk to MT5, open trades, or change risk settings.

## Agent Rules
- Only **Risk** approves trades; only **Execution** trades; only **Notifier** sends events; only **Command** receives commands.
- Strategy and Risk (`engine/`) are pure; only `connectors/mt5_client.py` imports `MetaTrader5`.
- Every agent output that matters is logged with a UTC timestamp and never contains secrets.

---
# Part B: Rules for AI Coding Assistants

## Project
ATMR-Bot is an automated MT5 trading bot (EUR/USD, XAUUSD, AUD/USD) using EMA(200), RSI(14), Bollinger Bands(20,2) and ATR(14). It runs on Windows next to the MT5 terminal, sends events to n8n, and polls n8n for commands from Telegram. **It trades real money when in live mode.**

## Read These First (in order)
1. `docs/PRD.md`: what the bot must do (and the open decisions D1-D6)
2. `docs/ARCHITECTURE.md`: modules, protocol, command flow
3. `docs/SECURITY.md`: hard safety rules
4. `docs/TESTING.md`, `docs/CODE_STYLE.md`, `docs/DESIGN_SYSTEM.md`

## Hard Rules (never break)
- **Secrets:** never hardcode, print or log credentials; they come from `.env` only. Never edit or commit `.env`.
- **Live trading:** never enable it, change the `TRADING_MODE` default, or weaken the live-mode guard.
- **Risk:** never remove or bypass the mandatory SL, loss limits, max-trade limit, or the Risk Agent's veto.
- **Network:** the bot opens **no inbound ports**. Do not add FastAPI, Flask, sockets or any server. All connections go outbound (n8n, Telegram).
- **Commands:** only `PAUSE`, `RESUME`, `STATUS`, `RISK_CHECK`, `KILL_ALL`. Do not add `/stop_all` or any command that opens trades or changes risk values.
- **Kill switch:** do not change the `/kill_all` logic, the YES confirmation, the expiry, or exit code 10 without explicit approval.
- **Tests:** never send real orders from tests. Use mocks; integration tests are demo-only and marked `@pytest.mark.integration`.
- **Layers:** only `connectors/mt5_client.py` imports `MetaTrader5`. Everything in `engine/` stays pure (no I/O, no MT5, no network, no `datetime.now()`) and never imports from `connectors/`. Follow `docs/CODE_STYLE.md`.
- **Spec:** do not invent trading logic. If the PRD is unclear or conflicts (for example D1), **stop and ask**.
- **Time:** store and log in UTC.

## Where Things Live
| Task | File |
|---|---|
| Indicator math | `src/atmr/engine/indicators.py` |
| Entry/exit rules | `src/atmr/engine/strategy.py` |
| Sizing, limits, trailing | `src/atmr/engine/risk.py` |
| Orders, kill sequence | `src/atmr/connectors/executor.py` |
| MT5 access | `src/atmr/connectors/mt5_client.py` |
| Events, outbox, fallback | `src/atmr/connectors/notifier.py` |
| Command polling | `src/atmr/connectors/command_poller.py` |
| State (SQLite) | `src/atmr/connectors/state.py` |
| Backtest runner | `src/atmr/backtest/` |

## Workflow
1. Restate the task and the PRD section it implements.
2. Write or update tests first for any change to strategy, risk or commands.
3. Implement in small steps; keep functions short and typed.
4. Run `ruff check . && black . && pytest` and fix failures.
5. Update the matching doc in `docs/` if behaviour changed.
6. Commit with a Conventional Commit message.

## Commands
```
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
pytest
pytest -m integration        # needs MT5 demo running
python -m atmr.main --config config/config.yaml
python -m atmr.backtest --config config/backtest.yaml
```

## Ask Before Touching
`.env`, risk values in `config/config.yaml`, the live-mode guard in `main.py`, the kill switch, the command allowlist, and SQLite database files.

---
## Changelog
- **v2.0.1:** File paths updated to the `engine/` `connectors/` `utils/` layout.
- **v2.0.0:** Combined your agent definitions with rules for AI coding tools. Kept: five agents and nicknames, Risk veto, signal and trade outputs. Changed: Ingestor is candle-based, not tick-based; Strategy also emits exit signals; Risk owns limits, trailing and locks; Execution owns the kill sequence and SL/live guards; Orchestrator uses the command queue instead of a Python API. Added: Command Agent, Notifier Agent, agent rules, Part B.
- **v1.0.0:** Five agent definitions.
