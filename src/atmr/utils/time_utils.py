"""UTC time conversion and trading session reset utilities.

Provides deterministic UTC normalization for MT5 broker timestamps,
candle time indexing, and daily/weekly circuit-breaker resets.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def broker_time_to_utc(
    broker_time: float | int | datetime,
    offset_hours: float,
) -> datetime:
    """
    Convert a broker server timestamp or datetime to a timezone-aware UTC datetime.

    MT5 timestamps (rates, ticks, position open times) are in broker server time.
    To normalize to UTC: utc_time = broker_time - (offset_hours * 3600).

    Args:
        broker_time: Epoch seconds or datetime in broker server time.
        offset_hours: Broker server UTC offset in hours (e.g. +2.0 for EET).

    Returns:
        datetime: Timezone-aware UTC datetime.
    """
    offset_delta = timedelta(hours=offset_hours)
    if isinstance(broker_time, (int, float)):
        # Convert numeric broker epoch to UTC datetime
        dt = datetime.fromtimestamp(float(broker_time), tz=UTC)
        return dt - offset_delta

    if broker_time.tzinfo is None:
        dt = broker_time.replace(tzinfo=UTC)
    else:
        dt = broker_time.astimezone(UTC)
    return dt - offset_delta


def utc_to_broker_time(
    utc_time: float | int | datetime,
    offset_hours: float,
) -> datetime:
    """
    Convert a UTC timestamp or datetime to broker server time.

    Args:
        utc_time: Epoch seconds or datetime in UTC.
        offset_hours: Broker server UTC offset in hours.

    Returns:
        datetime: Timezone-aware datetime in broker server time.
    """
    offset_delta = timedelta(hours=offset_hours)
    if isinstance(utc_time, (int, float)):
        dt = datetime.fromtimestamp(float(utc_time), tz=UTC)
        return dt + offset_delta

    if utc_time.tzinfo is None:
        dt = utc_time.replace(tzinfo=UTC)
    else:
        dt = utc_time.astimezone(UTC)
    return dt + offset_delta


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(UTC)


def utc_date_str(dt: datetime | None = None) -> str:
    """
    Return ISO date string (YYYY-MM-DD) in UTC.

    Args:
        dt: Optional datetime. If None, uses current UTC time.

    Returns:
        str: Date string formatted as YYYY-MM-DD.
    """
    now = dt or datetime.now(UTC)
    utc_dt = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return utc_dt.strftime("%Y-%m-%d")


def get_day_start_utc(dt: datetime | None = None) -> datetime:
    """
    Return 00:00:00 UTC for the given day.

    Args:
        dt: Reference datetime (defaults to current UTC time).

    Returns:
        datetime: Start of the day (00:00:00) in UTC.
    """
    now = dt or datetime.now(UTC)
    utc_dt = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return utc_dt.replace(hour=0, minute=0, second=0, microsecond=0)


def get_next_daily_reset_utc(dt: datetime | None = None) -> datetime:
    """
    Return 00:00:00 UTC of the following day for daily limit reset.

    Args:
        dt: Reference datetime (defaults to current UTC time).

    Returns:
        datetime: Next daily reset time in UTC.
    """
    return get_day_start_utc(dt) + timedelta(days=1)


def get_week_start_utc(dt: datetime | None = None) -> datetime:
    """
    Return 00:00:00 UTC of Monday for the current week.

    Args:
        dt: Reference datetime (defaults to current UTC time).

    Returns:
        datetime: Monday 00:00:00 UTC of the current week.
    """
    day_start = get_day_start_utc(dt)
    # weekday(): Monday is 0, Sunday is 6
    days_since_monday = day_start.weekday()
    return day_start - timedelta(days=days_since_monday)


def get_next_weekly_reset_utc(dt: datetime | None = None) -> datetime:
    """
    Return 00:00:00 UTC of the following Monday for weekly limit reset.

    Args:
        dt: Reference datetime (defaults to current UTC time).

    Returns:
        datetime: Next Monday 00:00:00 UTC.
    """
    return get_week_start_utc(dt) + timedelta(days=7)
