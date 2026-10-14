# ATMR-Bot

Automated Trend-Following Mean Reversion bot for MetaTrader 5. It trades EUR/USD, XAU/USD and AUD/USD using EMA(200), RSI(14), Bollinger Bands(20, 2) and ATR(14), enforces strict risk limits in code, and reports to you on Telegram through n8n.

> **Warning:** Trading is risky and you can lose money. This project is for learning and personal use, not financial advice. Run on a **demo account** until every gate in [TESTING.md](TESTING.md) has passed.

**Version:** 2.0.0 | **Status:** Core engine logic and utils complete (79 unit tests passed); connectors next.

## What It Does
- **Long:** price above EMA(200), RSI oversold, price at or below the lower Bollinger Band, then a bullish candle close.
- **Short:** the mirror image (below EMA(200), RSI overbought, upper band, bearish candle close).
- **Exits:** RSI 50-55, the middle band, or the 1:1.5R target; stop loss at 1.5 x ATR; time stop after 15 candles; staged trailing (breakeven at +1R, 50% closed at +1.5R, rest trailed at 2 x ATR).
- **Risk:** 0.5% per trade on demo, 1.0% max live; max 2 trades; correlated pairs blocked in the same direction; daily 2% and weekly 5% loss limits.

## How It Works
```
[MT5 Terminal] <-> [Python bot] --events--> [n8n] --> [Telegram]
                        ^                     |
                        +---- polls commands -+   <-- you
```
- The **Python bot** (Windows, next to MT5) does the maths, risk checks and trading.
- **n8n** receives events and routes them to Telegram; it also queues your commands.
- **Telegram** is your phone dashboard.
- The bot opens **no inbound ports**. It only makes outgoing connections.

## Safety First
- Demo mode is the default; live mode needs an explicit confirm flag and a matching server.
- Every order has a stop loss; loss limits and a risk veto are enforced in code.
- Remote control is limited to five commands, and `/kill_all` needs a `YES` confirmation.
- Secrets live only in `.env`, which is never committed. Details: [SECURITY.md](SECURITY.md).

## Requirements
- **Windows** (the MT5 terminal and Python library run on Windows; use a Windows VPS for 24/5 operation)
- MetaTrader 5 terminal and a **demo account**
- Python 3.11+
- An n8n instance (n8n Cloud or self-hosted with HTTPS) and a Telegram bot

## Quick Start (Windows, demo)
```
git clone https://github.com/chandru-webdev/atmr-bot.git
cd atmr-bot
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
copy .env.example .env
```
1. Fill `.env` with your **demo** credentials (variables are listed in [SECURITY.md](SECURITY.md) section 1.1).
2. In MT5: Tools > Options > Expert Advisors > tick **Allow algorithmic trading**, then log in to your demo account.
3. Test the connection:
   ```
   python -m atmr.check_connection
   ```
4. Run the bot (demo):
   ```
   python -m atmr.main --config config/config.yaml
   ```

## Telegram and n8n Setup
1. Create a bot with @BotFather and save the token; get your chat ID from @userinfobot.
2. Import or build the four n8n workflows: Event Receiver, Command Listener, Command Queue, Heartbeat Watchdog (see [ARCHITECTURE.md](ARCHITECTURE.md) section 4.2).
3. Set the n8n Telegram node to **MarkdownV2** (see [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) section 5).
4. Put `N8N_BASE_URL` and `X_BOT_API_KEY` in `.env`, and send a test event.

## Telegram Commands
| Command | What it does |
|---|---|
| `/status` | Balance, equity, open positions, state |
| `/risk_check` | Daily and weekly drawdown vs limits |
| `/pause` | Stop opening new trades (open trades still managed) |
| `/resume` | Allow new trades again (refused during a limit lock) |
| `/kill_all` | Reply `YES` within 60 seconds: close all, cancel pending, stop the bot |

## Project Layout
```
atmr-bot/
  docs/                    documentation (this folder)
  src/atmr/
    main.py                entry point and main loop
    config.py, exceptions.py
    engine/                pure logic: indicators, strategy, risk, models
    connectors/            MT5, data, executor, state, notifier, command poller
    backtest/              custom backtest runner
    utils/                 logging, redaction, helpers
  tests/
  config/config.yaml       strategy and risk settings
  data/                    historical data (git-ignored)
  .env.example  .gitignore  requirements.txt  pyproject.toml
```

## Development
```
pytest                       # unit tests
pytest -m integration        # needs MT5 demo running
ruff check . && black . && mypy src
python -m atmr.backtest --config config/backtest.yaml
```

## Documentation
| Doc | Purpose |
|---|---|
| [PRD.md](PRD.md) | What the bot does, rules, open decisions |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layers, modules, command flow, message protocol |
| [SECURITY.md](SECURITY.md) | Secrets, hardening, kill switch, checklists |
| [TESTING.md](TESTING.md) | Four gates: unit, backtest, integration, paper trading |
| [CODE_STYLE.md](CODE_STYLE.md) | Conventions, tooling, folder rules |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | Telegram message formats and emoji language |
| [AGENTS.md](AGENTS.md) | The system's agents and rules for AI coding tools |
| [PROGRESS.md](PROGRESS.md) | Build status for AI tools — read and update on every task |
| [DECISIONS.md](DECISIONS.md) | Locked answers to D1-D8, and the canonical D1 exit-rule precedence |

## Roadmap
1. **Phase 1:** local development and backtesting (Windows machine, demo account).
2. **Phase 2:** paper trading for at least 4 weeks on a Windows VPS, with n8n and Telegram monitoring.
3. **Phase 3:** live deployment with small capital, only after the go-live criteria in the PRD.

**Decisions:** D1-D8 are locked — see [DECISIONS.md](DECISIONS.md).
