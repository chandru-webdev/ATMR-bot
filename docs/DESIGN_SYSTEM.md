# Design System: ATMR-Bot (Information Design)

**Version:** 2.0.1 | **Status:** Draft | **Last updated:** 2026-09-28
Covers Telegram messages (built by the n8n W1 workflow), logs and console output. ATMR-Bot has no graphical UI in v1; if a dashboard is added later, extend this file with colours, typography and components.

The design goal is **"High Signal, Low Noise."** Telegram messages must be scannable in **less than 2 seconds**.

## 1. Visual Hierarchy (Telegram Formatting)
We use **MarkdownV2** to create hierarchy:
- **Headers:** `*SYMBOL/TYPE*` (bold) identifies the asset immediately (for example `*XAUUSD/BUY*`). Alert titles are bold with an emoji.
- **Key values:** monospaced (for example `2035.50`), so numbers are easy to read.
- **Status indicators:** emojis communicate direction and health.
- **Mode tag:** every alert title ends with `DEMO` or `LIVE` in monospace, so you never confuse the two.

## 2. The Emoji Language (Semantics)
To reduce reading time we use one standard emoji set.

| Emoji | Meaning |
|---|---|
| 🟢 **Green Circle** | Long/Buy signal or Profit |
| 🔴 **Red Circle** | Short/Sell signal or Loss |
| ⚠️ **Warning** | Margin call, daily/weekly loss limit hit, or error |
| 📉 **Chart Down** | Bearish trend / price drop |
| 🚀 **Rocket** | Trade execution success |
| 🛑 **Stop Sign** | Trade closed (TP / SL / Time Stop) |

**Added for the new events** (same style):
| Emoji | Meaning |
|---|---|
| 🔒 | Stop loss moved (breakeven / trailing), or trading locked |
| 🎯 | Partial close taken |
| ⏸ / ▶️ | Bot paused / resumed |
| ℹ️ | Status report |
| 🛡 | Risk check report |
| 📊 | Daily summary |
| ✅ | Active / healthy / command done |
| 🚨 | Kill switch and bot offline (the most urgent alerts) |

**Circle rule (avoids confusion):** in a *trade opened* message the circle shows **direction** (🟢 BUY, 🔴 SELL). In a *trade closed* message the circle shows **result** (🟢 profit, 🔴 loss), and the direction is written as text.

## 3. Message Templates
Templates are shown as the raw MarkdownV2 text n8n sends. Dynamic values go inside backticks (see section 5 for escaping).

### Template A: New Trade Alert (`TRADE_OPEN`)
```
*🚀 NEW TRADE EXECUTED* · `DEMO`
Asset: *XAUUSD*
Direction: 🟢 BUY
Lot: `0.10`
Entry: `2035.50`
SL: `2025.00`
TP: `2055.00`
Risk: `0.5%`
```
`TP` is shown only when a fixed target exists. Otherwise the line is `Exit: ` followed by the exit rule in monospace (for example `RSI 50-55 / mid-BB / 1.5R`), because exits are dynamic. `Risk` shows the configured risk (0.5% demo, 1.0% live).

### Template B: Error / Alert (`ERROR`)
```
*⚠️ CRITICAL ALERT* · `DEMO`
Type: `CONNECTION_LOST`
Message: Python agent cannot reach MT5 terminal\. Checking heartbeat\.\.\.
Time: `10:15 IST`
```
Warnings (retries, high spread, reconnecting) use the same layout with the title `*⚠️ WARNING*`.

### Template C: Daily Summary (`DAILY_SUMMARY`)
```
*📊 DAILY SUMMARY* · `DEMO`
Date: `2026-09-28`
P/L Today: `+$145.00 (1.2%)`
Max Drawdown: `0.4%`
Balance: `10145.00`
Equity: `10150.00`
Trades: `3 (2W / 1L)`
Status: ✅ ACTIVE
```
`Status` is one of `✅ ACTIVE`, `⏸ PAUSED`, `🔒 LOCKED`.

### Template D: Trade Closed (`TRADE_CLOSE`)
```
*🛑 TRADE CLOSED* · `DEMO`
Asset: *XAUUSD* · SELL
Result: 🟢 `+$72.40 (+1.4R)`
Exit: `Middle BB`
Held: `6 candles`
```
A loss shows 🔴 with the negative amount, for example `-$50.00 (-1.0R)`. Exit reasons: `RSI 50-55`, `Middle BB`, `1.5R target`, `Stop loss`, `Time stop`, `Loss limit`, `Kill switch`.

### Template E: Trailing Events (`SL_MOVED`, `PARTIAL_CLOSE`)
```
*🔒 SL MOVED* · `DEMO`
Asset: *EURUSD*
Stage: `Breakeven (+1R)`
New SL: `1.08500`
```
```
*🎯 PARTIAL CLOSE* · `DEMO`
Asset: *EURUSD*
Closed: `0.05 lot (50%)`
Realized: 🟢 `+$36.20`
Remaining: trailing at 2×ATR
```

### Template F: Loss Limit (`LIMIT_HIT`)
```
*⚠️ DAILY LOSS LIMIT HIT* · `DEMO`
Drawdown: `2.1% (limit 2%)`
Action: all trades closed, trading locked
Locked until: `2026-09-29 00:00 IST`
```
The weekly limit uses the title `*⚠️ WEEKLY LOSS LIMIT HIT*`.

### Template G: Bot State (`BOT_STATE`, `COMMAND_ACK`)
```
*⏸ BOT PAUSED* · `DEMO`
No new trades\. Open trades are still managed\.
```
```
*▶️ BOT RESUMED* · `DEMO`
New trades allowed\.
```
A refused command:
```
*⚠️ COMMAND REJECTED* · `DEMO`
Command: `RESUME`
Reason: `Daily loss lock active`
```

### Template H: Command Replies (`STATUS_REPORT`, `RISK_REPORT`)
```
*ℹ️ STATUS* · `DEMO`
Balance: `10145.00`
Equity: `10150.00`
State: ✅ ACTIVE
Open trades: `1`
*EURUSD/BUY* `0.10 @ 1.08500  +$12.30`
```
```
*🛡 RISK CHECK* · `DEMO`
Daily DD: `0.4%` of `2%`
Weekly DD: `1.1%` of `5%`
Status: ✅ ACTIVE
```

### Template I: Kill Switch (`/kill_all`, `KILL_RESULT`)
Confirmation prompt (sent by n8n):
```
*🚨 KILL SWITCH*
Reply *YES* within 60 seconds to close ALL positions and STOP the bot\.
```
Result (after the bot acts):
```
*🚨 KILL SWITCH EXECUTED* · `DEMO`
Orders cancelled: `1`
Positions closed: `2`
Failed: `0`
Bot stopped\. Restart needed: `--acknowledge-kill`
```
If any close fails, add the line `KILL INCOMPLETE, close manually in the MT5 app\.`

### Template J: Bot Offline (from the n8n watchdog)
```
*🚨 BOT OFFLINE*
No heartbeat since: `10:15 IST`
Check the VPS, or close trades in the MT5 mobile app\.
```

## 4. Event to Template Map
| Event | Template | Icon |
|---|---|---|
| `TRADE_OPEN` | A | 🚀 |
| `TRADE_CLOSE` | D | 🛑 |
| `SL_MOVED` | E | 🔒 |
| `PARTIAL_CLOSE` | E | 🎯 |
| `LIMIT_HIT` | F | ⚠️ |
| `ERROR` | B | ⚠️ |
| `DAILY_SUMMARY` | C | 📊 |
| `BOT_STATE` | G | ⏸ ▶️ 🔒 |
| `STATUS_REPORT` / `RISK_REPORT` | H | ℹ️ 🛡 |
| `KILL_RESULT` | I | 🚨 |
| `COMMAND_ACK` (rejected) | G | ⚠️ |
| Missing heartbeat (n8n) | J | 🚨 |
| `HEARTBEAT` | Not sent to Telegram (used only by the watchdog) | none |

## 5. MarkdownV2 Escaping (important)
Telegram rejects a MarkdownV2 message ("can't parse entities") if special characters are not escaped.
- Outside code spans, these characters must be escaped with a backslash: `_ * [ ] ( ) ~ ` > # + - = | { } . !`
- **Inside backticks**, only the backtick and backslash need escaping.
- Rule: **put every dynamic value in backticks** and escape the static text.

Helpers for the n8n Code node:
```js
const escapeMd = (t) => String(t).replace(/([_*\[\]()~`>#+\-=|{}.!\\])/g, '\\$1');
const code = (t) => '`' + String(t).replace(/([`\\])/g, '\\$1') + '`';
```
In the Telegram node set **Parse Mode = MarkdownV2**, and turn off the "Append n8n Attribution" option so messages stay clean. If escaping becomes a maintenance problem, switch to HTML parse mode (`<b>`, `<code>`), where only `&`, `<` and `>` need escaping.

## 6. Formatting Rules
- **Length:** an alert has at most about 8 lines and 400 characters. First line = what happened; second = which asset.
- **One event, one message.** Group repeated noise; no spam.
- **Numbers:** prices to the symbol's digits; money with 2 decimals and a sign (`+72.40`, `-30.10`); percentages 1-2 decimals.
- **Time:** stored in UTC; shown in a configurable timezone (default Asia/Kolkata, written `IST`).
- **Symbols:** uppercase, no slash (`EURUSD`, `XAUUSD`).
- **Never** include passwords, tokens, API keys or full account numbers.

## 7. Log Format and Console Output
```
2026-09-28 10:15:00Z - [RiskManager] - INFO - EURUSD size=0.10 risk=0.5% sl_dist=30.0pips
```
Fields: UTC timestamp - [module or class] - LEVEL - message (the CODE_STYLE.md format). One line per event, no secrets. On start, the console prints: version, mode, symbols, timeframe, masked account, and `kill switch: off`. In live mode it prints a highlighted warning line.

## 8. Changelog
- **v2.0.1:** Log format aligned with CODE_STYLE.md.
- **v2.0.0:** Merged your design system. Kept everything from your draft: the "High Signal, Low Noise" goal, MarkdownV2 hierarchy (bold headers, monospaced values, status emojis), the six-emoji language (🟢 🔴 ⚠️ 📉 🚀 🛑), and Templates A, B and C. Changed: Template A shows Lot, mode tag and an optional TP (exits are dynamic); Template C adds balance, equity and trades; the circle rule separates direction (open) from result (closed). Added: emojis and templates D-J for closing, trailing, limits, pause/resume, status, risk check, kill switch and bot offline; event map; MarkdownV2 escaping rules; formatting, log and console rules.
- **v1.0.0:** Original design system.
