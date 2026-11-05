"""Append-only journal of staff verified-role approvals."""

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from cogs.mod._case_helpers import MAX_AUDIT_REASON_LENGTH


VERIFICATION_LOG_COLLECTION = "verification_logs"
VERIFICATION_INDEX_NAME = "guild_created_at_id"
VERIFICATION_ACTION_GRANTED = "granted"


def verification_event_id(guild_id: int, message_id: int) -> str:
    """Return the idempotent id for one verified command message."""
    return f"verified:{int(guild_id)}:{int(message_id)}"


def build_verified_audit_reason(
    command_name: str,
    moderator_name: str,
    moderator_id: int,
) -> str:
    """Name the approving moderator inside Discord's 512-character reason limit."""
    prefix = f"Support verified via {command_name} by "
    suffix = f" ({int(moderator_id)})"
    reason = f"{prefix}{moderator_name}{suffix}"
    if len(reason) <= MAX_AUDIT_REASON_LENGTH:
        return reason
    available = MAX_AUDIT_REASON_LENGTH - len(prefix) - len(suffix)
    if available < 1:
        return reason[:MAX_AUDIT_REASON_LENGTH]
    return f"{prefix}{moderator_name[:available]}{suffix}"


def build_verification_document(
    *,
    guild_id: int,
    message_id: int,
    channel_id: int,
    moderator_id: int,
    moderator_name: str,
    target_id: int,
    target_name: str,
    role_id: int,
    role_name: str,
    created_at: datetime,
) -> dict[str, Any]:
    """Build one granted-approval row. The command message id is the idempotency key."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return {
        "_id": verification_event_id(guild_id, message_id),
        "action": VERIFICATION_ACTION_GRANTED,
        "guild_id": int(guild_id),
        "message_id": int(message_id),
        "channel_id": int(channel_id),
        "moderator_id": int(moderator_id),
        "moderator_name": str(moderator_name),
        "target_id": int(target_id),
        "target_name": str(target_name),
        "role_id": int(role_id),
        "role_name": str(role_name),
        "created_at": created_at,
    }


def ensure_verification_indexes(collection: Any) -> None:
    """Index the journal for newest-first guild pages."""
    collection.create_index(
        [
            ("guild_id", ASCENDING),
            ("created_at", DESCENDING),
            ("_id", DESCENDING),
        ],
        name=VERIFICATION_INDEX_NAME,
    )


def record_verification(collection: Any, document: dict[str, Any]) -> None:
    """Insert the approval once. A lost acknowledgement retry does not create a second row."""
    payload = {key: value for key, value in document.items() if key != "_id"}
    try:
        collection.update_one(
            {"_id": document["_id"]},
            {"$setOnInsert": payload},
            upsert=True,
        )
    except DuplicateKeyError:
        return
