"""Pure parsing and state transitions for Crocodile Dentist."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


DEFAULT_TOOTH_COUNT = 13
MIN_TOOTH_COUNT = 2
MAX_TOOTH_COUNT = 25
MIN_INVITEE_COUNT = 1
MAX_INVITEE_COUNT = 4

INVITATION_TIMEOUT = timedelta(minutes=5)
ACTIVE_TIMEOUT = timedelta(days=7)

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_FINISHED = "finished"
STATUS_CANCELLED = "cancelled"

RESPONSE_PENDING = "pending"
RESPONSE_ACCEPTED = "accepted"
RESPONSE_DECLINED = "declined"
RESPONSE_TIMED_OUT = "timed_out"

MENTION_RE = re.compile(r"<@!?(\d+)>")
INTEGER_RE = re.compile(r"[+-]?\d+")


class GameStateError(ValueError):
    """Raised when a requested state transition is not valid."""


@dataclass(frozen=True)
class ChallengeArguments:
    """Normalized command arguments for a Crocodile challenge."""

    teeth_count: int
    invitee_ids: tuple[int, ...]


def parse_challenge_arguments(
    arguments: str | Sequence[str],
) -> ChallengeArguments:
    """Parse ``[teeth] @member...`` without relying on Discord converters.

    The numeric tooth count is optional, but when supplied it must be the
    first token. Mentions are retained in their original order so duplicate
    targets can be rejected explicitly.
    """

    tokens = arguments.split() if isinstance(arguments, str) else list(arguments)
    if not tokens:
        raise ValueError("Bạn phải mời ít nhất một người chơi.")

    teeth_count = DEFAULT_TOOTH_COUNT
    if INTEGER_RE.fullmatch(tokens[0]):
        teeth_count = int(tokens.pop(0))

    if not MIN_TOOTH_COUNT <= teeth_count <= MAX_TOOTH_COUNT:
        raise ValueError(
            f"Số răng phải từ {MIN_TOOTH_COUNT} đến {MAX_TOOTH_COUNT}."
        )
    if not tokens:
        raise ValueError("Bạn phải mời ít nhất một người chơi.")
    if len(tokens) > MAX_INVITEE_COUNT:
        raise ValueError(f"Chỉ được mời tối đa {MAX_INVITEE_COUNT} người.")

    invitee_ids: list[int] = []
    for position, token in enumerate(tokens):
        match = MENTION_RE.fullmatch(token)
        if match is None:
            if position > 0 and INTEGER_RE.fullmatch(token):
                raise ValueError("Số răng (nếu có) phải đứng trước các @mention.")
            raise ValueError("Người chơi phải được nhập bằng @mention hợp lệ.")
        invitee_ids.append(int(match.group(1)))

    return ChallengeArguments(teeth_count, tuple(invitee_ids))


def _invitee_values(invitee: Any) -> tuple[int, bool]:
    if isinstance(invitee, Mapping):
        user_id = invitee.get("id")
        is_bot = bool(invitee.get("bot", False))
    elif isinstance(invitee, int):
        user_id = invitee
        is_bot = False
    else:
        user_id = getattr(invitee, "id", None)
        is_bot = bool(getattr(invitee, "bot", False))

    try:
        normalized_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Không tìm thấy một người chơi được mời.") from exc
    return normalized_id, is_bot


def validate_invitees(host_id: int, invitees: Sequence[Any]) -> tuple[int, ...]:
    """Validate invitee identity/bot rules and return ordered user IDs."""

    if not MIN_INVITEE_COUNT <= len(invitees) <= MAX_INVITEE_COUNT:
        raise ValueError(
            f"Bạn phải mời từ {MIN_INVITEE_COUNT} đến "
            f"{MAX_INVITEE_COUNT} người."
        )

    normalized: list[int] = []
    seen: set[int] = set()
    for invitee in invitees:
        user_id, is_bot = _invitee_values(invitee)
        if user_id == int(host_id):
            raise ValueError("Bạn không thể tự thách đấu chính mình.")
        if is_bot:
            raise ValueError("Không thể mời tài khoản bot tham gia.")
        if user_id in seen:
            raise ValueError("Mỗi người chỉ được mời một lần.")
        seen.add(user_id)
        normalized.append(user_id)
    return tuple(normalized)


def tooth_layout(teeth_count: int) -> list[list[int]]:
    """Return a Discord-compatible grid containing at most five rows."""

    if not MIN_TOOTH_COUNT <= int(teeth_count) <= MAX_TOOTH_COUNT:
        raise ValueError(
            f"Số răng phải từ {MIN_TOOTH_COUNT} đến {MAX_TOOTH_COUNT}."
        )
    teeth = list(range(1, int(teeth_count) + 1))
    return [teeth[start : start + 5] for start in range(0, len(teeth), 5)]


def _comparable_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _deadline_reached(deadline: Any, now: datetime) -> bool:
    return isinstance(deadline, datetime) and (
        _comparable_datetime(deadline) <= _comparable_datetime(now)
    )


def is_game_expired(game: Mapping[str, Any], now: datetime) -> bool:
    """Return whether a pending/active game has reached its deadline."""

    status = game.get("status")
    if status == STATUS_PENDING:
        return _deadline_reached(game.get("invitation_expires_at"), now)
    if status == STATUS_ACTIVE:
        return _deadline_reached(game.get("activity_expires_at"), now)
    return False


def _ordered_accepted_players(game: Mapping[str, Any]) -> list[int]:
    host_id = int(game["host_id"])
    original = [int(user_id) for user_id in game["original_player_ids"]]
    responses = game.get("responses") or {}
    accepted = [
        user_id
        for user_id in original
        if user_id != host_id
        and responses.get(str(user_id), responses.get(user_id))
        == RESPONSE_ACCEPTED
    ]
    return [host_id, *accepted]


def _increment_revision(game: dict[str, Any]) -> None:
    game["revision"] = int(game.get("revision", 0)) + 1


def _finish_pending_resolution(
    game: dict[str, Any],
    now: datetime,
) -> None:
    players = _ordered_accepted_players(game)
    game["player_ids"] = players
    game["participant_ids"] = players
    game["updated_at"] = now

    if len(players) < 2:
        game["status"] = STATUS_CANCELLED
        game["cancel_reason"] = "no_accepted_invitees"
        game["completed_at"] = now
        game["current_turn"] = None
        game["current_player_id"] = None
        game["activity_expires_at"] = None
        return

    game["status"] = STATUS_ACTIVE
    game["cancel_reason"] = None
    game["completed_at"] = None
    game["current_turn"] = 0
    game["current_player_id"] = players[0]
    game["started_at"] = now
    game["last_activity_at"] = now
    game["activity_expires_at"] = now + ACTIVE_TIMEOUT


def resolve_pending_game(
    game: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    """Resolve a pending game when all replies arrive or its deadline passes."""

    if game.get("status") != STATUS_PENDING:
        raise GameStateError("Ván này không còn chờ xác nhận.")

    updated = copy.deepcopy(dict(game))
    responses = dict(updated.get("responses") or {})
    invitee_ids = [
        int(user_id)
        for user_id in updated.get("original_player_ids", [])[1:]
    ]
    all_answered = all(
        responses.get(str(user_id), responses.get(user_id)) != RESPONSE_PENDING
        for user_id in invitee_ids
    )
    deadline_reached = is_game_expired(updated, now)
    if not all_answered and not deadline_reached:
        return None

    if deadline_reached:
        for user_id in invitee_ids:
            key = str(user_id)
            if responses.get(key, responses.get(user_id)) == RESPONSE_PENDING:
                responses[key] = RESPONSE_TIMED_OUT
                responses.pop(user_id, None)

    updated["responses"] = responses
    _finish_pending_resolution(updated, now)
    _increment_revision(updated)
    return updated


def apply_invitation_response(
    game: Mapping[str, Any],
    user_id: int,
    accepted: bool,
    now: datetime,
) -> dict[str, Any]:
    """Apply one final invitee response and resolve if it was the last one."""

    if game.get("status") != STATUS_PENDING:
        raise GameStateError("Ván này không còn chờ xác nhận.")
    if is_game_expired(game, now):
        raise GameStateError("Lời mời đã hết hạn.")

    normalized_id = int(user_id)
    original = [int(value) for value in game.get("original_player_ids", [])]
    if normalized_id == int(game.get("host_id", 0)) or normalized_id not in original:
        raise GameStateError("Bạn không phải người được mời trong ván này.")

    updated = copy.deepcopy(dict(game))
    responses = dict(updated.get("responses") or {})
    key = str(normalized_id)
    current = responses.get(key, responses.get(normalized_id))
    if current != RESPONSE_PENDING:
        raise GameStateError("Bạn đã trả lời lời mời này rồi.")

    responses[key] = RESPONSE_ACCEPTED if accepted else RESPONSE_DECLINED
    responses.pop(normalized_id, None)
    updated["responses"] = responses
    if not accepted:
        updated["participant_ids"] = [
            int(value)
            for value in updated.get("participant_ids", original)
            if int(value) != normalized_id
        ]
    if not accepted:
        updated["player_ids"] = [
            int(player_id)
            for player_id in updated.get("player_ids", [])
            if int(player_id) != normalized_id
        ]
    updated["updated_at"] = now

    invitee_ids = original[1:]
    all_answered = all(
        responses.get(str(invitee_id), responses.get(invitee_id))
        != RESPONSE_PENDING
        for invitee_id in invitee_ids
    )
    if all_answered:
        _finish_pending_resolution(updated, now)
    _increment_revision(updated)
    return updated


def resolve_active_expiry(
    game: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    """Cancel an inactive active game once its seven-day deadline is reached."""

    if game.get("status") != STATUS_ACTIVE:
        raise GameStateError("Ván này không ở trạng thái đang chơi.")
    if not is_game_expired(game, now):
        return None

    updated = copy.deepcopy(dict(game))
    updated["status"] = STATUS_CANCELLED
    updated["cancel_reason"] = "inactivity_timeout"
    updated["completed_at"] = now
    updated["updated_at"] = now
    updated["current_player_id"] = None
    updated["current_turn"] = None
    updated["activity_expires_at"] = None
    _increment_revision(updated)
    return updated


def resolve_expired_game(
    game: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    """Resolve the applicable deadline for pending or active state."""

    if game.get("status") == STATUS_PENDING:
        return resolve_pending_game(game, now)
    if game.get("status") == STATUS_ACTIVE:
        return resolve_active_expiry(game, now)
    return None


def press_tooth(
    game: Mapping[str, Any],
    user_id: int,
    tooth_number: int,
    now: datetime,
) -> dict[str, Any]:
    """Apply one valid tooth press and return the new persisted state."""

    if game.get("status") != STATUS_ACTIVE:
        raise GameStateError("Ván này không ở trạng thái đang chơi.")
    if is_game_expired(game, now):
        raise GameStateError("Ván này đã hết hạn vì không hoạt động.")

    normalized_user_id = int(user_id)
    if normalized_user_id != int(game.get("current_player_id", 0)):
        raise GameStateError("Chưa đến lượt của bạn.")

    normalized_tooth = int(tooth_number)
    tooth_count = int(game.get("tooth_count", 0))
    if not 1 <= normalized_tooth <= tooth_count:
        raise GameStateError("Chiếc răng này không thuộc ván chơi.")

    pressed = [int(value) for value in game.get("pressed_teeth", [])]
    if normalized_tooth in pressed:
        raise GameStateError("Chiếc răng này đã được nhấn rồi.")

    players = [int(value) for value in game.get("player_ids", [])]
    if len(players) < 2:
        raise GameStateError("Ván chơi không còn đủ người tham gia.")

    updated = copy.deepcopy(dict(game))
    updated["pressed_teeth"] = [*pressed, normalized_tooth]
    updated["last_activity_at"] = now
    updated["updated_at"] = now

    if normalized_tooth == int(game.get("dangerous_tooth", 0)):
        winner_ids = [
            player_id for player_id in players if player_id != normalized_user_id
        ]
        updated["status"] = STATUS_FINISHED
        updated["result"] = {
            "loser_id": normalized_user_id,
            "winner_ids": winner_ids,
            "dangerous_tooth": normalized_tooth,
            "pressed_at": now,
        }
        updated["completed_at"] = now
        updated["current_player_id"] = None
        updated["current_turn"] = None
        updated["activity_expires_at"] = None
    else:
        current_turn = int(game.get("current_turn", 0))
        next_turn = (current_turn + 1) % len(players)
        updated["current_turn"] = next_turn
        updated["current_player_id"] = players[next_turn]
        updated["activity_expires_at"] = now + ACTIVE_TIMEOUT

    _increment_revision(updated)
    return updated
