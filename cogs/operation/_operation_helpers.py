import csv
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from discord.ext import commands


MAX_ARGUMENT_LENGTH = 500
MAX_EXPORT_ROWS = 100_000

CommandStatus = Literal[
    "running",
    "succeeded",
    "denied",
    "invalid",
    "cooldown",
    "failed",
]

COMMAND_STATUSES: frozenset[CommandStatus] = frozenset(
    {
        "running",
        "succeeded",
        "denied",
        "invalid",
        "cooldown",
        "failed",
    }
)

CSV_COLUMNS: tuple[str, ...] = (
    "created_at",
    "completed_at",
    "event_type",
    "status",
    "command_or_action",
    "arguments",
    "actor_id",
    "actor_name",
    "guild_id",
    "channel_id",
    "message_id",
    "invoked_with",
    "error_type",
    "details",
)

_URL_PATTERN = re.compile(
    (
        r"(?<![\w@])(?:"
        r"(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>]+"
        r"|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z]{2,24}(?::\d{1,5})?(?:/[^\s<>]*)?"
        r")"
    ),
    re.IGNORECASE,
)
_FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass(frozen=True, slots=True)
class TimeRangeOption:
    """A selectable UTC time range for audit queries or pruning."""

    key: str
    label: str
    days: int | None

    def cutoff(self, now: datetime | None = None) -> datetime | None:
        """Return the lower/upper date boundary, or ``None`` for all records."""
        if self.days is None:
            return None
        reference = now if now is not None else datetime.now(timezone.utc)
        return reference - timedelta(days=self.days)


AUDIT_TIME_RANGES: tuple[TimeRangeOption, ...] = (
    TimeRangeOption("7d", "7 ngày", 7),
    TimeRangeOption("30d", "30 ngày", 30),
    TimeRangeOption("90d", "90 ngày", 90),
    TimeRangeOption("all", "Tất cả", None),
)

PRUNE_TIME_RANGES: tuple[TimeRangeOption, ...] = (
    TimeRangeOption("30d", "Cũ hơn 30 ngày", 30),
    TimeRangeOption("90d", "Cũ hơn 90 ngày", 90),
    TimeRangeOption("180d", "Cũ hơn 180 ngày", 180),
    TimeRangeOption("all", "Tất cả nhật ký", None),
)

AUDIT_RANGE_LABELS: Mapping[str, str] = {
    option.key: option.label for option in AUDIT_TIME_RANGES
}
PRUNE_RANGE_LABELS: Mapping[str, str] = {
    option.key: option.label for option in PRUNE_TIME_RANGES
}


class AuditExportError(ValueError):
    """Base error for an audit export that cannot be produced safely."""


class ExportRowLimitError(AuditExportError):
    """Raised when an export contains more than ``MAX_EXPORT_ROWS`` rows."""


class CsvPartTooLargeError(AuditExportError):
    """Raised when the header or one CSV row cannot fit in one output part."""


def get_audit_time_range(key: str) -> TimeRangeOption:
    """Resolve an audit/export range key."""
    return _get_time_range(key, AUDIT_TIME_RANGES)


def get_prune_time_range(key: str) -> TimeRangeOption:
    """Resolve a prune range key."""
    return _get_time_range(key, PRUNE_TIME_RANGES)


def get_audit_cutoff(
    key: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the inclusive ``created_at`` cutoff for an audit/export query."""
    return get_audit_time_range(key).cutoff(now)


def get_prune_cutoff(
    key: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the exclusive ``created_at`` cutoff for a prune query."""
    return get_prune_time_range(key).cutoff(now)


def sanitize_command_arguments(arguments: str | None) -> str:
    """Redact URLs, normalize whitespace, and bound stored command arguments."""
    if not arguments:
        return ""
    redacted = _URL_PATTERN.sub("[url]", arguments)
    normalized = " ".join(redacted.split())
    return normalized[:MAX_ARGUMENT_LENGTH]


def unwrap_command_error(error: BaseException) -> BaseException:
    """Return the original exception nested by command invocation wrappers."""
    current = error
    seen: set[int] = set()
    while isinstance(current, commands.CommandInvokeError):
        if id(current) in seen:
            break
        seen.add(id(current))
        original = current.original
        if not isinstance(original, BaseException):
            break
        current = original
    return current


def classify_command_error(error: BaseException) -> CommandStatus:
    """Map a Discord command error to a stable audit status."""
    unwrapped = unwrap_command_error(error)
    if isinstance(
        unwrapped,
        (commands.CommandOnCooldown, commands.MaxConcurrencyReached),
    ):
        return "cooldown"
    if isinstance(unwrapped, (commands.CheckFailure, commands.DisabledCommand)):
        return "denied"
    if isinstance(
        unwrapped,
        (
            commands.UserInputError,
            commands.ConversionError,
            commands.CommandNotFound,
        ),
    ):
        return "invalid"
    return "failed"


def command_error_type(error: BaseException) -> str:
    """Return the non-sensitive class name stored for a command error."""
    return type(unwrap_command_error(error)).__name__


def neutralize_csv_formula(value: Any) -> str:
    """Convert a value to text and prevent spreadsheet formula execution."""
    text = _stringify_csv_value(value)
    if not text:
        return text

    potential_formula = text.lstrip(" ")
    if potential_formula.startswith(("\t", "\r", "\n", *_FORMULA_PREFIXES)):
        return f"'{text}"
    return text


def audit_document_to_csv_row(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Flatten an operation audit document into the stable CSV column order."""
    command_or_action = document.get("command_name") or document.get("action")
    values = {
        **document,
        "command_or_action": command_or_action,
    }
    return tuple(
        neutralize_csv_formula(values.get(column)) for column in CSV_COLUMNS
    )


def serialize_audit_csv(documents: Iterable[Mapping[str, Any]]) -> bytes:
    """Serialize up to ``MAX_EXPORT_ROWS`` audit documents as UTF-8 BOM CSV."""
    bounded_documents = _collect_export_documents(documents)
    rows = (audit_document_to_csv_row(document) for document in bounded_documents)
    return _encode_csv(rows, include_header=True)


def split_audit_csv(
    documents: Iterable[Mapping[str, Any]],
    *,
    max_bytes: int,
) -> list[bytes]:
    """Split an audit export into deterministic, independently readable CSVs."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")

    bounded_documents = _collect_export_documents(documents)
    header = _encode_csv((), include_header=True)
    if len(header) > max_bytes:
        raise CsvPartTooLargeError(
            f"CSV header requires {len(header)} bytes, exceeding {max_bytes}"
        )

    parts: list[bytes] = []
    current = bytearray(header)
    for document in bounded_documents:
        row = _encode_csv((audit_document_to_csv_row(document),), include_header=False)
        if len(header) + len(row) > max_bytes:
            raise CsvPartTooLargeError(
                f"One CSV row requires {len(header) + len(row)} bytes, "
                f"exceeding {max_bytes}"
            )
        if len(current) + len(row) > max_bytes:
            parts.append(bytes(current))
            current = bytearray(header)
        current.extend(row)

    parts.append(bytes(current))
    return parts


def _get_time_range(
    key: str,
    options: Sequence[TimeRangeOption],
) -> TimeRangeOption:
    for option in options:
        if option.key == key:
            return option
    accepted = ", ".join(option.key for option in options)
    raise ValueError(f"Unknown time range {key!r}; expected one of: {accepted}")


def _collect_export_documents(
    documents: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    collected: list[Mapping[str, Any]] = []
    for document in documents:
        if len(collected) >= MAX_EXPORT_ROWS:
            raise ExportRowLimitError(
                f"Audit export exceeds the {MAX_EXPORT_ROWS:,}-row limit"
            )
        collected.append(document)
    return collected


def _encode_csv(
    rows: Iterable[Sequence[str]],
    *,
    include_header: bool,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    if include_header:
        writer.writerow(CSV_COLUMNS)
    writer.writerows(rows)
    encoding = "utf-8-sig" if include_header else "utf-8"
    return output.getvalue().encode(encoding)


def _stringify_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat().replace("+00:00", "Z")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        normalized = _normalize_json_value(value)
        return json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return str(value)


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized_items = (_normalize_json_value(item) for item in value)
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if isinstance(value, datetime):
        return _stringify_csv_value(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
