import logging
from typing import Any


logger = logging.getLogger(__name__)

VALID_CASE_STATUSES = {"open", "resolved", "appealed", "void"}
MAX_REASON_LENGTH = 1000
MAX_AUDIT_REASON_LENGTH = 512


def clean_case_reason(value: str | None) -> str:
    """Normalize a moderation reason for storage and embeds."""
    reason = " ".join((value or "").split())
    return (reason or "Không có lý do cụ thể")[:MAX_REASON_LENGTH]


def normalize_case_status(value: str) -> str:
    status = value.strip().lower()
    if status not in VALID_CASE_STATUSES:
        raise ValueError("Invalid moderation case status")
    return status


def format_audit_reason(reason: str | None, moderator: Any) -> str:
    """Fit a normalized reason and moderator into Discord's audit-log limit."""
    suffix = f" (Requested by {moderator})"
    available = max(0, MAX_AUDIT_REASON_LENGTH - len(suffix))
    return f"{clean_case_reason(reason)[:available]}{suffix}"[
        :MAX_AUDIT_REASON_LENGTH
    ]


def can_moderate(actor: Any, target: Any) -> bool:
    """Enforce caller hierarchy independently from the bot's own hierarchy."""
    if actor.id == target.id or target.id == target.guild.owner_id:
        return False
    if actor.id == actor.guild.owner_id:
        return True
    return actor.top_role > target.top_role


async def record_case(
    bot: Any,
    *,
    guild: Any,
    target: Any,
    moderator: Any,
    action: str,
    reason: str | None = None,
    duration_seconds: int | None = None,
) -> int | None:
    """Record a case when the cases cog is loaded without breaking moderation."""
    cog = bot.get_cog("ModerationCasesCog")
    if cog is None:
        logger.warning("ModerationCasesCog is not loaded; action=%s not recorded", action)
        return None

    try:
        return await cog.create_case(
            guild=guild,
            target=target,
            moderator=moderator,
            action=action,
            reason=clean_case_reason(reason),
            duration_seconds=duration_seconds,
        )
    except Exception:
        logger.exception("Failed to record moderation case for action=%s", action)
        return None


def case_suffix(case_number: int | None) -> str:
    return f" · Case #{case_number}" if case_number is not None else ""
