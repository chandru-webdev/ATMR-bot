"""Unit tests for time_utils.py."""

from __future__ import annotations

from datetime import UTC, datetime

from atmr.utils.time_utils import (
    broker_time_to_utc,
    get_day_start_utc,
    get_next_daily_reset_utc,
    get_next_weekly_reset_utc,
    get_week_start_utc,
    utc_date_str,
    utc_to_broker_time,
)


def test_broker_time_to_utc_numeric_timestamp() -> None:
    # 2026-10-10 15:00:00 UTC epoch timestamp = 1791644400
    # If broker server time is 15:00 with offset +2 hours, true UTC is 13:00
    broker_ts = 1791644400.0
    utc_dt = broker_time_to_utc(broker_ts, offset_hours=2.0)
    assert utc_dt.tzinfo == UTC
    assert utc_dt.hour == 13
    assert utc_dt.minute == 0


def test_broker_time_to_utc_datetime_object() -> None:
    broker_dt = datetime(2026, 10, 10, 15, 0, 0)
    utc_dt = broker_time_to_utc(broker_dt, offset_hours=2.0)
    assert utc_dt.tzinfo == UTC
    assert utc_dt.hour == 13
    assert utc_dt.day == 10


def test_utc_to_broker_time() -> None:
    utc_dt = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)
    broker_dt = utc_to_broker_time(utc_dt, offset_hours=2.0)
    assert broker_dt.hour == 15


def test_utc_date_str() -> None:
    dt = datetime(2026, 10, 10, 23, 30, 0, tzinfo=UTC)
    assert utc_date_str(dt) == "2026-10-10"


def test_daily_resets_utc() -> None:
    dt = datetime(2026, 10, 10, 14, 30, 0, tzinfo=UTC)  # Saturday
    day_start = get_day_start_utc(dt)
    assert day_start == datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC)

    next_reset = get_next_daily_reset_utc(dt)
    assert next_reset == datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC)


def test_weekly_resets_utc() -> None:
    # 2026-10-10 is Saturday.
    # Current week's Monday was 2026-10-05.
    # Next weekly reset (next Monday) is 2026-10-12.
    dt = datetime(2026, 10, 10, 14, 30, 0, tzinfo=UTC)
    week_start = get_week_start_utc(dt)
    assert week_start == datetime(2026, 10, 5, 0, 0, 0, tzinfo=UTC)

    next_reset = get_next_weekly_reset_utc(dt)
    assert next_reset == datetime(2026, 10, 12, 0, 0, 0, tzinfo=UTC)
