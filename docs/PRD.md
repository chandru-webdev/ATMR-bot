# PRD: ATMR-Bot (Automated Trend-Following Mean Reversion Bot)

**Version:** 1.2.0 (merge of PRD v1.0.0 and v0.2) | **Status:** Draft / In Development
**Author:** Chandru | **Last updated:** 2026-09-28

## 1. Executive Summary
ATMR-Bot is an automated algorithmic trading system for MetaTrader 5 (MT5). It combines a trend-following filter with mean-reversion entries to find high-probability pullback trades in trending markets, and it protects capital with strict risk limits. Telegram monitoring runs through n8n.

## 2. Goals and Non-Goals
**Goals**
- Run the strategy exactly as specified, on demo first, unattended 24/5.
- Enforce every risk limit in code so the bot cannot exceed them.
- Send Telegram alerts for every trade, error and daily summary.

**Non-Goals (v1)**
- No live-money trading until the go-live criteria (section 10) are met.
- No high-frequency or tick-level trading.
- No web dashboard (Telegram, email and logs only).
- No machine learning.

## 3. Target Assets and Timeframes
- **Symbols:** EUR/USD, XAU/USD (Gold), AUD/USD.
- **Timeframes supported:** M1, M15, H1, H4, D1. One primary timeframe drives signals (default H1); others are configurable.

## 4. Functional Requirements
### 4.1 Indicators
| Purpose | Indicator |
|---|---|
| Trend filter | EMA(200) |
| Momentum/reversal | RSI(14) |
| Volatility stretch | Bollinger Bands(20, 2) |
| Volatility/risk | ATR(14) |

Entry signals use **closed candles only** (no repainting). Open positions are monitored continuously for exits.

### 4.2 Entry Logic
**Long (Buy)**: all must be true
1. **Trend:** price above EMA(200).
2. **Momentum:** RSI(14) below `rsi_oversold` (default **35**).
3. **Volatility:** price touched or closed below the lower Bollinger Band (within `touch_lookback` candles, default 1).
4. **Confirmation:** entry on the close of a bullish candle following the touch.

**Short (Sell)**: all must be true
1. **Trend:** price below EMA(200).
2. **Momentum:** RSI(14) above `rsi_overbought` (default **70**).
3. **Volatility:** price touched or closed above the upper Bollinger Band.
4. **Confirmation:** entry on the close of a bearish candle following the touch.

### 4.3 Exit and Trade Management
**Take profit** (whichever comes first, see D1 for the interaction with staging)
- RSI reaches 50-55.
- Price touches the middle Bollinger Band (20 SMA).
- Target reward-to-risk of 1:1.5 is reached.

**Stop loss**
- Initial SL = 1.5 x ATR(14) from entry, set at order time.

**Time stop**
- Close the trade if it stays open more than 15 candles.

**Trailing stop (staged)**
- Stage 1 (breakeven): at +1R, move SL to entry.
- Stage 2 (partial profit): at +1.5R, close 50% of the position.
- Stage 3 (trailing): trail the remaining 50% at 2 x ATR from the current price.

## 5. Risk Management and Constraints
### 5.1 Capital Protection
- **Risk per trade:** 0.5% of balance in testing; maximum 1.0% live.
- **Daily drawdown limit:** if equity drops 2% in a day, **close all trades** and stop trading until the next day.
- **Weekly drawdown limit:** if equity drops 5% in a week, stop trading until the following week (open positions are also closed by default, see D6).
- Baselines: daily = equity at broker-day start; weekly = equity at the week's first market open.

### 5.2 Position Sizing and Exposure
- **Size** = (Account Balance x Risk %) / (SL distance x value per price unit). The value comes from MT5 `trade_tick_value` / `trade_tick_size` (not a fixed pip value, so XAUUSD is handled correctly). Lot is rounded down to the broker's `volume_step` and clamped to min/max.
- **Max concurrent trades:** 2.
- **Correlation rule:** no two trades on highly correlated pairs moving in the same direction (e.g. EUR/USD long and AUD/USD long are blocked; opposite directions are allowed). Correlation groups are configurable.

### 5.3 Extra Safety Filters (new)
- Skip entry if spread exceeds `max_spread` for the symbol.
- Skip entry outside configured trading sessions.
- Kill switch: a `STOP` file or Telegram `/stop` halts new entries.
- Live mode requires an explicit confirm flag (see SECURITY.md).

## 6. Non-Functional Requirements
### 6.1 Notifications
- **Telegram (via n8n), primary:** trade opened/closed, SL moved, partial close, limit hit, errors, daily summary, and a heartbeat every 5 minutes (n8n alerts if it stops).
- **Email:** detailed trade logs (entry, SL, TP, symbol, result).
- **SMS/WhatsApp (Twilio):** critical alerts only (margin call, daily/weekly limit hit, errors). Deferred to a later version; critical alerts go to Telegram meanwhile.
- Critical events are also sent directly to the Telegram API as a fallback if n8n is down.

### 6.2 Infrastructure
- **Execution:** MT5 desktop terminal + Python `MetaTrader5` library. The terminal and library are **Windows-only**, so the bot must run on Windows next to MT5 (Linux would need Wine, which is unreliable).
- **Development:** personal Windows machine, demo account, private GitHub repo.
- **Production:** Windows cloud VPS (MT5 + bot running 24/5). Cloud choice: any provider with Windows servers (for example AWS EC2 Windows, Vultr, Contabo or a forex VPS host). Check that your provider offers Windows before choosing.
- **n8n:** separate Linux VPS (Docker) or n8n Cloud; it never talks to MT5.
- **Storage:** SQLite (trades, state, daily stats).

### 6.3 Configuration
All strategy and risk values live in `config/config.yaml` (symbols, timeframe, RSI levels, risk %, multipliers, time stop, limits, spread caps, sessions, correlation groups). Nothing is hardcoded.

## 7. Notification Events
| Event | When |
|---|---|
| `trade_opened` | Order filled |
| `trade_closed` | Any exit, with P/L and reason |
| `sl_moved` / `partial_close` | Trailing stages |
| `limit_hit` | Daily or weekly limit |
| `error` | MT5 disconnect, order rejection, exception |
| `heartbeat` | Every 5 minutes |
| `daily_summary` | End of day: balance, equity, P/L, trades |

## 8. Roadmap
- **Phase 1: Local development and backtesting** (Python + MT5 on a Windows machine)
  1. Demo account + MT5/Python connection test
  2. Data ingestion, indicators, strategy signals, unit tests
  3. Risk sizing, limits, execution, trailing, time stop
  4. Backtest and review
- **Phase 2: Paper trading** (demo account, then Windows VPS)
  5. Demo forward test, minimum 4 weeks
  6. n8n + Telegram monitoring
  7. Deploy to Windows VPS as a service
- **Phase 3: Live deployment** (small capital, real account)
  8. Go-live review against section 10, then start at reduced risk

## 9. Risks
- Broker differences (spreads, symbol names such as `EURUSD.m`, execution quality).
- Weekend and news gaps can jump past any stop loss.
- Bot or VPS downtime with open positions (mitigated by SL on every order and the heartbeat).
- Backtest results overstating live performance.

## 10. Go-Live Criteria
- At least 4 weeks on demo and at least 50 trades.
- Behaviour matches the spec: no limit breaches, no missed exits.
- No unhandled crashes in the last 2 weeks.
- SECURITY.md pre-live checklist fully passed.
- First live phase at minimum size / 0.25% risk.

## 11. Decisions
All open decisions (D1-D8) are locked. See [DECISIONS.md](DECISIONS.md) for the full table and the canonical D1 exit-rule precedence. Summary: D1(a) 1.5R = 50% partial, remainder exits on RSI 50-55/mid-BB; D2 RSI 35/70; D3 risk 0.5% demo / 1.0% live; D4 H1 primary; D5 EURUSD+AUDUSD correlated; D6 weekly limit closes all; D7 no news filter in v1; D8 broker symbols deferred to config.


## 12. Changelog
- **v1.2.0:** D1-D8 locked; see DECISIONS.md (section 11 now points there instead of listing open items).
- **v1.1.0:** Merged v1.0.0 and v0.2. Kept from v1.0.0: RSI 35 default, 1:1.5R as a TP scenario, daily limit closes all trades, same-direction correlation rule, email and SMS/WhatsApp channels, 3-phase roadmap. Added from v0.2: Windows-only MT5 requirement and Windows VPS, n8n + Telegram monitoring with heartbeat and fallback, closed-candle signals, tick-value position sizing, spread/session filters, kill switch, live-mode guard, SQLite state, go-live criteria, open decisions.
- **v1.0.0:** Draft with full strategy, risk rules and roadmap.
- **v0.2:** Earlier draft with Telegram/n8n and infrastructure notes.
