# Decisions Log: ATMR-Bot

**Version:** 1.0.0 | **Status:** Locked | **Last updated:** 2026-09-30

Decisions that were open in PRD section 11. Once locked, change only with an explicit update to this file and the matching PRD / ARCHITECTURE sections.

| ID | Topic | Decision | Locked |
|---|---|---|---|
| **D1** | Take profit vs staged trailing | **(a)** At +1.5R close 50% (Stage 2). Full exit of the remainder only via RSI 50-55 or middle Bollinger Band (or SL / time stop / limit / kill). **1:1.5R is not a full-close TP.** | 2026-09-30 |
| **D2** | RSI thresholds | Keep asymmetric **35** oversold / **70** overbought (defaults; still configurable). | 2026-09-30 |
| **D3** | Risk % | **0.5%** demo; **1.0%** max live; first live phase **0.25%** (PRD go-live). | 2026-09-30 |
| **D4** | Primary timeframe | **H1** (others configurable; one primary drives signals). | 2026-09-30 |
| **D5** | Correlation groups | Default: **EURUSD + AUDUSD** correlated (same direction blocked); **XAUUSD** independent. | 2026-09-30 |
| **D6** | Weekly loss limit | Same as daily: **close all open positions** and lock new entries until the following week. | 2026-09-30 |
| **D7** | News filter | **None in v1.** Revisit after Gate 4 paper trading (see TESTING.md). | 2026-09-30 |
| **D8** | Broker / symbol suffixes | **Deferred** until demo broker is chosen. Map broker symbols (e.g. `EURUSD.m`) in `config.yaml` only; code uses canonical `EURUSD`, `XAUUSD`, `AUDUSD`. | 2026-09-30 |

## Related defaults (confirmed, not open)

| Topic | Value |
|---|---|
| Email trade logs | **In v1** (SMTP via `EMAIL_USER` / `EMAIL_PASS`). Twilio SMS/WhatsApp deferred. |
| `kill_scope` | Default **`account`** (close every position on the account). Config may set `bot` (magic number only). |
| Telegram display timezone | **Asia/Kolkata (IST)**; storage/logs remain UTC. |
| Kill command | Telegram **`/kill_all`** (YES confirm). Local halt of new entries: `STOP` file. There is no `/stop` command. |

## D1 exit rules (canonical)

For an open position, evaluate in this order of precedence when multiple fire in the same loop (implementation detail; SL on the broker always wins if hit first):

1. **Stop loss** (broker or bot-modified SL) / **loss limit** / **kill** / **time stop** (15 candles) — full close of remaining size.
2. **Stage 1:** at +1R → move SL to breakeven (no close).
3. **Stage 2:** at +1.5R → close **50%** of original size once; then trail remainder at 2×ATR (Stage 3).
4. **Remainder full exit:** RSI in **50-55** **or** price touches **middle Bollinger Band**.

Do **not** fully close at 1.5R. Do **not** treat 1:1.5R as a standalone full take-profit.

## Changelog
- **v1.0.0:** Locked D1-D8 from owner answers (2026-09-30).
