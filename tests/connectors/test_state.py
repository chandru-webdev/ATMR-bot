"""Unit tests for SQLite StateStore.

Validates schema initialization, trade persistence and recovery, bot state & locks,
daily statistics, outbox event queue, seen command idempotency, and audit logging.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atmr.connectors.state import (
    AuditLogRecord,
    BotStateRecord,
    DailyStatsRecord,
    OutboxRecord,
    StateStore,
)
from atmr.engine.models import Direction, Position, PositionStage

_TS = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    """Provide a fresh StateStore backed by a temporary file."""
    db_file = tmp_path / "test_atmr.db"
    store = StateStore(db_file)
    yield store
    store.close()


def test_init_creates_default_bot_state(store: StateStore) -> None:
    """Verify that initialization creates a singleton bot_state row."""
    state = store.get_bot_state()
    assert isinstance(state, BotStateRecord)
    assert state.paused is False
    assert state.lock_type is None
    assert state.locked_until is None


def test_init_is_idempotent(tmp_path: Path) -> None:
    """Re-opening the database should preserve existing state without error."""
    db_file = tmp_path / "idempotent.db"
    with StateStore(db_file) as s1:
        s1.set_paused(True)

    with StateStore(db_file) as s2:
        state = s2.get_bot_state()
        assert state.paused is True


def test_save_and_get_open_positions(store: StateStore) -> None:
    """Save an open position and recover it faithfully as a Position instance."""
    pos1 = Position(
        ticket=1001,
        symbol="XAUUSD",
        direction=Direction.LONG,
        lot=0.10,
        entry_price=2650.50,
        sl_price=2640.00,
        opened_at=_TS,
        stage=PositionStage.NONE,
        candles_open=1,
        original_lot=0.10,
        initial_sl_price=2640.00,
    )
    store.save_position(pos1)

    open_positions = store.get_open_positions()
    assert len(open_positions) == 1
    recovered = open_positions[0]
    assert recovered.ticket == 1001
    assert recovered.symbol == "XAUUSD"
    assert recovered.direction is Direction.LONG
    assert recovered.lot == 0.10
    assert recovered.entry_price == 2650.50
    assert recovered.sl_price == 2640.00
    assert recovered.opened_at == _TS
    assert recovered.stage is PositionStage.NONE
    assert recovered.candles_open == 1
    assert recovered.original_lot == 0.10
    assert recovered.initial_sl_price == 2640.00


def test_update_position(store: StateStore) -> None:
    """Update stop-loss, stage, and volume of an open position."""
    pos = Position(
        ticket=1002,
        symbol="EURUSD",
        direction=Direction.SHORT,
        lot=0.20,
        entry_price=1.1000,
        sl_price=1.1050,
        opened_at=_TS,
        stage=PositionStage.NONE,
        original_lot=0.20,
        initial_sl_price=1.1050,
    )
    store.save_position(pos)

    store.update_position(
        ticket=1002,
        sl_price=1.1000,
        stage=PositionStage.BREAKEVEN,
        candles_open=3,
        lot=0.10,
    )

    open_positions = store.get_open_positions()
    assert len(open_positions) == 1
    updated = open_positions[0]
    assert updated.sl_price == 1.1000
    assert updated.stage is PositionStage.BREAKEVEN
    assert updated.candles_open == 3
    assert updated.lot == 0.10
    assert updated.original_lot == 0.20


def test_close_trade(store: StateStore) -> None:
    """Closing a trade removes it from open positions and records pnl."""
    pos = Position(
        ticket=1003,
        symbol="XAUUSD",
        direction=Direction.LONG,
        lot=0.10,
        entry_price=2650.00,
        sl_price=2640.00,
        opened_at=_TS,
    )
    store.save_position(pos)
    assert len(store.get_open_positions()) == 1

    closed_at = _TS + timedelta(hours=2)
    store.close_trade(ticket=1003, closed_at=closed_at, pnl=150.00, exit_reason="TP_RSI")

    # Should no longer be in open positions
    assert len(store.get_open_positions()) == 0

    # Trade details should be retrievable
    trade = store.get_trade(1003)
    assert trade is not None
    assert trade["ticket"] == 1003
    assert trade["closed_at"] is not None
    assert trade["pnl"] == 150.00
    assert trade["exit_reason"] == "TP_RSI"


def test_bot_state_pause_resume(store: StateStore) -> None:
    """Test pausing and resuming trading."""
    store.set_paused(True)
    assert store.get_bot_state().paused is True

    store.set_paused(False)
    assert store.get_bot_state().paused is False


def test_bot_state_locks(store: StateStore) -> None:
    """Test setting and clearing daily and weekly locks."""
    until = _TS + timedelta(days=1)
    store.set_lock(lock_type="DAILY", locked_until=until)

    state = store.get_bot_state()
    assert state.lock_type == "DAILY"
    assert state.locked_until == until

    store.clear_lock()
    state2 = store.get_bot_state()
    assert state2.lock_type is None
    assert state2.locked_until is None


def test_kill_switch_persistence_and_acknowledgement(store: StateStore) -> None:
    """Test setting KILLED state and acknowledging it."""
    assert store.is_killed() is False

    store.set_lock("KILLED")
    assert store.is_killed() is True
    assert store.get_bot_state().lock_type == "KILLED"

    # Acknowledge kill clears the lock and returns True
    acked = store.acknowledge_kill()
    assert acked is True
    assert store.is_killed() is False
    assert store.get_bot_state().lock_type is None

    # Subsequent acknowledgement returns False
    assert store.acknowledge_kill() is False


def test_daily_stats_lifecycle(store: StateStore) -> None:
    """Test tracking start equity, pnl delta, trades, wins, and losses."""
    date_str = "2026-10-02"
    store.record_daily_start(date_str, start_equity=10000.0)

    stats = store.get_daily_stats(date_str)
    assert stats is not None
    assert isinstance(stats, DailyStatsRecord)
    assert stats.date == date_str
    assert stats.start_equity == 10000.0
    assert stats.pnl == 0.0
    assert stats.trades == 0
    assert stats.wins == 0
    assert stats.losses == 0

    # Record winning trade
    store.record_trade_result(date_str, pnl=250.0, is_win=True)
    stats1 = store.get_daily_stats(date_str)
    assert stats1 is not None
    assert stats1.pnl == 250.0
    assert stats1.trades == 1
    assert stats1.wins == 1
    assert stats1.losses == 0

    # Record losing trade
    store.record_trade_result(date_str, pnl=-100.0, is_win=False)
    stats2 = store.get_daily_stats(date_str)
    assert stats2 is not None
    assert stats2.pnl == 150.0
    assert stats2.trades == 2
    assert stats2.wins == 1
    assert stats2.losses == 1


def test_outbox_queue_fifo_and_retry(store: StateStore) -> None:
    """Test enqueueing, filtering by next_try_at, retry updates, and removal."""
    t0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=10)
    t2 = t0 + timedelta(seconds=20)

    store.enqueue_outbox(
        event_id="evt-1",
        payload=json.dumps({"event": "TRADE_OPEN", "ticket": 1}),
        next_try_at=t0,
        now=t0,
    )
    store.enqueue_outbox(
        event_id="evt-2",
        payload=json.dumps({"event": "TRADE_OPEN", "ticket": 2}),
        next_try_at=t2,
        now=t0,
    )

    # At t0, only evt-1 is pending
    pending = store.get_pending_outbox(now=t0)
    assert len(pending) == 1
    assert pending[0].event_id == "evt-1"
    assert pending[0].attempts == 0

    # Mark evt-1 failed and retry at t2
    store.mark_outbox_failed("evt-1", next_try_at=t2)
    assert len(store.get_pending_outbox(now=t1)) == 0

    # At t2, both evt-1 and evt-2 are ready; order is preserved
    ready = store.get_pending_outbox(now=t2)
    assert len(ready) == 2
    assert isinstance(ready[0], OutboxRecord)
    assert ready[0].event_id == "evt-1"
    assert ready[0].attempts == 1
    assert ready[1].event_id == "evt-2"

    # Remove evt-1 after success
    store.remove_outbox("evt-1")
    ready_after = store.get_pending_outbox(now=t2)
    assert len(ready_after) == 1
    assert ready_after[0].event_id == "evt-2"


def test_commands_seen_idempotency(store: StateStore) -> None:
    """Test command deduplication tracking."""
    cmd_id = "cmd-12345"
    assert store.is_command_seen(cmd_id) is False

    store.record_command(cmd_id, status="EXECUTED", processed_at=_TS)
    assert store.is_command_seen(cmd_id) is True

    record = store.get_command(cmd_id)
    assert record is not None
    assert record["command_id"] == cmd_id
    assert record["status"] == "EXECUTED"


def test_audit_events_logging(store: StateStore) -> None:
    """Test recording and querying audit log events."""
    store.record_event(level="INFO", message="Bot started", timestamp=_TS)
    store.record_event(
        level="WARN",
        message="Spread high",
        timestamp=_TS + timedelta(seconds=1),
        details='{"spread": 45}',
    )

    events = store.get_events(limit=10)
    assert len(events) == 2
    assert isinstance(events[0], AuditLogRecord)
    assert events[0].level == "WARN"  # Most recent first
    assert events[0].message == "Spread high"
    assert events[0].details == '{"spread": 45}'
    assert events[1].level == "INFO"

    # Filter by level
    warn_events = store.get_events(limit=10, level="WARN")
    assert len(warn_events) == 1
    assert warn_events[0].level == "WARN"
