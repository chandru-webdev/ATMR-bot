# Testing & Validation Plan: ATMR-Bot

**Version:** 2.0.0 | **Status:** Draft | **Last updated:** 2026-09-28
Covers PRD v1.1.0, ARCHITECTURE v2.0.2 and SECURITY v2.0.0.

## 1. Testing Philosophy
The strategy follows the **"Pyramid of Trust"**: start with small, isolated code tests and move toward live-market simulation. **No code is deployed to the Live environment (AWS Live) until it has passed all four gates.**

- Code that decides trades (`indicators.py`, `strategy.py`, `risk.py`) is pure and must be unit tested first.
- A bug in risk code costs more than a bug in notifications, so risk gets the strictest tests.
- Backtests are an upper bound of live performance, never a promise.

| Gate | Environment | Money at risk | Goal |
|---|---|---|---|
| 1. Unit | Local PC | None | Mathematical and logical accuracy |
| 2. Backtest | Local PC | None | Strategy is profitable after costs, and not overfitted |
| 3. Integration | Local + AWS demo | None | MT5, Python, n8n and Telegram work together and safely |
| 4. Paper trading | AWS Windows, demo account | None | Survives real market events for 4+ weeks |
| LIVE | AWS Windows, live account | **High** | Controlled live start at reduced risk |

## 2. Gate 1: Unit Testing (Logic Verification)
**Tool:** `pytest` (`pytest-cov` for coverage). Target: at least 90% coverage on `indicators.py`, `strategy.py` and `risk.py`.

### 2.1 Indicator Accuracy
- Feed a static price series and compare EMA(200), RSI(14), Bollinger Bands(20, 2) and ATR(14) with reference values from a trusted source (TradingView export or TA-Lib) within a small tolerance.
- Check the smoothing method matches the reference (RSI and ATR use Wilder smoothing).
- Too little data for EMA(200) must produce no signal, not an error.

### 2.2 Strategy Rules
- Long fires only when all four conditions are true; each condition false on its own means no signal. Mirror tests for short.
- Only **closed candles** are used; the forming candle is ignored.
- `touch_lookback` behaves as configured (touch, then a confirming candle).
- Spread filter and session filter block entries as configured.

### 2.3 Position Sizing
Formula: `lots = (balance x risk %) / (SL distance x value per price unit per lot)`, using `tick_value` / `tick_size` from `symbol_info`.

Worked examples to encode as tests (values are examples; real values come from the broker's `symbol_info`):

| Case | Balance | Risk | SL distance | Loss per lot | Raw lots | Final lots (rounded down to 0.01) |
|---|---|---|---|---|---|---|
| EURUSD, 1% | $10,000 | $100 | 0.0030 (30 pips) | $300 | 0.33333 | 0.33 |
| EURUSD, 0.5% | $10,000 | $50 | 0.0030 | $300 | 0.16667 | 0.16 |
| XAUUSD, 1% | $10,000 | $100 | $9.00 (1.5 x ATR 6.00) | $900 | 0.11111 | 0.11 |

Rules to test:
- The raw calculation is exact (to the 5th decimal); the **final lot** is rounded **down** to `volume_step` and clamped to `volume_max`.
- The real risk after rounding never exceeds the target risk.
- If the result is below `volume_min`, the trade is **skipped**, never rounded up.
- Correct behaviour for 0.5% (demo) and 1.0% (live) risk.

### 2.4 Risk Limits and Trade Management
- **Daily loss:** simulate a 2% equity drop; `risk.py` returns `stop_trading = True` and `close_all = True`, and the lock lasts until the next day.
- **Weekly loss:** simulate 5%; `stop_trading = True` until the following week.
- Locks survive a restart (state reloaded from SQLite).
- Max 2 concurrent trades enforced.
- Correlation rule: EUR/USD long + AUD/USD long is blocked; opposite directions are allowed.
- Trailing: SL to breakeven at +1R; 50% closed at +1.5R; remaining 50% trailed at 2 x ATR.
- Time stop fires after 15 candles.
- *Tests for the take-profit and staging interaction are written after PRD decision D1 is settled.*

### 2.5 Commands and Protocol (Python side)
- Command parser accepts only `PAUSE`, `RESUME`, `STATUS`, `RISK_CHECK`, `KILL_ALL`; unknown or malformed input is rejected with `COMMAND_ACK` / `REJECTED`.
- Commands past their expiry are ignored (10 minutes; `KILL_ALL` 2 minutes).
- The same `command_id` is never executed twice.
- `KILL_ALL` without `confirmed_at` is rejected.
- `RESUME` is refused while a daily/weekly/kill lock is active.
- Event payloads match the schema in ARCHITECTURE section 6 (required fields, UTC timestamps, `tp` optional).

### 2.6 Executor, Safety and Logging (MT5 mocked)
- Retries on requote / price changed / timeout (max 3, with backoff); no retry on "no money" or "market closed".
- An order without a stop loss is refused.
- Live mode is refused without `CONFIRM_LIVE` and a matching `LIVE_SERVER`.
- Log output never contains passwords, API keys or the Telegram token (including inside request URLs); account numbers are masked.

## 3. Gate 2: Backtesting (Historical Performance)
**Goal:** find out whether the strategy would have been profitable after realistic costs, without fooling ourselves.

### 3.1 Tool: Custom Backtest Runner
A candle-by-candle runner in `src/atmr/backtest/` that **reuses `strategy.py` and `risk.py` unchanged**, so the code being tested is the code that will trade. It includes a simulated broker that handles:
- spread, slippage and commission per symbol
- SL and exit checks inside each candle (if SL and a target are both possible in one candle, assume the **SL hit first**)
- partial closes, trailing, time stop
- the daily/weekly limits, max trades and correlation rule

**Test the runner itself** with tiny hand-made datasets where the correct result is calculated by hand, before trusting any backtest output.

### 3.2 Data
- Source: high-quality historical OHLCV for EUR/USD, XAU/USD and AUD/USD on H1 and H4 (MT5 history export or another reliable provider). Record the source and date range.
- Clean the data: remove duplicates, check gaps and weekend bars, verify time zones.
- Keep data out of Git (large files): add `data/` to `.gitignore`.
- Brokers limit history depth; if fewer than 5 years are available, get data from another source.

### 3.3 Costs (must be included in every backtest)
- Use a **per-symbol spread and slippage** from config, not one flat "+2 pips" (pips mean different things on Gold). Use your broker's real average spread, plus commission per lot.
- Run a **stress case at 2x the spread** as well.

### 3.4 Data Split (latest 5 years, split by time, never shuffled)
| Period | Use |
|---|---|
| Years 1-3 (about Oct 2021 to Sep 2024) | **Tune**: choose parameters |
| Year 4 (about Oct 2024 to Sep 2025) | **Validate**: check the tuned parameters on unseen data |
| Year 5 (about Oct 2025 to Sep 2026) | **Final test**: run **once**, untouched |

Exact dates are fixed when the data is downloaded. Rules:
- Tune only a small grid (for example RSI threshold, touch lookback, TP option) and prefer parameter regions where neighbouring values also work.
- Lock the parameters **before** validation, and again before the final test.
- If you change anything after seeing the final-test result, that data is burned and you need new data.
- Keep the tune / validate / test split; no parameter is chosen on validation or final-test data.

### 3.5 Pass Criteria
All must hold on validation **and** final test, with costs included:
| Metric | Requirement |
|---|---|
| Sharpe ratio (annualised, from daily equity returns) | **> 1.5** |
| Maximum drawdown | **<= 10%** of equity |
| Profit factor | **> 1.3** |
| Number of trades | At least 100 in total |
| Expectancy | Positive per trade |
| Cost stress | Still profitable at 2x spread |
| Concentration | No single symbol or year makes more than half the profit |
| Overfit check | Validation profit factor not more than about 30% below the tuning result |

Note: a 10% maximum drawdown is high compared with your own limits (2% daily, 5% weekly), which is why the runner applies those limits during the backtest. Watch for a Sharpe above 1.5 combined with very few trades; that is usually luck.

### 3.6 Backtest Report
Equity curve, per-symbol and per-year table, exit-reason breakdown (TP by RSI / middle band / RR, SL, time stop, limits), monthly returns, longest losing streak, and how often the loss limits triggered.

## 4. Gate 3: Integration Testing (The Ecosystem)
**Goal:** confirm the "Hands" (MT5), "Brain" (Python) and "Messenger" (n8n/Telegram) work together, safely. Run on the demo account.

| Area | Test | Pass condition |
|---|---|---|
| **MT5** | Connect, read account info, pull candles for all 3 symbols | Data returned |
| | Open, modify SL, partial close and close a minimum-lot demo order | Every step succeeds, SL always set |
| | Restart MT5 terminal, then the bot | Reconnects; no duplicate or lost trade |
| **Webhook integrity** | Trigger a dummy `TRADE_OPEN` in Python | n8n receives valid JSON, Telegram shows the formatted alert |
| | Send one sample of every event type | All render correctly (DESIGN_SYSTEM.md) |
| **Command loopback** | `/status` in Telegram | n8n queues it; bot polls; `STATUS_REPORT` returns the correct balance and positions to Telegram |
| | `/risk_check` | Correct daily/weekly drawdown returned |
| | `/pause` then `/resume` | No new entries while paused; open trades still managed; resume works; resume refused during a limit lock |
| **Kill switch** | `/kill_all` then `YES` within 60 s | Pending orders cancelled, all positions closed, `KILL_RESULT` sent, process exits with code 10, service does not restart, next start needs `--acknowledge-kill` |
| | `/kill_all` then another reply, or no reply | Cancelled; nothing closed |
| | `/kill_all` while the bot is stopped | n8n alerts within 2 minutes that it was not executed |
| **Security** | Request to n8n with a wrong `X-BOT-API-KEY` | Rejected (401) |
| | Telegram message from a different account | Ignored |
| | Port scan of the VPS from outside | Only RDP (3389) from your allowed IP; no bot port |
| | Read the logs | No secrets, no token in URLs |
| **Resilience** | Stop n8n, generate events, restart n8n | Outbox delivers everything, in order |
| | Critical event while n8n is down | Direct Telegram fallback still arrives |
| | Stop the bot | "BOT OFFLINE" alert within 15 minutes |
| | Disconnect the network for a few minutes | Bot reconnects and resumes |
| **Latency** | Timestamp signal, order sent, fill | Record and review. Targets: signal to `order_send` under 1 second; command round trip under the poll interval plus 5 seconds |

## 5. Gate 4: Paper Trading (Forward Testing)
**Goal:** test in the live market with a demo account.

- **Environment:** MT5 demo account on the AWS Windows instance, `TRADING_MODE=demo`, settings identical to the intended live setup.
- **Duration:** at least **4 weeks of continuous 24/5 operation** (forex is closed at weekends) **and at least 50 trades**; keep running until both are met.

**Live events the bot must handle (observe and record each):**
- **News spikes** (NFP, CPI, rate decisions): how does it behave? The PRD has no news filter; decide after the demo run whether one is needed.
- **Connection drops:** simulate a network failure and kill the MT5 terminal; the bot must reconnect and resume monitoring.
- **Broker requotes and rejections:** the executor retries or aborts gracefully and reports it.
- **Weekend gaps and market open.**
- **VPS reboot** (including Windows Update): MT5 and the bot auto-start correctly.

**Reviews**
- Daily: read the summary, errors and heartbeat gaps.
- Weekly: replay the same week through the backtest runner and compare trade by trade with what the demo bot did. Explain every difference (spread, slippage, timing).

### 5.1 Acceptance Criteria (matches PRD go-live criteria)
- 4+ weeks and 50+ trades
- Zero limit breaches; every trade had an SL; every exit reason matches the spec
- No unhandled crash in the last 2 weeks
- Heartbeat uptime of at least 99% during market hours
- Demo results not materially worse than the backtest for the same period
- SECURITY.md pre-live checklist passed, including the `/kill_all` test repeated on the VPS

## 6. Going Live
Only after all four gates pass: start at minimum size or 0.25% risk (PRD), review after the first weeks, then move toward the planned risk.

## 7. Commands
```
pytest                                 # unit tests
pytest --cov=src/atmr                  # coverage
pytest -m integration                  # needs MT5 demo running
python -m atmr.backtest --config config/backtest.yaml   # run a backtest
ruff check . && black --check .
```

## 8. Changelog
- **v2.0.0:** Merged your testing plan with the current design. Kept: Pyramid of Trust, four gates, pass criteria (Sharpe > 1.5, MaxDD <= 10%, PF > 1.3), spread buffer, walk-forward idea, news / disconnect / requote checks. Changed: EUR/MSD fixed to EUR/USD; sizing test checks raw and rounded lots; flat "+2 pips" replaced by per-symbol costs; tools set to a **custom backtest runner**; data split set to the **latest 5 years (3 tune, 1 validate, 1 final test)**; loopback test uses the polling flow; 24/7 became 24/5 with a 50-trade minimum. Added: runner self-tests, minimum trade count, cost stress, overfit checks, kill-switch, security and resilience tests, acceptance criteria, going-live step.
- **v1.0.0:** Original testing plan.
