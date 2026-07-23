import re


ITEM_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
VALID_ITEM_TYPES = {"badge", "role"}


def normalize_item_id(value: str) -> str:
    """Normalize and validate a stable shop item identifier."""
    item_id = value.strip().lower()
    if not ITEM_ID_PATTERN.fullmatch(item_id):
        raise ValueError(
            "Item ID must contain 1-32 lowercase letters, numbers, underscores, or hyphens."
        )
    return item_id


def validate_price(value: int) -> int:
    """Return a valid positive shop price."""
    if value <= 0 or value > 1_000_000_000:
        raise ValueError("Price must be between 1 and 1,000,000,000.")
    return value


def clean_display_text(value: str, *, fallback: str, limit: int) -> str:
    """Collapse whitespace in user-facing catalog text and enforce a limit."""
    cleaned = " ".join(value.split()) or fallback
    return cleaned[:limit]


def format_price(value: int) -> str:
    return f"{value:,} TC"
