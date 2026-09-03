"""Immutable configuration and pure scoring helpers for role exams."""

from __future__ import annotations

import json
import random
import re
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol


__all__ = (
    "UNSAFE_ROLE_PERMISSION_NAMES",
    "RoleExamChoice",
    "RoleExamQuestion",
    "RoleExamConfig",
    "RoleExamConfigError",
    "load_role_exam_config",
    "shuffled_questions",
    "unsafe_role_permission_names",
    "required_correct_count",
    "score_answers",
    "is_passing_score",
)

SCHEMA_VERSION = 1
QUESTION_COUNT = 20
MIN_CHOICE_COUNT = 2
MAX_CHOICE_COUNT = 5

MAX_ID_LENGTH = 64
MAX_TITLE_LENGTH = 256
MAX_INSTRUCTIONS_LENGTH = 2_000
MAX_PROMPT_LENGTH = 1_000
MAX_CHOICE_TEXT_LENGTH = 500

UNSAFE_ROLE_PERMISSION_NAMES: tuple[str, ...] = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "kick_members",
    "ban_members",
    "moderate_members",
    "manage_messages",
    "pin_messages",
    "bypass_slowmode",
    "view_audit_log",
    "view_guild_insights",
    "view_creator_monetization_analytics",
    "mention_everyone",
    "manage_webhooks",
    "manage_events",
    "create_events",
    "manage_expressions",
    "create_expressions",
    "manage_threads",
    "manage_nicknames",
    "mute_members",
    "deafen_members",
    "move_members",
    "set_voice_channel_status",
)

_ROLE_ID_RE = re.compile(r"[0-9]+")


class RoleExamConfigError(ValueError):
    """Raised when a role-exam configuration file is missing or invalid."""


@dataclass(frozen=True, slots=True)
class RoleExamChoice:
    """One stable answer choice from a role-exam question."""

    id: str
    text: str


@dataclass(frozen=True, slots=True)
class RoleExamQuestion:
    """One role-exam question with immutable answer choices."""

    id: str
    prompt: str
    choices: tuple[RoleExamChoice, ...]
    correct_choice_id: str


@dataclass(frozen=True, slots=True)
class RoleExamConfig:
    """Validated role-exam configuration loaded from disk."""

    schema_version: int
    title: str
    instructions: str
    role_id: int | None
    required_percent: int
    questions: tuple[RoleExamQuestion, ...]


class _ShuffleRng(Protocol):
    def shuffle(self, values: MutableSequence[Any]) -> None:
        """Shuffle ``values`` in place."""


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RoleExamConfigError(f"{location} must be a JSON object.")
    return value


def _list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise RoleExamConfigError(f"{location} must be a JSON array.")
    return value


def _required(data: Mapping[str, Any], key: str, location: str) -> Any:
    if key not in data:
        raise RoleExamConfigError(f"{location}.{key} is required.")
    return data[key]


def _bounded_text(value: Any, location: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise RoleExamConfigError(f"{location} must be a string.")
    if not value.strip():
        raise RoleExamConfigError(f"{location} must not be blank.")
    if len(value) > maximum:
        raise RoleExamConfigError(
            f"{location} must be at most {maximum} characters long."
        )
    return value


def _exact_integer(value: Any, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoleExamConfigError(f"{location} must be an integer.")
    return value


def _parse_role_id(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or _ROLE_ID_RE.fullmatch(value) is None:
        raise RoleExamConfigError(
            "role_id must be null or a positive decimal string."
        )
    try:
        role_id = int(value)
    except ValueError as exc:
        raise RoleExamConfigError(
            "role_id must be null or a positive decimal string."
        ) from exc
    if role_id <= 0:
        raise RoleExamConfigError(
            "role_id must be null or a positive decimal string."
        )
    return role_id


def _parse_choice(raw_choice: Any, question_id: str, index: int) -> RoleExamChoice:
    location = f"question {question_id!r} choice {index + 1}"
    choice = _mapping(raw_choice, location)
    choice_id = _bounded_text(
        _required(choice, "id", location),
        f"{location}.id",
        MAX_ID_LENGTH,
    )
    text = _bounded_text(
        _required(choice, "text", location),
        f"{location}.text",
        MAX_CHOICE_TEXT_LENGTH,
    )
    return RoleExamChoice(id=choice_id, text=text)


def _parse_question(raw_question: Any, index: int) -> RoleExamQuestion:
    location = f"questions[{index}]"
    question = _mapping(raw_question, location)
    question_id = _bounded_text(
        _required(question, "id", location),
        f"{location}.id",
        MAX_ID_LENGTH,
    )
    prompt = _bounded_text(
        _required(question, "prompt", location),
        f"question {question_id!r}.prompt",
        MAX_PROMPT_LENGTH,
    )

    raw_choices = _list(
        _required(question, "choices", location),
        f"question {question_id!r}.choices",
    )
    if not MIN_CHOICE_COUNT <= len(raw_choices) <= MAX_CHOICE_COUNT:
        raise RoleExamConfigError(
            f"question {question_id!r} must contain between "
            f"{MIN_CHOICE_COUNT} and {MAX_CHOICE_COUNT} choices."
        )

    choices = tuple(
        _parse_choice(raw_choice, question_id, choice_index)
        for choice_index, raw_choice in enumerate(raw_choices)
    )
    choice_ids = [choice.id for choice in choices]
    if len(set(choice_ids)) != len(choice_ids):
        raise RoleExamConfigError(
            f"question {question_id!r} contains duplicate choice IDs."
        )

    correct_choice_id = _bounded_text(
        _required(question, "correct_choice_id", location),
        f"question {question_id!r}.correct_choice_id",
        MAX_ID_LENGTH,
    )
    if correct_choice_id not in set(choice_ids):
        raise RoleExamConfigError(
            f"question {question_id!r} correct_choice_id does not match a choice."
        )
    return RoleExamQuestion(
        id=question_id,
        prompt=prompt,
        choices=choices,
        correct_choice_id=correct_choice_id,
    )


def _parse_config(raw_config: Any) -> RoleExamConfig:
    config = _mapping(raw_config, "root")

    schema_version = _exact_integer(
        _required(config, "schema_version", "root"),
        "schema_version",
    )
    if schema_version != SCHEMA_VERSION:
        raise RoleExamConfigError(
            f"schema_version must be exactly {SCHEMA_VERSION}."
        )

    title = _bounded_text(
        _required(config, "title", "root"),
        "title",
        MAX_TITLE_LENGTH,
    )
    instructions = _bounded_text(
        _required(config, "instructions", "root"),
        "instructions",
        MAX_INSTRUCTIONS_LENGTH,
    )
    role_id = _parse_role_id(_required(config, "role_id", "root"))

    required_percent = _exact_integer(
        _required(config, "required_percent", "root"),
        "required_percent",
    )
    if not 1 <= required_percent <= 100:
        raise RoleExamConfigError(
            "required_percent must be between 1 and 100."
        )

    raw_questions = _list(
        _required(config, "questions", "root"),
        "questions",
    )
    if len(raw_questions) != QUESTION_COUNT:
        raise RoleExamConfigError(
            f"questions must contain exactly {QUESTION_COUNT} items."
        )
    questions = tuple(
        _parse_question(raw_question, index)
        for index, raw_question in enumerate(raw_questions)
    )
    question_ids = [question.id for question in questions]
    if len(set(question_ids)) != len(question_ids):
        raise RoleExamConfigError("questions contains duplicate question IDs.")

    return RoleExamConfig(
        schema_version=schema_version,
        title=title,
        instructions=instructions,
        role_id=role_id,
        required_percent=required_percent,
        questions=questions,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RoleExamConfigError(f"Duplicate JSON key {key!r} is not allowed.")
        result[key] = value
    return result


def load_role_exam_config(path: str | Path) -> RoleExamConfig:
    """Load and validate one UTF-8 role-exam JSON file."""

    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            raw_config = json.load(
                config_file,
                object_pairs_hook=_reject_duplicate_keys,
            )
    except RoleExamConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RoleExamConfigError(
            f"Could not load role-exam configuration {config_path}: {exc}"
        ) from exc
    return _parse_config(raw_config)


def shuffled_questions(
    config: RoleExamConfig,
    rng: _ShuffleRng | None = None,
) -> tuple[RoleExamQuestion, ...]:
    """Return a per-attempt question/choice shuffle without mutating config."""

    shuffle_rng: _ShuffleRng = rng if rng is not None else random.Random()
    questions = list(config.questions)
    shuffle_rng.shuffle(questions)

    shuffled: list[RoleExamQuestion] = []
    for question in questions:
        choices = list(question.choices)
        shuffle_rng.shuffle(choices)
        shuffled.append(replace(question, choices=tuple(choices)))
    return tuple(shuffled)


def unsafe_role_permission_names(permissions: object) -> tuple[str, ...]:
    """Return enabled privileged permission names in stable denylist order."""

    return tuple(
        permission_name
        for permission_name in UNSAFE_ROLE_PERMISSION_NAMES
        if bool(getattr(permissions, permission_name, False))
    )


def _validate_scoring_inputs(total: int, required_percent: int) -> None:
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise ValueError("total must be a positive integer.")
    if (
        isinstance(required_percent, bool)
        or not isinstance(required_percent, int)
        or not 1 <= required_percent <= 100
    ):
        raise ValueError("required_percent must be an integer from 1 to 100.")


def required_correct_count(total: int, required_percent: int) -> int:
    """Return the smallest whole-number score that reaches the percentage."""

    _validate_scoring_inputs(total, required_percent)
    return (total * required_percent + 99) // 100


def score_answers(
    questions: Sequence[RoleExamQuestion],
    answers: Mapping[str, str],
) -> int:
    """Count correct answers by stable question and choice IDs."""

    return sum(
        answers.get(question.id) == question.correct_choice_id
        for question in questions
    )


def is_passing_score(
    correct: int,
    total: int,
    required_percent: int,
) -> bool:
    """Return whether ``correct`` reaches the threshold using integer math."""

    _validate_scoring_inputs(total, required_percent)
    if (
        isinstance(correct, bool)
        or not isinstance(correct, int)
        or not 0 <= correct <= total
    ):
        raise ValueError("correct must be an integer from 0 through total.")
    return correct * 100 >= total * required_percent
