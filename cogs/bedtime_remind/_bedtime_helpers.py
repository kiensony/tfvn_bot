"""Pure time helpers for recurring bedtime reminders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone


MINUTES_PER_DAY = 24 * 60
VIETNAM_TIMEZONE = timezone(timedelta(hours=7), name="UTC+07:00")

_CLOCK_TIME_PATTERN = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


@dataclass(frozen=True, slots=True)
class SleepWindow:
    """One recurring sleep window, represented by aware UTC datetimes."""

    bedtime_date: date
    starts_at: datetime
    ends_at: datetime


def _validate_minutes(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value < MINUTES_PER_DAY:
        raise ValueError(f"{name} must be between 0 and 1439")
    return value


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating naive Mongo values as UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_mongo_utc(value: datetime) -> datetime:
    """Return naive UTC at MongoDB's BSON millisecond precision."""

    normalized = as_utc(value).replace(tzinfo=None)
    return normalized.replace(microsecond=(normalized.microsecond // 1000) * 1000)


def parse_clock_time(value: str) -> int:
    """Parse ``H:MM`` or ``HH:MM`` into minutes after midnight."""

    if not isinstance(value, str):
        raise TypeError("time value must be a string")
    match = _CLOCK_TIME_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("time must use H:MM or HH:MM")

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise ValueError("time is outside the 24-hour clock")
    return hour * 60 + minute


def format_clock_time(value: int) -> str:
    """Format minutes after midnight as a normalized ``HH:MM`` value."""

    minutes = _validate_minutes(value, name="time")
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def sleep_window_for_date(
    bedtime_date: date,
    bedtime_minutes: int,
    wake_minutes: int,
) -> SleepWindow:
    """Build the sleep window belonging to one local bedtime date."""

    bedtime = _validate_minutes(bedtime_minutes, name="bedtime_minutes")
    wake = _validate_minutes(wake_minutes, name="wake_minutes")
    if bedtime == wake:
        raise ValueError("bedtime and wake time must differ")

    bed_hour, bed_minute = divmod(bedtime, 60)
    wake_hour, wake_minute = divmod(wake, 60)
    local_start = datetime.combine(
        bedtime_date,
        time(hour=bed_hour, minute=bed_minute, tzinfo=VIETNAM_TIMEZONE),
    )
    wake_date = bedtime_date + timedelta(days=wake <= bedtime)
    local_end = datetime.combine(
        wake_date,
        time(hour=wake_hour, minute=wake_minute, tzinfo=VIETNAM_TIMEZONE),
    )
    return SleepWindow(
        bedtime_date=bedtime_date,
        starts_at=local_start.astimezone(timezone.utc),
        ends_at=local_end.astimezone(timezone.utc),
    )


def active_sleep_window(
    now: datetime,
    bedtime_minutes: int,
    wake_minutes: int,
) -> SleepWindow | None:
    """Return the active window at ``now`` using inclusive/exclusive bounds."""

    bedtime = _validate_minutes(bedtime_minutes, name="bedtime_minutes")
    wake = _validate_minutes(wake_minutes, name="wake_minutes")
    if bedtime == wake:
        raise ValueError("bedtime and wake time must differ")

    current = as_utc(now)
    local_now = current.astimezone(VIETNAM_TIMEZONE)
    local_minutes = local_now.hour * 60 + local_now.minute
    candidate_date = local_now.date()
    if local_minutes < bedtime:
        candidate_date -= timedelta(days=1)

    window = sleep_window_for_date(candidate_date, bedtime, wake)
    if window.starts_at <= current < window.ends_at:
        return window
    return None


def bedtime_date_key(window: SleepWindow) -> str:
    """Return the stable local date key persisted for reminder deduplication."""

    return window.bedtime_date.isoformat()


def next_bedtime(now: datetime, bedtime_minutes: int) -> datetime:
    """Return the first bedtime occurrence strictly after ``now`` in UTC."""

    bedtime = _validate_minutes(bedtime_minutes, name="bedtime_minutes")
    current = as_utc(now)
    local_now = current.astimezone(VIETNAM_TIMEZONE)
    bed_hour, bed_minute = divmod(bedtime, 60)
    local_bedtime = datetime.combine(
        local_now.date(),
        time(hour=bed_hour, minute=bed_minute, tzinfo=VIETNAM_TIMEZONE),
    )
    if local_bedtime <= local_now:
        local_bedtime += timedelta(days=1)
    return local_bedtime.astimezone(timezone.utc)


def next_reminder_deadline(
    now: datetime,
    bedtime_minutes: int,
    wake_minutes: int,
) -> datetime:
    """Return the current window start when due, otherwise the next bedtime."""

    current = as_utc(now)
    window = active_sleep_window(current, bedtime_minutes, wake_minutes)
    if window is not None:
        return window.starts_at
    return next_bedtime(current, bedtime_minutes)
