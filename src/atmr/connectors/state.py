"""SQLite state persistence layer (StateStore).

Implements ARCHITECTURE.md Section 8 and SECURITY.md:
- trades: position tracking, stage transitions, and restart recovery
- daily_stats: daily PnL and trade counts
- bot_state: paused state, drawdown locks (DAILY/WEEKLY), and KILLED lock
- outbox: resilient event delivery queue for n8n
- commands_seen: command deduplication and idempotency
- events: audit log of operations and alerts
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atmr.engine.models import Direction, Position, PositionStage

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS trades (
    ticket INTEGER PRIMARY KEY, symbol TEXT NOT NULL, direction TEXT NOT NULL,
    lot REAL NOT NULL, entry_price REAL NOT NULL, sl_price REAL NOT NULL,
    stage TEXT NOT NULL DEFAULT 'NONE', opened_at TEXT NOT NULL, closed_at TEXT,
    pnl REAL, exit_reason TEXT, candles_open INTEGER NOT NULL DEFAULT 0,
    original_lot REAL, initial_sl_price REAL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY, start_equity REAL NOT NULL, pnl REAL NOT NULL DEFAULT 0.0,
    trades INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), paused INTEGER NOT NULL DEFAULT 0,
    lock_type TEXT, locked_until TEXT, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    next_try_at TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands_seen (
    command_id TEXT PRIMARY KEY, status TEXT NOT NULL, processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
    level TEXT NOT NULL, message TEXT NOT NULL, details TEXT
);
"""


def _iso_utc(dt: datetime) -> str:
    """Format datetime as UTC ISO 8601 string."""
    u = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return u.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_utc(text: str) -> datetime:
    """Parse ISO 8601 string to timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@dataclass(frozen=True)
class BotStateRecord:
    """Snapshot of bot control flags and locks."""

    paused: bool
    lock_type: str | None
    locked_until: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class OutboxRecord:
    """Queued event waiting for delivery to n8n."""

    event_id: str
    payload: str
    attempts: int
    next_try_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class DailyStatsRecord:
    """Summary of trading performance for a single date."""

    date: str
    start_equity: float
    pnl: float
    trades: int
    wins: int
    losses: int


@dataclass(frozen=True)
class AuditLogRecord:
    """Audit entry recorded in the events table."""

    id: int
    timestamp: datetime
    level: str
    message: str
    details: str | None


class StateStore:
    """Thread-safe SQLite storage for bot state, trades, queue, and audit logs."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        """Initialize the SQLite database connection and schema."""
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode; statements execute immediately
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def __enter__(self) -> StateStore:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit: close connection."""
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def _init_db(self) -> None:
        """Create tables and ensure singleton bot_state row exists."""
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            now_iso = _iso_utc(datetime.now(UTC))
            sql = (
                "INSERT OR IGNORE INTO bot_state (id, paused, lock_type, locked_until, updated_at) "
                "VALUES (1, 0, NULL, NULL, ?);"
            )
            self._conn.execute(sql, (now_iso,))

    # -------------------------------------------------------------------------
    # Trades / Positions
    # -------------------------------------------------------------------------

    def save_position(self, position: Position) -> None:
        """Insert or replace an open position record."""
        opened_iso = _iso_utc(position.opened_at)
        sql = (
            "INSERT INTO trades (ticket, symbol, direction, lot, entry_price, sl_price, stage, "
            "opened_at, candles_open, original_lot, initial_sl_price) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ticket) DO UPDATE SET "
            "lot = excluded.lot, sl_price = excluded.sl_price, stage = excluded.stage, "
            "candles_open = excluded.candles_open, original_lot = excluded.original_lot, "
            "initial_sl_price = excluded.initial_sl_price;"
        )
        with self._lock:
            self._conn.execute(
                sql,
                (
                    position.ticket,
                    position.symbol,
                    str(position.direction),
                    position.lot,
                    position.entry_price,
                    position.sl_price,
                    str(position.stage),
                    opened_iso,
                    position.candles_open,
                    position.original_lot,
                    position.initial_sl_price,
                ),
            )

    def update_position(
        self,
        ticket: int,
        *,
        lot: float | None = None,
        sl_price: float | None = None,
        stage: PositionStage | str | None = None,
        candles_open: int | None = None,
    ) -> None:
        """Update dynamic fields of an existing position."""
        updates: list[str] = []
        params: list[Any] = []
        if lot is not None:
            updates.append("lot = ?")
            params.append(lot)
        if sl_price is not None:
            updates.append("sl_price = ?")
            params.append(sl_price)
        if stage is not None:
            updates.append("stage = ?")
            params.append(str(stage))
        if candles_open is not None:
            updates.append("candles_open = ?")
            params.append(candles_open)
        if not updates:
            return

        params.append(ticket)
        with self._lock:
            self._conn.execute(f"UPDATE trades SET {', '.join(updates)} WHERE ticket = ?;", params)

    def close_trade(self, ticket: int, closed_at: datetime, pnl: float, exit_reason: str) -> None:
        """Mark a trade as closed and record final PnL and exit reason."""
        closed_iso = _iso_utc(closed_at)
        sql = "UPDATE trades SET closed_at = ?, pnl = ?, exit_reason = ? WHERE ticket = ?;"
        with self._lock:
            self._conn.execute(sql, (closed_iso, pnl, exit_reason, ticket))

    def get_open_positions(self) -> tuple[Position, ...]:
        """Retrieve all currently open positions (closed_at IS NULL)."""
        sql = """
            SELECT ticket, symbol, direction, lot, entry_price, sl_price,
                   stage, opened_at, candles_open, original_lot, initial_sl_price
            FROM trades WHERE closed_at IS NULL ORDER BY opened_at ASC;
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()

        return tuple(
            Position(
                ticket=r["ticket"],
                symbol=r["symbol"],
                direction=Direction(r["direction"]),
                lot=r["lot"],
                entry_price=r["entry_price"],
                sl_price=r["sl_price"],
                opened_at=_parse_iso_utc(r["opened_at"]),
                stage=PositionStage(r["stage"]),
                candles_open=r["candles_open"],
                original_lot=r["original_lot"],
                initial_sl_price=r["initial_sl_price"],
            )
            for r in rows
        )

    def get_trade(self, ticket: int) -> dict[str, Any] | None:
        """Retrieve raw record for a specific trade ticket."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM trades WHERE ticket = ?;", (ticket,)).fetchone()
        return dict(row) if row else None

    # -------------------------------------------------------------------------
    # Bot State & Locks
    # -------------------------------------------------------------------------

    def get_bot_state(self) -> BotStateRecord:
        """Retrieve the singleton bot state record."""
        with self._lock:
            row = self._conn.execute(
                "SELECT paused, lock_type, locked_until, updated_at FROM bot_state WHERE id = 1;"
            ).fetchone()

        locked_until = _parse_iso_utc(row["locked_until"]) if row["locked_until"] else None
        return BotStateRecord(
            paused=bool(row["paused"]),
            lock_type=row["lock_type"],
            locked_until=locked_until,
            updated_at=_parse_iso_utc(row["updated_at"]),
        )

    def set_paused(self, paused: bool, now: datetime | None = None) -> None:
        """Set or clear the paused flag."""
        now_dt = now or datetime.now(UTC)
        sql = "UPDATE bot_state SET paused = ?, updated_at = ? WHERE id = 1;"
        with self._lock:
            self._conn.execute(sql, (1 if paused else 0, _iso_utc(now_dt)))

    def set_lock(
        self,
        lock_type: str | None,
        locked_until: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        """Set a circuit-breaker or kill lock."""
        now_dt = now or datetime.now(UTC)
        until_iso = _iso_utc(locked_until) if locked_until else None
        sql = "UPDATE bot_state SET lock_type = ?, locked_until = ?, updated_at = ? WHERE id = 1;"
        with self._lock:
            self._conn.execute(sql, (lock_type, until_iso, _iso_utc(now_dt)))

    def clear_lock(self, now: datetime | None = None) -> None:
        """Clear any active lock."""
        self.set_lock(lock_type=None, locked_until=None, now=now)

    def is_killed(self) -> bool:
        """Check if the bot is locked in KILLED state."""
        return self.get_bot_state().lock_type == "KILLED"

    def acknowledge_kill(self, now: datetime | None = None) -> bool:
        """Acknowledge a past kill switch invocation (--acknowledge-kill flag)."""
        with self._lock:
            row = self._conn.execute("SELECT lock_type FROM bot_state WHERE id = 1;").fetchone()
            if row and row["lock_type"] == "KILLED":
                now_dt = now or datetime.now(UTC)
                sql = (
                    "UPDATE bot_state SET lock_type = NULL, locked_until = NULL, "
                    "updated_at = ? WHERE id = 1;"
                )
                self._conn.execute(sql, (_iso_utc(now_dt),))
                return True
        return False

    # -------------------------------------------------------------------------
    # Daily Statistics
    # -------------------------------------------------------------------------

    def record_daily_start(self, date_str: str, start_equity: float) -> None:
        """Record start equity for a new trading day if not already recorded."""
        sql = (
            "INSERT OR IGNORE INTO daily_stats (date, start_equity, pnl, trades, wins, losses) "
            "VALUES (?, ?, 0.0, 0, 0, 0);"
        )
        with self._lock:
            self._conn.execute(sql, (date_str, start_equity))

    def record_trade_result(
        self,
        date_str: str,
        pnl: float,
        is_win: bool | None = None,
    ) -> None:
        """Update daily cumulative PnL, trade count, and win/loss totals."""
        win_delta = 1 if is_win is True else 0
        loss_delta = 1 if is_win is False else 0
        sql = (
            "UPDATE daily_stats SET pnl = pnl + ?, trades = trades + 1, "
            "wins = wins + ?, losses = losses + ? WHERE date = ?;"
        )
        with self._lock:
            self._conn.execute(sql, (pnl, win_delta, loss_delta, date_str))

    def get_daily_stats(self, date_str: str) -> DailyStatsRecord | None:
        """Retrieve daily statistics for a specific date."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM daily_stats WHERE date = ?;", (date_str,)
            ).fetchone()
        if not row:
            return None
        return DailyStatsRecord(
            date=row["date"],
            start_equity=row["start_equity"],
            pnl=row["pnl"],
            trades=row["trades"],
            wins=row["wins"],
            losses=row["losses"],
        )

    # -------------------------------------------------------------------------
    # Outbox Queue
    # -------------------------------------------------------------------------

    def enqueue_outbox(
        self,
        event_id: str,
        payload: str | dict[str, Any],
        next_try_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        """Enqueue an event to be sent to n8n."""
        now_dt = now or datetime.now(UTC)
        next_dt = next_try_at or now_dt
        payload_str = payload if isinstance(payload, str) else json.dumps(payload)
        sql = (
            "INSERT INTO outbox (event_id, payload, attempts, next_try_at, created_at) "
            "VALUES (?, ?, 0, ?, ?) ON CONFLICT(event_id) DO UPDATE SET "
            "payload = excluded.payload, next_try_at = excluded.next_try_at;"
        )
        with self._lock:
            self._conn.execute(sql, (event_id, payload_str, _iso_utc(next_dt), _iso_utc(now_dt)))

    def get_pending_outbox(
        self,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[OutboxRecord]:
        """Fetch pending outbox events whose next_try_at is <= now, ordered FIFO."""
        now_dt = now or datetime.now(UTC)
        sql = (
            "SELECT event_id, payload, attempts, next_try_at, created_at FROM outbox "
            "WHERE next_try_at <= ? ORDER BY created_at ASC LIMIT ?;"
        )
        with self._lock:
            rows = self._conn.execute(sql, (_iso_utc(now_dt), limit)).fetchall()

        return [
            OutboxRecord(
                event_id=row["event_id"],
                payload=row["payload"],
                attempts=row["attempts"],
                next_try_at=_parse_iso_utc(row["next_try_at"]),
                created_at=_parse_iso_utc(row["created_at"]),
            )
            for row in rows
        ]

    def mark_outbox_failed(self, event_id: str, next_try_at: datetime) -> None:
        """Increment delivery attempt count and update next scheduled attempt."""
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, next_try_at = ? WHERE event_id = ?;",
                (_iso_utc(next_try_at), event_id),
            )

    def remove_outbox(self, event_id: str) -> None:
        """Remove an event from the outbox after successful delivery."""
        with self._lock:
            self._conn.execute("DELETE FROM outbox WHERE event_id = ?;", (event_id,))

    # -------------------------------------------------------------------------
    # Commands Seen (Idempotency)
    # -------------------------------------------------------------------------

    def is_command_seen(self, command_id: str) -> bool:
        """Check if a remote command has already been processed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM commands_seen WHERE command_id = ?;", (command_id,)
            ).fetchone()
            return row is not None

    def record_command(
        self,
        command_id: str,
        status: str,
        processed_at: datetime | None = None,
    ) -> None:
        """Record a processed command to ensure idempotency."""
        proc_dt = processed_at or datetime.now(UTC)
        sql = (
            "INSERT INTO commands_seen (command_id, status, processed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(command_id) DO UPDATE SET status = excluded.status, "
            "processed_at = excluded.processed_at;"
        )
        with self._lock:
            self._conn.execute(sql, (command_id, status, _iso_utc(proc_dt)))

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        """Retrieve record for a seen command."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM commands_seen WHERE command_id = ?;", (command_id,)
            ).fetchone()
        return dict(row) if row else None

    # -------------------------------------------------------------------------
    # Events (Audit Log)
    # -------------------------------------------------------------------------

    def record_event(
        self,
        level: str,
        message: str,
        timestamp: datetime | None = None,
        details: str | None = None,
    ) -> None:
        """Record an entry in the events audit log."""
        ts_dt = timestamp or datetime.now(UTC)
        sql = "INSERT INTO events (timestamp, level, message, details) VALUES (?, ?, ?, ?);"
        with self._lock:
            self._conn.execute(sql, (_iso_utc(ts_dt), level, message, details))

    def get_events(
        self,
        limit: int = 50,
        level: str | None = None,
    ) -> list[AuditLogRecord]:
        """Retrieve recent audit log entries in reverse chronological order."""
        query = "SELECT id, timestamp, level, message, details FROM events"
        params: list[Any] = []
        if level:
            query += " WHERE level = ?"
            params.append(level)
        query += " ORDER BY id DESC LIMIT ?;"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        return [
            AuditLogRecord(
                id=r["id"],
                timestamp=_parse_iso_utc(r["timestamp"]),
                level=r["level"],
                message=r["message"],
                details=r["details"],
            )
            for r in rows
        ]
