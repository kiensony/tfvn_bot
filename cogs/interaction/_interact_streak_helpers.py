"""Pure helpers for guild-scoped pair interaction streaks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any


VIETNAM_TIMEZONE = timezone(timedelta(hours=7), name="UTC+07:00")

SOURCE_MENTION = "mention"
SOURCE_REPLY = "reply"
SOURCE_VOICE = "voice"
VALID_SOURCES = frozenset({SOURCE_MENTION, SOURCE_REPLY, SOURCE_VOICE})

SOURCE_LABELS = {
    SOURCE_MENTION: "tin nhắn",
    SOURCE_REPLY: "trả lời",
    SOURCE_VOICE: "voice",
}

# Credit as soon as both humans are in the same voice or stage channel.
VOICE_OVERLAP_SECONDS = 0
STREAK_LIST_LIMIT = 10
CREDITED_STREAK_LIST_LIMIT = 25
STREAK_BOARD_WEEKS = 16
STREAK_BOARD_HISTORY_DAYS = 366
WEEKDAY_LABELS = ("CN", "T2", "T3", "T4", "T5", "T6", "T7")
STREAK_DAY_MARK = "█"
STREAK_EMPTY_MARK = "░"
MILESTONE_DAYS = frozenset({3, 7, 30, 100, 365})


@dataclass(frozen=True, slots=True)
class StreakCreditResult:
    document: dict[str, Any]
    advanced: bool
    announce: bool = False


def normalize_pair(user_id_a: int, user_id_b: int) -> tuple[int, int]:
    """Return sorted pair ids so (a,b) and (b,a) map to the same document."""
    if user_id_a == user_id_b:
        raise ValueError("pair members must be different users")
    if user_id_a < user_id_b:
        return user_id_a, user_id_b
    return user_id_b, user_id_a


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating naive values as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_mongo_utc(value: datetime) -> datetime:
    """Return naive UTC at MongoDB's BSON millisecond precision."""
    normalized = as_utc(value).replace(tzinfo=None)
    return normalized.replace(microsecond=(normalized.microsecond // 1000) * 1000)


def vietnam_date(now: datetime) -> date:
    """Calendar date in Vietnam time (UTC+7)."""
    return as_utc(now).astimezone(VIETNAM_TIMEZONE).date()


def iso_date(value: date) -> str:
    return value.isoformat()


def previous_iso_date(value: str) -> str:
    return (date.fromisoformat(value) - timedelta(days=1)).isoformat()


def live_dates(today: str) -> tuple[str, str]:
    return today, previous_iso_date(today)


def is_live_streak(last_active_date: str | None, today: str) -> bool:
    if not last_active_date:
        return False
    return last_active_date in live_dates(today)


def live_streak_query(guild_id: int, today: str) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "last_active_date": {"$in": list(live_dates(today))},
        "current_streak": {"$gt": 0},
    }


def credited_streak_query(guild_id: int, user_id: int) -> dict[str, Any]:
    """Pairs this member has already completed at least one day with."""
    return {
        "guild_id": guild_id,
        "partner_ids": user_id,
        "last_active_date": {"$exists": True, "$ne": None},
        "current_streak": {"$gt": 0},
    }


def is_credited_streak(document: Mapping[str, Any]) -> bool:
    """A completed day was stored. A one-sided reply is not a streak yet."""
    last_active = document.get("last_active_date")
    if not isinstance(last_active, str) or not last_active:
        return False
    current = document.get("current_streak")
    if isinstance(current, bool) or not isinstance(current, int):
        return False
    return current > 0


def credited_streak_sort_key(
    document: Mapping[str, Any], today: str
) -> tuple[int, int, int, str]:
    """Live chains first, then current count, record, and latest credit."""
    current = displayed_current_streak(document, today)
    longest = _stored_count(document.get("longest_streak"))
    last_active = document.get("last_active_date")
    last_text = last_active if isinstance(last_active, str) else ""
    return (1 if current > 0 else 0, current, longest, last_text)


def _valid_iso_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def chain_dates(last_active_date: object, current_streak: object) -> list[str]:
    """Inclusive Vietnam days of one stored chain, ending on its last credit."""
    end_text = _valid_iso_date(last_active_date)
    count = _stored_count(current_streak)
    if end_text is None or count <= 0:
        return []
    end = date.fromisoformat(end_text)
    start = end - timedelta(days=count - 1)
    return [
        (start + timedelta(days=offset)).isoformat()
        for offset in range(count)
    ]


def next_active_dates(
    document: Mapping[str, Any] | None,
    *,
    today: str,
) -> list[str]:
    """Credited days to store, including today and at most one year back.

    A row that has never stored `active_dates` is seeded from its current chain.
    """
    dates: set[str] = set()
    if document is not None and "active_dates" in document:
        raw = document.get("active_dates")
        if isinstance(raw, list):
            for item in raw:
                parsed = _valid_iso_date(item)
                if parsed is not None:
                    dates.add(parsed)
    elif document is not None:
        dates.update(
            chain_dates(
                document.get("last_active_date"),
                document.get("current_streak"),
            )
        )
    today_text = _valid_iso_date(today)
    if today_text is not None:
        dates.add(today_text)
        cutoff = (
            date.fromisoformat(today_text) - timedelta(days=STREAK_BOARD_HISTORY_DAYS)
        ).isoformat()
        latest = today_text
    else:
        cutoff = ""
        latest = "9999-12-31"
    return sorted(item for item in dates if cutoff <= item <= latest)


def board_active_dates(document: Mapping[str, Any] | None) -> list[str]:
    """Dates to draw. Stored history wins; otherwise draw the current chain."""
    if document is None:
        return []
    if "active_dates" in document:
        raw = document.get("active_dates")
        if not isinstance(raw, list):
            return []
        return sorted(
            {
                parsed
                for item in raw
                if (parsed := _valid_iso_date(item)) is not None
            }
        )
    return chain_dates(
        document.get("last_active_date"),
        document.get("current_streak"),
    )


def format_streak_board(
    active_dates: Iterable[str],
    *,
    today: date,
    weeks: int = STREAK_BOARD_WEEKS,
) -> str:
    """Weekday rows, one column per week, with one space between weeks.

    Sunday is the first row. Columns run from the oldest week to the current
    week. A filled block is a credited day. Days after `today` stay empty.
    """
    if weeks < 1:
        raise ValueError("streak board needs at least one week")
    active = {
        parsed
        for item in active_dates
        if (parsed := _valid_iso_date(item)) is not None
    }
    days_since_sunday = (today.weekday() + 1) % 7
    this_sunday = today - timedelta(days=days_since_sunday)
    start = this_sunday - timedelta(weeks=weeks - 1)
    rows: list[str] = []
    for row, label in enumerate(WEEKDAY_LABELS):
        cells: list[str] = []
        for week in range(weeks):
            day = start + timedelta(days=week * 7 + row)
            if day <= today and day.isoformat() in active:
                cells.append(STREAK_DAY_MARK)
            else:
                cells.append(STREAK_EMPTY_MARK)
        rows.append(f"{label} {' '.join(cells)}")
    return "\n".join(rows)


def displayed_current_streak(document: Mapping[str, Any], today: str) -> int:
    if not is_live_streak(document.get("last_active_date"), today):
        return 0
    return int(document.get("current_streak") or 0)


def other_user_id(document: Mapping[str, Any], user_id: int) -> int:
    user_a = int(document["user_a"])
    user_b = int(document["user_b"])
    if user_id == user_a:
        return user_b
    if user_id == user_b:
        return user_a
    raise ValueError("user is not in pair")


def source_label(source: str | None) -> str:
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    return "không rõ"


def is_streak_milestone(current_streak: int) -> bool:
    return current_streak in MILESTONE_DAYS


def _stored_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def recorded_milestones(document: Mapping[str, Any] | None) -> list[int]:
    """Milestones this pair was already noticed for.

    Documents written before `announced_milestones` existed count every
    milestone at or below the stored streak as already noticed.
    """
    if document is None:
        return []
    if "announced_milestones" in document:
        raw = document.get("announced_milestones")
        if not isinstance(raw, list):
            return []
        return sorted(
            {
                item
                for item in raw
                if isinstance(item, int)
                and not isinstance(item, bool)
                and item in MILESTONE_DAYS
            }
        )
    reached = max(
        _stored_count(document.get("longest_streak")),
        _stored_count(document.get("current_streak")),
    )
    return sorted(day for day in MILESTONE_DAYS if day <= reached)


def format_milestone_message(user_a: int, user_b: int, current_streak: int) -> str:
    return (
        f"🔥 <@{user_a}> và <@{user_b}> đã đạt chuỗi **{current_streak} ngày**!"
    )


def is_credit_eligible_voice_channel(
    channel_id: int | None,
    afk_channel_id: int | None,
) -> bool:
    if channel_id is None:
        return False
    if afk_channel_id is not None and channel_id == afk_channel_id:
        return False
    return True


def voice_overlap_partner_ids(
    member_id: int,
    occupants: Sequence[object],
) -> tuple[int, ...]:
    partners: list[int] = []
    seen: set[int] = set()
    for occupant in occupants:
        user_id = getattr(occupant, "id", None)
        if not isinstance(user_id, int) or user_id in seen:
            continue
        if user_id == member_id or bool(getattr(occupant, "bot", False)):
            continue
        seen.add(user_id)
        partners.append(user_id)
    return tuple(partners)


def reply_partner_id(author_id: int, reply_author: object | None) -> int | None:
    """Human the author replied to, when that reply can count for a pair."""
    if reply_author is None:
        return None
    user_id = getattr(reply_author, "id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        return None
    if user_id == author_id or bool(getattr(reply_author, "bot", False)):
        return None
    return user_id


def next_pending_replies(
    document: Mapping[str, Any] | None,
    *,
    today: str,
    replier_id: int,
    user_a: int,
    user_b: int,
) -> tuple[list[int], bool]:
    """Return today's replier ids and whether both pair members have replied.

    Replies from another Vietnam day are dropped. One side is not enough.
    """
    pair = {user_a, user_b}
    ids: set[int] = set()
    if document is not None and document.get("pending_reply_date") == today:
        raw = document.get("pending_reply_ids")
        if isinstance(raw, list):
            ids = {
                item
                for item in raw
                if isinstance(item, int) and not isinstance(item, bool) and item in pair
            }
    if replier_id in pair:
        ids.add(replier_id)
    return sorted(ids), pair <= ids


def apply_streak_credit(
    document: Mapping[str, Any] | None,
    *,
    guild_id: int,
    user_id_a: int,
    user_id_b: int,
    today: str,
    yesterday: str,
    source: str,
    now: datetime,
) -> StreakCreditResult:
    """Return the next stored pair document and whether the Vietnam day advanced."""
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown streak source: {source}")
    user_a, user_b = normalize_pair(user_id_a, user_id_b)
    now_utc = to_mongo_utc(now)

    if document is not None and document.get("last_active_date") == today:
        return StreakCreditResult(dict(document), advanced=False, announce=False)

    if document is not None and document.get("last_active_date") == yesterday:
        current = int(document.get("current_streak") or 0) + 1
    else:
        current = 1

    noticed = recorded_milestones(document)
    announce = current in MILESTONE_DAYS and current not in noticed
    if announce:
        noticed = sorted([*noticed, current])

    longest = current
    created_at = now_utc
    stored_id = None
    if document is not None:
        longest = max(int(document.get("longest_streak") or 0), current)
        created_at = document.get("created_at") or now_utc
        stored_id = document.get("_id")

    updated: dict[str, Any] = {
        "guild_id": guild_id,
        "user_a": user_a,
        "user_b": user_b,
        "partner_ids": [user_a, user_b],
        "current_streak": current,
        "longest_streak": longest,
        "last_active_date": today,
        "last_source": source,
        "announced_milestones": noticed,
        "active_dates": next_active_dates(document, today=today),
        "created_at": created_at,
        "updated_at": now_utc,
    }
    if stored_id is not None:
        updated["_id"] = stored_id
    return StreakCreditResult(updated, advanced=True, announce=announce)
