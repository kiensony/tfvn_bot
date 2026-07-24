"""Pure helpers for the big_speaker utility (amount → size, clean, format)."""

from __future__ import annotations

import re
from dataclasses import dataclass


AMOUNT_TO_SIZE: dict[int, int] = {
    1: 1,
    2: 2,
    5: 3,
    10: 4,
    20: 5,
    50: 6,
}
ALLOWED_AMOUNTS = frozenset(AMOUNT_TO_SIZE)
MAX_MESSAGE_LENGTH = 180

ROLE_MENTION_RE = re.compile(r"<@&\d+>")
EVERYONE_HERE_RE = re.compile(r"@(?:everyone|here)\b", re.IGNORECASE)

# Price list for user-facing error / help text (stable order).
AMOUNT_SIZE_GUIDE = (
    (1, 1),
    (2, 2),
    (5, 3),
    (10, 4),
    (20, 5),
    (50, 6),
)


@dataclass(frozen=True)
class SpeakerTier:
    amount: int
    text_size: int


def validate_amount(amount: int) -> int:
    """Return amount if it is an allowed fixed price; else raise ValueError."""
    if amount not in ALLOWED_AMOUNTS:
        allowed = ", ".join(str(a) for a, _ in AMOUNT_SIZE_GUIDE)
        raise ValueError(
            f"Số TC không hợp lệ. Chỉ chấp nhận: {allowed}."
        )
    return amount


def amount_to_text_size(amount: int) -> int:
    """Map an allowed amount to text_size 1..6."""
    validated = validate_amount(amount)
    return AMOUNT_TO_SIZE[validated]


def resolve_speaker_tier(amount: int) -> SpeakerTier:
    """Validate amount and return amount + text_size."""
    validated = validate_amount(amount)
    return SpeakerTier(amount=validated, text_size=AMOUNT_TO_SIZE[validated])


def sanitize_mentions(text: str) -> str:
    """Remove role / everyone / here mentions; keep personal user mentions."""
    text = ROLE_MENTION_RE.sub("", text)
    text = EVERYONE_HERE_RE.sub("", text)
    return text


def clean_message(text: str, *, max_len: int = MAX_MESSAGE_LENGTH) -> str:
    """
    Collapse whitespace, strip abuse mentions, and enforce length.

    Raises ValueError if the result is empty.
    """
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
    """Apply Discord markdown heading/bold for text_size 1..6."""
    if text_size not in range(1, 7):
        raise ValueError("text_size must be between 1 and 6.")

    if text_size == 1:
        return f"### {text}"
    if text_size == 2:
        return f"### **{text}**"
    if text_size == 3:
        return f"## {text}"
    if text_size == 4:
        return f"## **{text}**"
    if text_size == 5:
        return f"# {text}"
    # text_size == 6: biggest + bold + speaker emoji
    return f"# **📢 {text}**"


def format_amount_guide() -> str:
    """Short Vietnamese guide of allowed amounts and sizes."""
    parts = [f"{amount} TC → cỡ {size}" for amount, size in AMOUNT_SIZE_GUIDE]
    return " · ".join(parts)
