from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError


logger = logging.getLogger(__name__)

LIFECYCLE_COLLECTION = "bot_lifecycle_events"
LIFECYCLE_INDEX_NAME = "environment_occurred_at_id"
LIFECYCLE_EVENT_TYPES = frozenset(
    {"initial_ready", "reidentified", "resumed"}
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: Any) -> datetime | None:
    """Return a Mongo timestamp as an aware UTC datetime when possible."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class BotLifecycleRecorder:
    """Record process-scoped Discord gateway lifecycle events.

    Captures are synchronous so gateway callbacks never wait for MongoDB. A
    single background worker persists the head of the retry queue at a time,
    using a deterministic event ID and ``$setOnInsert`` so retrying a write
    whose acknowledgement was lost cannot create a duplicate.
    """

    def __init__(
        self,
        database: Any,
        environment: str,
        *,
        process_id: str | None = None,
        process_started_at: datetime | None = None,
        clock: Callable[[], datetime] = _utcnow,
        retry_delay: float = 60.0,
        cache_size: int = 100,
    ) -> None:
        self.process_id = process_id or str(uuid.uuid4())
        self.environment = environment.strip().lower() or "production"
        self._clock = clock
        self.process_started_at = _as_utc(
            process_started_at or clock()
        ) or _utcnow()
        self._collection = database[LIFECYCLE_COLLECTION]
        self._retry_delay = max(0.01, float(retry_delay))

        self._sequence = 0
        self._seen_ready = False
        self._pending: deque[dict[str, Any]] = deque()
        self._cache: deque[dict[str, Any]] = deque(
            maxlen=max(10, int(cache_size))
        )
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._started = False
        self._closing = False
        self._closed = False
        self._index_ready = False
        self._logged_failures: set[str] = set()

    async def start(self) -> None:
        """Start the persistence worker without delaying Discord startup."""
        if self._started:
            return
        if self._closed:
            raise RuntimeError("BotLifecycleRecorder cannot be restarted")

        self._started = True
        self._worker = asyncio.create_task(
            self._run_worker(),
            name="bot-lifecycle-recorder",
        )
        self._wake.set()

    async def close(self, drain_timeout: float = 5) -> None:
        """Best-effort drain pending events, then stop the worker safely."""
        if not self._started:
            self._closed = True
            return

        self._closing = True
        self._wake.set()
        worker = self._worker
        if worker is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=max(0.0, float(drain_timeout)),
                )
            except asyncio.TimeoutError:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                worker.cancel()
                raise
            except Exception:
                logger.exception("Lifecycle persistence worker stopped unexpectedly")

        self._worker = None
        self._started = False
        self._closed = True

    def capture_ready(self, guild_count: int) -> None:
        """Capture an initial Ready or a later gateway re-identification."""
        event_type = "reidentified" if self._seen_ready else "initial_ready"
        self._seen_ready = True
        self._capture(event_type, guild_count)

    def capture_resumed(self, guild_count: int) -> None:
        """Capture a successful Discord gateway session resume."""
        self._capture("resumed", guild_count)

    async def fetch_recent(
        self,
        limit: int = 10,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return current-environment events newest first and Mongo health.

        The process-local cache is always merged over MongoDB results. This
        keeps newly captured events visible while their writes are pending and
        also provides useful degraded history when MongoDB cannot be queried.
        """
        bounded_limit = max(1, min(100, int(limit)))
        cached = [dict(document) for document in self._cache]

        try:
            stored = await asyncio.to_thread(
                self._query_recent,
                bounded_limit,
            )
        except (PyMongoError, OSError) as exc:
            self._log_failure("query", exc)
            return self._newest(cached, bounded_limit), False
        except Exception as exc:
            # A driver/mock boundary should not break the operations panel.
            self._log_unexpected_failure("query", exc)
            return self._newest(cached, bounded_limit), False

        self._log_recovery("query")
        merged: dict[str, dict[str, Any]] = {}
        for document in stored:
            normalized = self._normalize_document(document)
            merged[str(normalized.get("_id", ""))] = normalized
        for document in cached:
            normalized = self._normalize_document(document)
            merged[str(normalized.get("_id", ""))] = normalized
        return self._newest(list(merged.values()), bounded_limit), True

    def _capture(self, event_type: str, guild_count: int) -> None:
        if self._closing or self._closed:
            logger.warning(
                "Ignored %s lifecycle event after recorder shutdown began",
                event_type,
            )
            return
        if event_type not in LIFECYCLE_EVENT_TYPES:
            raise ValueError(f"Unsupported lifecycle event: {event_type}")

        self._sequence += 1
        event_id = f"{self.process_id}:{self._sequence:020d}"
        occurred_at = _as_utc(self._clock()) or _utcnow()
        document: dict[str, Any] = {
            "_id": event_id,
            "event_id": event_id,
            "event_type": event_type,
            "process_id": self.process_id,
            "sequence": self._sequence,
            "process_started_at": self.process_started_at,
            "occurred_at": occurred_at,
            "environment": self.environment,
            "guild_count": max(0, int(guild_count)),
        }
        self._cache.append(document)
        self._pending.append(document)
        self._wake.set()

    async def _run_worker(self) -> None:
        while True:
            if self._closing and not self._pending:
                return

            if not self._index_ready:
                self._index_ready = await self._ensure_index()

            if self._pending:
                persisted = await self._persist(self._pending[0])
                if persisted:
                    self._pending.popleft()
                    continue
                await self._wait_for_retry()
                continue

            if not self._index_ready:
                await self._wait_for_retry()
                continue

            self._wake.clear()
            await self._wake.wait()

    async def _ensure_index(self) -> bool:
        try:
            await asyncio.to_thread(
                self._collection.create_index,
                [
                    ("environment", ASCENDING),
                    ("occurred_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name=LIFECYCLE_INDEX_NAME,
            )
        except (PyMongoError, OSError) as exc:
            self._log_failure("index", exc)
            return False
        except Exception as exc:
            self._log_unexpected_failure("index", exc)
            return False
        self._log_recovery("index")
        return True

    async def _persist(self, document: dict[str, Any]) -> bool:
        try:
            await asyncio.to_thread(
                self._collection.update_one,
                {"_id": document["_id"]},
                {"$setOnInsert": dict(document)},
                upsert=True,
            )
        except (PyMongoError, OSError) as exc:
            self._log_failure("write", exc)
            return False
        except Exception as exc:
            self._log_unexpected_failure("write", exc)
            return False
        self._log_recovery("write")
        return True

    async def _wait_for_retry(self) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(
                self._wake.wait(),
                timeout=self._retry_delay,
            )
        except asyncio.TimeoutError:
            pass

    def _query_recent(self, limit: int) -> list[dict[str, Any]]:
        cursor = self._collection.find({"environment": self.environment})
        cursor = cursor.sort(
            [("occurred_at", DESCENDING), ("_id", DESCENDING)]
        ).limit(limit)
        return [dict(document) for document in cursor]

    @staticmethod
    def _normalize_document(document: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(document)
        for key in ("process_started_at", "occurred_at"):
            value = _as_utc(normalized.get(key))
            if value is not None:
                normalized[key] = value
        return normalized

    @classmethod
    def _newest(
        cls,
        documents: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        floor = datetime.min.replace(tzinfo=UTC)
        return sorted(
            (cls._normalize_document(document) for document in documents),
            key=lambda document: (
                _as_utc(document.get("occurred_at")) or floor,
                str(document.get("_id", "")),
            ),
            reverse=True,
        )[:limit]

    def _log_failure(self, operation: str, exc: Exception) -> None:
        if operation in self._logged_failures:
            return
        self._logged_failures.add(operation)
        logger.warning(
            "Lifecycle MongoDB %s failed; retry/cache fallback is active: %s",
            operation,
            exc,
        )

    def _log_recovery(self, operation: str) -> None:
        if operation not in self._logged_failures:
            return
        self._logged_failures.remove(operation)
        logger.info("Lifecycle MongoDB %s recovered", operation)

    def _log_unexpected_failure(self, operation: str, exc: Exception) -> None:
        if operation in self._logged_failures:
            return
        self._logged_failures.add(operation)
        logger.error(
            "Unexpected lifecycle MongoDB %s failure; retry/cache fallback "
            "is active",
            operation,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
