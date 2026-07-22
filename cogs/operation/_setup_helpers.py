from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SetupCheck:
    level: str
    name: str
    detail: str
    fix: str | None = None


def parse_discord_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def summarize_checks(checks: list[SetupCheck]) -> dict[str, int]:
    totals = {"ok": 0, "warning": 0, "error": 0}
    for check in checks:
        totals[check.level] = totals.get(check.level, 0) + 1
    return totals
