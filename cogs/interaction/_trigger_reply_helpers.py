import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


MATCH_MODE_ALIASES = {
    "contains": "contains",
    "include": "contains",
    "exact": "exact",
}
MAX_TRIGGER_LENGTH = 200
MAX_REPLY_LENGTH = 2000


def normalize_message_text(value: str) -> str:
    """Normalize case and whitespace while retaining punctuation."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.casefold().split())


def normalize_match_mode(value: str) -> str:
    """Return the canonical match mode or raise for an unsupported mode."""
    try:
        return MATCH_MODE_ALIASES[value.strip().casefold()]
    except KeyError as exc:
        raise ValueError("Match mode must be contains or exact") from exc


def parse_rule_spec(mode: str, spec: str) -> dict[str, str]:
    """Parse `<trigger> | <reply>` command input into a validated rule."""
    match_mode = normalize_match_mode(mode)
    trigger, separator, reply = spec.partition("|")
    trigger = trigger.strip()
    reply = reply.strip()
    normalized_trigger = normalize_message_text(trigger)

    if not separator:
        raise ValueError("Rule must separate trigger and reply with |")
    if not normalized_trigger:
        raise ValueError("Trigger cannot be empty")
    if len(trigger) > MAX_TRIGGER_LENGTH:
        raise ValueError("Trigger is too long")
    if not reply:
        raise ValueError("Reply cannot be empty")
    if len(reply) > MAX_REPLY_LENGTH:
        raise ValueError("Reply is too long")

    return {
        "mode": match_mode,
        "trigger": trigger,
        "normalized_trigger": normalized_trigger,
        "reply": reply,
    }


def select_matching_rule(
    message_content: str,
    rules: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Select one rule, preferring exact then the most specific substring."""
    normalized_message = normalize_message_text(message_content)
    if not normalized_message:
        return None

    matches: list[Mapping[str, Any]] = []
    for rule in rules:
        mode = rule.get("mode")
        trigger = rule.get("normalized_trigger")
        if not isinstance(trigger, str) or not trigger:
            continue
        if mode == "exact" and normalized_message == trigger:
            matches.append(rule)
        elif mode == "contains" and trigger in normalized_message:
            matches.append(rule)

    if not matches:
        return None

    def priority(rule: Mapping[str, Any]) -> tuple[int, int, int]:
        mode_priority = 0 if rule.get("mode") == "exact" else 1
        trigger_length = -len(str(rule.get("normalized_trigger", "")))
        rule_id = rule.get("rule_id", 0)
        return mode_priority, trigger_length, int(rule_id)

    return min(matches, key=priority)
