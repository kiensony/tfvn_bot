import asyncio
import unittest
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import AutoReconnect

from cogs.operation._lifecycle import (
    LIFECYCLE_COLLECTION,
    LIFECYCLE_INDEX_NAME,
    BotLifecycleRecorder,
)


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = [dict(document) for document in documents]
        self.sort_spec: list[tuple[str, int]] | None = None
        self.limit_value: int | None = None

    def sort(self, spec: list[tuple[str, int]]) -> "FakeCursor":
        self.sort_spec = spec
        for key, direction in reversed(spec):
            self.documents.sort(
                key=lambda document: document.get(key),
                reverse=direction == DESCENDING,
            )
        return self

    def limit(self, value: int) -> "FakeCursor":
        self.limit_value = value
        self.documents = self.documents[:value]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeLifecycleCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.index_calls: list[tuple[list[tuple[str, int]], str]] = []
        self.update_attempt_ids: list[str] = []
        self.write_failures: deque[str] = deque()
        self.query_unavailable = False
        self.find_queries: list[dict[str, Any]] = []
        self.last_cursor: FakeCursor | None = None

    def create_index(
        self,
        spec: list[tuple[str, int]],
        *,
        name: str,
    ) -> str:
        self.index_calls.append((spec, name))
        return name

    def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool,
    ) -> None:
        event_id = query["_id"]
        self.update_attempt_ids.append(event_id)
        failure = self.write_failures.popleft() if self.write_failures else None
        if failure == "offline":
            raise AutoReconnect("temporarily offline")

        if event_id not in self.documents and upsert:
            self.documents[event_id] = dict(update["$setOnInsert"])

        if failure == "lost_ack":
            raise AutoReconnect("acknowledgement lost")

    def find(self, query: dict[str, Any]) -> FakeCursor:
        self.find_queries.append(dict(query))
        if self.query_unavailable:
            raise AutoReconnect("query unavailable")
        documents = [
            document
            for document in self.documents.values()
            if all(document.get(key) == value for key, value in query.items())
        ]
        self.last_cursor = FakeCursor(documents)
        return self.last_cursor


class FakeDatabase:
    def __init__(self, collection: FakeLifecycleCollection) -> None:
        self.collection = collection
        self.requested_names: list[str] = []

    def __getitem__(self, name: str) -> FakeLifecycleCollection:
        self.requested_names.append(name)
        return self.collection


async def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for lifecycle worker")
        await asyncio.sleep(0.005)


class TestBotLifecycleRecorder(unittest.IsolatedAsyncioTestCase):
    def make_recorder(
        self,
        collection: FakeLifecycleCollection,
        *,
        event_times: list[datetime] | None = None,
    ) -> BotLifecycleRecorder:
        times = iter(event_times or [NOW])
        return BotLifecycleRecorder(
            FakeDatabase(collection),
            "Development",
            process_id="11111111-2222-3333-4444-555555555555",
            process_started_at=NOW - timedelta(minutes=2),
            clock=lambda: next(times),
            retry_delay=0.01,
        )

    async def test_ready_resume_and_reidentify_have_stable_process_sequence(
        self,
    ) -> None:
        collection = FakeLifecycleCollection()
        recorder = self.make_recorder(
            collection,
            event_times=[
                NOW,
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
            ],
        )

        await recorder.start()
        recorder.capture_ready(4)
        recorder.capture_resumed(5)
        recorder.capture_ready(6)
        await wait_until(lambda: len(collection.documents) == 3)
        await recorder.close()

        documents = sorted(
            collection.documents.values(),
            key=lambda document: document["sequence"],
        )
        self.assertEqual(
            [document["event_type"] for document in documents],
            ["initial_ready", "resumed", "reidentified"],
        )
        self.assertEqual([document["sequence"] for document in documents], [1, 2, 3])
        self.assertEqual(
            {document["process_id"] for document in documents},
            {recorder.process_id},
        )
        self.assertEqual(len({document["event_id"] for document in documents}), 3)
        self.assertEqual([document["guild_count"] for document in documents], [4, 5, 6])
        self.assertEqual(recorder.environment, "development")
        self.assertEqual(recorder.process_started_at, NOW - timedelta(minutes=2))
        self.assertEqual(
            collection.index_calls,
            [
                (
                    [
                        ("environment", ASCENDING),
                        ("occurred_at", DESCENDING),
                        ("_id", DESCENDING),
                    ],
                    LIFECYCLE_INDEX_NAME,
                )
            ],
        )

    async def test_retry_is_ordered_and_lost_acknowledgement_is_idempotent(
        self,
    ) -> None:
        collection = FakeLifecycleCollection()
        collection.write_failures.extend(["offline", "lost_ack"])
        recorder = self.make_recorder(
            collection,
            event_times=[NOW, NOW + timedelta(seconds=1)],
        )

        await recorder.start()
        recorder.capture_ready(4)
        recorder.capture_resumed(4)
        await wait_until(
            lambda: len(collection.documents) == 2
            and len(collection.update_attempt_ids) >= 4
        )
        await recorder.close()

        first_id = f"{recorder.process_id}:{1:020d}"
        second_id = f"{recorder.process_id}:{2:020d}"
        self.assertEqual(
            collection.update_attempt_ids,
            [first_id, first_id, first_id, second_id],
        )
        self.assertEqual(set(collection.documents), {first_id, second_id})
        self.assertEqual(collection.documents[first_id]["event_type"], "initial_ready")
        self.assertEqual(collection.documents[second_id]["event_type"], "resumed")

    async def test_fetch_merges_cache_filters_environment_and_normalizes_utc(
        self,
    ) -> None:
        collection = FakeLifecycleCollection()
        recorder = self.make_recorder(
            collection,
            event_times=[NOW + timedelta(minutes=2)],
        )
        recorder.capture_ready(7)
        local_id = f"{recorder.process_id}:{1:020d}"
        collection.documents[local_id] = {
            "_id": local_id,
            "event_id": local_id,
            "event_type": "initial_ready",
            "process_id": recorder.process_id,
            "sequence": 1,
            "process_started_at": NOW.replace(tzinfo=None),
            "occurred_at": (NOW + timedelta(minutes=2)).replace(tzinfo=None),
            "environment": "development",
            "guild_count": 999,
        }
        collection.documents["other"] = {
            "_id": "other",
            "event_id": "other",
            "event_type": "resumed",
            "process_id": "another-process",
            "sequence": 2,
            "process_started_at": NOW.replace(tzinfo=None),
            "occurred_at": (NOW + timedelta(minutes=3)).replace(tzinfo=None),
            "environment": "development",
            "guild_count": 8,
        }
        collection.documents["production"] = {
            "_id": "production",
            "occurred_at": NOW + timedelta(days=1),
            "environment": "production",
        }

        documents, mongo_available = await recorder.fetch_recent(limit=10)

        self.assertTrue(mongo_available)
        self.assertEqual([document["_id"] for document in documents], ["other", local_id])
        self.assertEqual(documents[1]["guild_count"], 7)
        self.assertEqual(documents[0]["occurred_at"].tzinfo, UTC)
        self.assertEqual(documents[0]["process_started_at"].tzinfo, UTC)
        self.assertEqual(collection.find_queries, [{"environment": "development"}])
        self.assertEqual(
            collection.last_cursor.sort_spec,
            [("occurred_at", DESCENDING), ("_id", DESCENDING)],
        )
        self.assertEqual(collection.last_cursor.limit_value, 10)

    async def test_fetch_returns_cached_events_when_mongo_is_unavailable(
        self,
    ) -> None:
        collection = FakeLifecycleCollection()
        collection.query_unavailable = True
        recorder = self.make_recorder(
            collection,
            event_times=[NOW, NOW + timedelta(seconds=1)],
        )
        recorder.capture_ready(4)
        recorder.capture_resumed(5)

        documents, mongo_available = await recorder.fetch_recent(limit=10)

        self.assertFalse(mongo_available)
        self.assertEqual(
            [document["event_type"] for document in documents],
            ["resumed", "initial_ready"],
        )
        self.assertTrue(all(document["occurred_at"].tzinfo is UTC for document in documents))

    async def test_uses_the_global_collection_name(self) -> None:
        collection = FakeLifecycleCollection()
        database = FakeDatabase(collection)

        BotLifecycleRecorder(
            database,
            "development",
            process_id="process",
            process_started_at=NOW,
        )

        self.assertEqual(database.requested_names, [LIFECYCLE_COLLECTION])


if __name__ == "__main__":
    unittest.main()
