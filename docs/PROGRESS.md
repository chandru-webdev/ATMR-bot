# ATMR-Bot — Progress

Last updated: 2026-10-10 by Antigravity

## Status

`MT5Connector` terminal gateway and order manager is complete (113 tests total, 34 in connectors). Next: `src/atmr/connectors/executor.py` (`ExecutionEngine`).

## Done

- [x] Project docs (`docs/PRD.md`, `ARCHITECTURE.md`, `SECURITY.md`, `TESTING.md`, `CODE_STYLE.md`, `AGENTS.md`, `DESIGN_SYSTEM.md`, `DECISIONS.md`) — D1–D8 locked
- [x] `config/config.yaml` — locked defaults; only **XAUUSD** is active (EURUSD/AUDUSD commented)
- [x] `.env.example`, `pyproject.toml` (src layout), `requirements.txt`
- [x] `src/atmr/check_connection.py` — MT5 demo connection verified on MetaQuotes-Demo
- [x] `src/atmr/exceptions.py` — `ConfigError`, `LiveModeGuardError`, trading errors
- [x] `src/atmr/config.py` — loads `.env` + YAML, hard caps, live-mode guard (`tests/test_config.py`: **13 passed**)
- [x] `src/atmr/engine/models.py` — frozen `Signal`, `TradeRequest`, `Position`, `RiskDecision`, `ExitDecision`, `AccountState`, `LimitDecision` (`tests/test_models.py`: **8 passed**)
- [x] `src/atmr/engine/indicators.py` — MT5-style EMA / Wilder RSI / BB / ATR (`tests/engine/test_indicators.py`: **10 passed**)
- [x] `src/atmr/engine/strategy.py` — closed-candle entries + D1 exit precedence (`tests/engine/test_strategy.py`: **16 passed**)
- [x] `src/atmr/engine/risk.py` — sizing, daily/weekly circuit breaker, veto, D1 trailing (`tests/engine/test_risk.py`: **15 passed**)
- [x] `src/atmr/utils/logging_setup.py`, `src/atmr/utils/redact.py` — UTC logs, secret redaction (`tests/utils/`: **17 passed**)
- [x] `src/atmr/connectors/state.py` — SQLite (`StateStore`): trades, daily stats, bot_state & locks, outbox queue, seen commands, audit events (`tests/connectors/test_state.py`: **12 passed**)
- [x] `src/atmr/connectors/data.py` — OHLCV ingest, clean, drop forming candle, detect new closed candles (`tests/connectors/test_data.py`: **9 passed**)
- [x] `src/atmr/connectors/mt5_client.py` — only file that may import `MetaTrader5` (`MT5Connector`, account/symbol/position models, exponential backoff, retry handling, timeout duplicate protection, live guard) (`tests/connectors/test_mt5_client.py`: **13 passed**)

Suite total as of this update: **113 passed**.

## In Progress

None.

## Not Started

- [ ] `src/atmr/connectors/executor.py` — orders, SL, partials, kill sequence (`ExecutionEngine`)
- [ ] `src/atmr/connectors/notifier.py` — n8n events, outbox, Telegram fallback (`Notifier`)
- [ ] `src/atmr/connectors/command_poller.py` — poll n8n; allowlist only (`CommandPoller`)
- [ ] `src/atmr/main.py` — loop, wiring, kill switch, `--acknowledge-kill`
- [ ] `src/atmr/backtest/` — candle-by-candle runner reusing `strategy.py` and `risk.py`
- [ ] n8n workflows W1–W4 (not Python)
- [ ] Gate 2 backtest data in `data/` (git-ignored)
- [ ] Gate 3 integration tests (`@pytest.mark.integration`, demo only)
- [ ] Gate 4 paper trading (4+ weeks, 50+ trades)

## Needs a human decision (do not guess)

**Trailing is implemented in two places** (accepted for now; wire in `main.py` later). `StrategyEngine.evaluate_exit` uses candle high/low plus SL / time stop / RSI / mid-BB. `RiskManager.evaluate_trailing` uses a single `current_price`. Do not call both independently in the main loop.

## Decisions Already Locked

Do not re-ask. Follow `docs/DECISIONS.md` exactly (D1–D8).

- **D1(a):** +1.5R = 50% partial once, never a full close. Remainder exits on RSI 50–55 or middle BB (or SL / time stop / limit / kill).
- **D2:** RSI 35 / 70
- **D3:** 0.5% demo, 1.0% max live, 0.25% first live
- **D4:** H1 primary
- **D5:** EURUSD+AUDUSD correlated; XAUUSD independent
- **D6:** weekly 5% closes all and locks until next week
- **D7:** no news filter in v1
- **D8:** broker symbol suffixes in `config.yaml` only

## Rules Every Tool Must Follow

`docs/AGENTS.md` Part B. In particular:

- No inbound ports, no FastAPI/Flask/sockets
- Only remote commands: `PAUSE`, `RESUME`, `STATUS`, `RISK_CHECK`, `KILL_ALL`
- `engine/` stays pure: no I/O, no MT5, no network, no `datetime.now()`
- Only `connectors/mt5_client.py` imports `MetaTrader5`
- Never enable live mode or weaken the live-mode guard
- Never commit `.env`
- After any task, update this file
