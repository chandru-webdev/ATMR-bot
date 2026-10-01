# Security Policy: ATMR-Bot

**Version:** 2.0.0 | **Status:** Draft | **Last updated:** 2026-09-28
Applies to PRD v1.1.0 and ARCHITECTURE v2.0.1. This bot can place real orders with real money, so security is a first-class requirement.

## 1. Credential Management (The Golden Rule)
**NEVER hardcode sensitive information in source code.** All passwords, API keys and broker credentials live in a `.env` file that is excluded from Git. Load them with `python-dotenv`.

### 1.1 Environment Variables
| Variable | Purpose | Required |
|---|---|---|
| `MT5_LOGIN` | MT5 account number | Yes |
| `MT5_PASSWORD` | MT5 account password | Yes |
| `MT5_SERVER` | Broker server name | Yes |
| `TRADING_MODE` | `demo` (default) or `live` | Yes |
| `CONFIRM_LIVE` | Must equal `YES_I_UNDERSTAND` for live mode | Live only |
| `LIVE_SERVER` | Expected live server name (guard) | Live only |
| `N8N_BASE_URL` | Base URL of your n8n webhooks | Yes |
| `X_BOT_API_KEY` | Secret sent as `X-BOT-API-KEY` on every call to n8n | Yes |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather (direct fallback alerts) | Yes |
| `TELEGRAM_CHAT_ID` | Your chat ID | Yes |
| `EMAIL_USER` / `EMAIL_PASS` | SMTP trade-log emails (use an app password) | Optional |
| `TWILIO_SID` / `TWILIO_AUTH_TOKEN` | SMS/WhatsApp alerts (deferred in the PRD) | Optional |

Use random keys of at least 32 characters for `X_BOT_API_KEY`.

### 1.2 Git Hygiene
`.gitignore` must contain, **before the first commit**:
```
.env
*.log
logs/
__pycache__/
venv/
*.db
```
- The repo is **private**.
- Commit `.env.example` with fake placeholders only.
- If a secret is ever committed, treat it as leaked: rotate it immediately. Deleting the file does not remove it from Git history.
- Never paste real credentials into chats, screenshots or issue trackers.

## 2. Trading Safety Guards
- `TRADING_MODE` defaults to `demo`.
- Live mode needs all three: `TRADING_MODE=live`, `CONFIRM_LIVE=YES_I_UNDERSTAND`, and a connected server matching `LIVE_SERVER`. Otherwise the bot exits.
- Every order carries a stop loss. The bot refuses to send an order without one.
- Hard caps in code, not only config: max lot size, max 2 concurrent trades, daily 2% and weekly 5% drawdown limits.
- Circuit breaker: on the daily limit the bot closes all trades and locks entries until the next day. `/resume` cannot lift a limit lock.
- Start live at minimum size after meeting the PRD go-live criteria.

## 3. Network and Infrastructure Security
### 3.1 Outbound-Only Design
The bot **opens no inbound port**. Every connection starts from the bot (events to n8n, command polling from n8n, fallback alerts to Telegram). There is no FastAPI server and no port 8000.

### 3.2 Windows VPS Hardening (AWS EC2 Windows or similar)
MT5 needs Windows, so remote access is Remote Desktop (RDP), not SSH.
- **IP whitelisting:** the AWS Security Group (or provider firewall) must allow inbound **port 3389 (RDP) only from your own IP**, or only through a VPN. If your home IP changes, update the rule.
- **No other inbound ports** open.
- Strong unique Administrator password; Network Level Authentication on; rename or disable the default Administrator account if possible.
- Enable Windows Update; schedule reboots outside market hours.
- Keep only MT5, Python and the bot on the machine.
- Run the bot under a normal (non-admin) user where practical.
- (If you ever use a Linux server for n8n: SSH on port 22 only from your IP, **SSH keys only, root login and password login disabled**.)

### 3.3 n8n Server
- HTTPS only; keep n8n updated.
- Protect the n8n editor with a strong password and 2FA; do not expose the editor to the public if you self-host (restrict by IP or VPN). Only the webhook paths need to be reachable.
- The Telegram credential inside n8n is stored in n8n credentials, not in workflow text.

### 3.4 Webhook and API Key
- **Secret token validation:** every request from the bot to n8n (both `POST /events` and `GET /commands`) includes the header `X-BOT-API-KEY`. n8n rejects any request without the correct key (HTTP 401) and does not reveal why.
- HTTPS only; timeouts of 5 seconds.
- Rotate the key if it leaks, and at least every 6 months.

## 4. Remote Control Security
### 4.1 Rules
- **Chat ID allowlist:** n8n ignores every Telegram message that does not come from your chat ID.
- **Command allowlist:** only `PAUSE`, `RESUME`, `STATUS`, `RISK_CHECK`, `KILL_ALL`. Anything else is rejected and reported.
- **Input validation (no command injection):** commands are parsed as a strict enum from JSON. The bot never passes command text to a shell, `eval`, `exec`, `os.system` or a SQL string. Unknown fields are ignored; malformed payloads are rejected.
- **Expiry (TTL):** commands expire (10 minutes; `KILL_ALL` 2 minutes) so a stale command cannot fire later.
- **Idempotency and acknowledgement:** processed command IDs are stored; every command gets a `COMMAND_ACK`.
- Remote commands can never open trades, change risk values, or lift a limit lock.

### 4.2 The Kill Switch Protocol (`/kill_all`)
A panic mode accessible via Telegram.

**Confirmation**
1. You send `/kill_all`.
2. n8n replies: "Send YES within 60 seconds to close ALL positions and STOP the bot."
3. Only an exact `YES` from your chat ID within 60 seconds queues the `KILL_ALL` command. Any other reply cancels it.

**Bot actions, in order**
1. Lock new entries immediately.
2. Cancel all pending orders.
3. Close all open positions (scope set by `kill_scope` in config: `account` = every position on the account, default; `bot` = only positions with the bot's magic number).
4. Verify nothing is left; retry failed closes up to 3 times over about 15 seconds.
5. Send a confirmation alert (`KILL_RESULT`: cancelled, closed, failed) through n8n **and** directly to Telegram.
6. Save `KILLED` in SQLite and **stop the bot process completely** (exit code 10).

**After a kill**
- The service must take **no automatic restart** on exit code 10.
- On the next start the bot refuses to trade until you run it with `--acknowledge-kill`, so an accidental restart cannot resume trading.
- If any position fails to close, the alert says "KILL INCOMPLETE, close manually in MT5".

**Limits you must know**
- The command travels Telegram -> n8n -> bot polling (up to about 5 seconds). If the bot, n8n or the internet is down it will not execute; n8n alerts you if a `KILL_ALL` is not acknowledged within 2 minutes.
- **The MT5 mobile app remains your real emergency button.** Keep it installed and logged in.

## 5. Error Sanitization and Logging
- Logs record what went wrong (for example `Connection Timeout`) but **never** passwords, API keys or tokens, even during a crash.
- The Telegram bot token appears inside request URLs, so exceptions from HTTP libraries must be sanitized before logging.
- Wrap top-level exception handling to redact any value loaded from `.env`.
- Mask account numbers (last 4 digits only).
- **Log rotation:** rotate weekly, with a size cap, and keep 8 weeks, to prevent disk overflow on the VPS.

## 6. Audit and Monitoring
- **Daily summary** via Telegram every 24 hours: trades and account equity.
- **Heartbeat** every 5 minutes; n8n raises "BOT OFFLINE" after 15 minutes of silence.
- **Audit log:** every command (time, chat ID, result) and every mode/lock change is stored in the `events` table.
- n8n counts rejected requests (wrong key, wrong chat ID) and alerts you on repeated failures.

## 7. Accounts and Machines
- Use a **demo account** for all development.
- Do not run the bot or store credentials on an employer-managed laptop; use a personal machine and check your employer's policy on personal projects.
- Enable 2FA on GitHub, the broker portal, Telegram, the VPS provider and n8n.

## 8. Dependencies
- Pin versions in `requirements.txt`; update deliberately.
- Install only the official `MetaTrader5` package; check names carefully to avoid look-alike packages.
- Run `pip audit` before each release.

## 9. Incident Response
1. Send `/kill_all`, or use the MT5 mobile app if the bot is unreachable; stop the bot service.
2. Review open positions in MT5 and close manually if needed.
3. Change the MT5 password; rotate the Telegram token (BotFather), `X_BOT_API_KEY` and any SMTP/Twilio secrets.
4. Review logs and the `events` table for what happened.
5. If the VPS may be compromised: rebuild it, do not reuse it.

## 10. Pre-Live Checklist
- [ ] `.env` never appeared in Git history; repo is private
- [ ] 2FA on all accounts
- [ ] RDP restricted to your IP or VPN; Windows updates on
- [ ] Bot has no inbound ports
- [ ] Wrong `X-BOT-API-KEY` is rejected by n8n
- [ ] Messages from another Telegram account are ignored
- [ ] Live-mode guard tested (refuses without confirm flag)
- [ ] `/kill_all` tested on demo: confirm works, wrong reply cancels, timeout cancels, positions close, process stops, service does not restart
- [ ] Heartbeat alert tested by stopping the bot
- [ ] Logs checked: no secrets, no token in URLs

## 11. Changelog
- **v2.0.0:** Merged your policy with the current design. Kept: `.env` rule and variable list, Git hygiene (`*.db` added), error sanitization, input validation, `X-BOT-API-KEY`, `/kill_all` kill switch, daily summary, weekly log rotation. Changed: SSH port 22 became RDP 3389 (Windows), FastAPI port 8000 rule replaced by outbound-only design, API key direction now bot -> n8n, `/kill_all` given a 60-second YES confirmation, 2-minute expiry and no-auto-restart. Added: live-mode guard, command allowlist, TTL/idempotency, 2FA, dependency rules, incident response, pre-live checklist.
- **v1.0.0:** Original security policy.
