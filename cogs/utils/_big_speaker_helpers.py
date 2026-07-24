"""Pure helpers for the big_speaker utility (size → cost, clean, format)."""

from __future__ import annotations

import re
from dataclasses import dataclass


# text_size 1..6 → Trap Coin cost.
SIZE_TO_COST: dict[int, int] = {
    1: 1,
    2: 2,
    3: 5,
    4: 10,
    5: 20,
    6: 50,
}
ALLOWED_SIZES = frozenset(SIZE_TO_COST)
MAX_MESSAGE_LENGTH = 180

ROLE_MENTION_RE = re.compile(r"<@&\d+>")
EVERYONE_HERE_RE = re.compile(r"@(?:everyone|here)\b", re.IGNORECASE)

SEPARATOR_LARGE = "────────────────"
SEPARATOR_MEGA = "━━━━━━━━━━━━━━━━"
BIG_SIZE_SEPARATOR_MIN = 5


@dataclass(frozen=True)
class SpeakerTier:
    text_size: int
    cost: int


def validate_text_size(text_size: int) -> int:
    """Return text_size if it is 1..6; else raise ValueError."""
    if text_size not in ALLOWED_SIZES:
        allowed = ", ".join(str(size) for size in sorted(ALLOWED_SIZES))
        raise ValueError(
            f"Cỡ chữ không hợp lệ. Chỉ chấp nhận: {allowed}."
        )
    return text_size


def resolve_speaker_tier(text_size: int) -> SpeakerTier:
    """Validate text_size and return size + TC cost."""
    size = validate_text_size(text_size)
    return SpeakerTier(text_size=size, cost=SIZE_TO_COST[size])


def sanitize_mentions(text: str) -> str:
    """Remove role / everyone / here mentions; keep personal user mentions."""
    text = ROLE_MENTION_RE.sub("", text)
    text = EVERYONE_HERE_RE.sub("", text)
    return text


def clean_message(text: str, *, max_len: int = MAX_MESSAGE_LENGTH) -> str:
    """Collapse whitespace, strip abuse mentions, and enforce length."""
    collapsed = " ".join(text.split())
    sanitized = sanitize_mentions(collapsed)
    cleaned = " ".join(sanitized.split())
    if not cleaned:
        raise ValueError("Nội dung loa không được để trống.")
    if len(cleaned) > max_len:
        raise ValueError(
            f"Nội dung tối đa {max_len} ký tự (hiện {len(cleaned)})."
        )
    return cleaned


def format_big_speaker(text: str, text_size: int) -> str:
    """Apply Discord markdown for text_size 1..6 (sizes 5–6 get separators)."""
    size = validate_text_size(text_size)

    if size == 1:
        body = f"### {text}"
    elif size == 2:
        body = f"### **{text}**"
    elif size == 3:
        body = f"## {text}"
    elif size == 4:
        body = f"## **{text}**"
    elif size == 5:
        body = f"# {text}"
    else:
        body = f"# **{text}**"

    if size < BIG_SIZE_SEPARATOR_MIN:
        return body

    separator = SEPARATOR_MEGA if size >= 6 else SEPARATOR_LARGE
    return f"{separator}\n{body}\n{separator}"


def format_size_guide() -> str:
    """Short Vietnamese guide of sizes and TC costs."""
    parts = [
        f"cỡ {size} → {SIZE_TO_COST[size]} TC"
        for size in sorted(SIZE_TO_COST)
    ]
    return " · ".join(parts)
