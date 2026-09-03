import asyncio
import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands
from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from cogs.bedtime_remind._bedtime_helpers import (
    VIETNAM_TIMEZONE,
    active_sleep_window,
    format_clock_time,
    next_bedtime,
    next_reminder_deadline,
    parse_clock_time,
    sleep_window_for_date,
    to_mongo_utc,
)
from cogs.bedtime_remind.bedtime_remind import (
    BEDTIME_REMINDERS_COLLECTION,
    BedtimeReminderCog,
)


GUILD_ID = 100
USER_ID = 42
CHANNEL_ID = 200
NOW = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = [dict(document) for document in documents or ()]
        self.index_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.find_calls: list[dict[str, Any]] = []
        self.update_calls: list[
            tuple[dict[str, Any], dict[str, Any], bool]
        ] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.write_error: Exception | None = None
        self.find_error: Exception | None = None
        self.index_error: Exception | None = None

    def create_index(self, *args: Any, **kwargs: Any) -> str:
        if self.index_error is not None:
            raise self.index_error
        self.index_calls.append((args, kwargs))
        return str(kwargs.get("name", "index"))

    def find(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        self.find_calls.append(dict(query))
        if self.find_error is not None:
            raise self.find_error
        return [
            dict(document)
            for document in self.documents
            if self._matches(document, query)
        ]

    def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> SimpleNamespace:
        self.update_calls.append((dict(query), dict(update), upsert))
        if self.write_error is not None:
            raise self.write_error

        selected = next(
            (
                document
                for document in self.documents
                if self._matches(document, query)
            ),
            None,
        )
        inserted = selected is None and upsert
        if inserted:
            selected = {
                key: value
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            selected.update(update.get("$setOnInsert", {}))
            self.documents.append(selected)
        if selected is not None:
            selected.update(update.get("$set", {}))
            for key in update.get("$unset", {}):
                selected.pop(key, None)
        return SimpleNamespace(
            matched_count=int(selected is not None and not inserted),
            modified_count=int(selected is not None),
            upserted_id=("inserted" if inserted else None),
        )

    def delete_one(self, query: dict[str, Any]) -> SimpleNamespace:
        self.delete_calls.append(dict(query))
        if self.write_error is not None:
            raise self.write_error
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    @staticmethod
    def _matches(
        document: dict[str, Any],
        query: dict[str, Any],
    ) -> bool:
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$nin" in expected and actual in expected["$nin"]:
                    return False
                if "$lte" in expected and (
                    actual is None or not actual <= expected["$lte"]
                ):
                    return False
                if "$lt" in expected and (
                    actual is None or not actual < expected["$lt"]
                ):
                    return False
                if "$gte" in expected and (
                    actual is None or not actual >= expected["$gte"]
                ):
                    return False
                continue
            if actual != expected:
                return False
        return True


class FakeDatabase:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection
        self.requested_names: list[str] = []

    def __getitem__(self, name: str) -> FakeCollection:
        self.requested_names.append(name)
        return self.collection


def make_member(
    user_id: int,
    *,
    guild: object | None = None,
    bot: bool = False,
    display_name: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        bot=bot,
        guild=guild,
        mention=f"<@{user_id}>",
        display_name=display_name or f"Member {user_id}",
    )


def make_http_error(status: int) -> Exception:
    response = SimpleNamespace(status=status, reason="test", headers={})
    if status == 403:
        return discord.Forbidden(response, "forbidden")
    if status == 404:
        return discord.NotFound(response, "not found")
    return discord.HTTPException(response, "transient")


def reminder_document(
    *,
    guild_id: int = GUILD_ID,
    user_id: int = USER_ID,
    channel_id: int = CHANNEL_ID,
    bedtime_minutes: int = 22 * 60,
    wake_minutes: int = 6 * 60,
    next_mention_at: datetime = datetime(2026, 8, 29, 15, 0),
    last_announced: str | None = None,
) -> dict[str, Any]:
    return {
        "_id": f"{guild_id}:{user_id}",
        "guild_id": guild_id,
        "user_id": user_id,
        "channel_id": channel_id,
        "bedtime_minutes": bedtime_minutes,
        "wake_minutes": wake_minutes,
        "next_mention_at": next_mention_at,
        "last_announced_bedtime_date": last_announced,
        "created_by": 7,
        "created_at": datetime(2026, 8, 1, 0, 0),
        "updated_by": 7,
        "updated_at": datetime(2026, 8, 1, 0, 0),
    }


class BedtimeFixture:
    def __init__(
        self,
        documents: list[dict[str, Any]] | None = None,
    ) -> None:
        self.collection = FakeCollection(documents)
        self.database = FakeDatabase(self.collection)
        self.guild = Mock(spec=discord.Guild)
        self.guild.id = GUILD_ID
        self.bot_member = make_member(999, guild=self.guild, bot=True)
        self.member = make_member(USER_ID, guild=self.guild)
        self.guild.me = self.bot_member

        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = CHANNEL_ID
        self.channel.name = "bed-time"
        self.channel.guild = self.guild
        self.channel.send = AsyncMock()
        self.channel.permissions_for = Mock(
            return_value=SimpleNamespace(
                view_channel=True,
                send_messages=True,
            )
        )

        self.members: dict[int, object] = {
            USER_ID: self.member,
            self.bot_member.id: self.bot_member,
        }
        self.channels: dict[int, object] = {CHANNEL_ID: self.channel}
        self.guild.get_member = Mock(side_effect=self.members.get)
        self.guild.get_channel = Mock(side_effect=self.channels.get)

        self.bot = SimpleNamespace(
            db=self.database,
            user=SimpleNamespace(id=self.bot_member.id),
            get_guild=Mock(
                side_effect=lambda guild_id: (
                    self.guild if guild_id == GUILD_ID else None
                )
            ),
            wait_until_ready=AsyncMock(),
        )
        with patch("discord.ext.tasks.Loop.start") as self.loop_start:
            self.cog = BedtimeReminderCog(self.bot)

    def make_context(self) -> SimpleNamespace:
        return SimpleNamespace(
            guild=self.guild,
            author=make_member(7, guild=self.guild),
            clean_prefix="!tf ",
            prefix="!tf ",
            send=AsyncMock(),
        )

    def make_message(
        self,
        *,
        guild: object | None = None,
        author: object | None = None,
        webhook_id: int | None = None,
        content: str = "hello",
        attachments: list[object] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            guild=self.guild if guild is None else guild,
            author=self.member if author is None else author,
            webhook_id=webhook_id,
            content=content,
            attachments=list(attachments or ()),
            reply=AsyncMock(),
        )


class TestBedtimeTimeHelpers(unittest.TestCase):
    def test_parse_and_format_clock_time(self) -> None:
        self.assertEqual(parse_clock_time("0:00"), 0)
        self.assertEqual(parse_clock_time("7:05"), 7 * 60 + 5)
        self.assertEqual(parse_clock_time("23:59"), 23 * 60 + 59)
        self.assertEqual(format_clock_time(7 * 60 + 5), "07:05")

    def test_parse_clock_time_rejects_invalid_values(self) -> None:
        for value in (
            "",
            "7",
            "07:5",
            "7:5",
            "007:05",
            "24:00",
            "23:60",
            "-1:00",
            "noon",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_clock_time(value)

    def test_cross_midnight_window_and_exact_boundaries(self) -> None:
        window = sleep_window_for_date(
            date(2026, 8, 29),
            22 * 60,
            6 * 60,
        )

        self.assertEqual(window.bedtime_date, date(2026, 8, 29))
        self.assertEqual(
            window.starts_at,
            datetime(2026, 8, 29, 15, 0, tzinfo=UTC),
        )
        self.assertEqual(
            window.ends_at,
            datetime(2026, 8, 29, 23, 0, tzinfo=UTC),
        )
        self.assertEqual(
            active_sleep_window(window.starts_at, 22 * 60, 6 * 60),
            window,
        )
        self.assertEqual(
            active_sleep_window(
                datetime(2026, 8, 30, 5, 59, 59, tzinfo=VIETNAM_TIMEZONE),
                22 * 60,
                6 * 60,
            ),
            window,
        )
        self.assertIsNone(
            active_sleep_window(window.ends_at, 22 * 60, 6 * 60)
        )

    def test_same_day_window_and_boundaries(self) -> None:
        window = sleep_window_for_date(
            date(2026, 8, 29),
            1 * 60,
            6 * 60,
        )

        self.assertEqual(
            active_sleep_window(window.starts_at, 1 * 60, 6 * 60),
            window,
        )
        self.assertIsNone(
            active_sleep_window(
                datetime(2026, 8, 29, 0, 59, 59, tzinfo=VIETNAM_TIMEZONE),
                1 * 60,
                6 * 60,
            )
        )
        self.assertIsNone(active_sleep_window(window.ends_at, 1 * 60, 6 * 60))

    def test_equal_bedtime_and_wake_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sleep_window_for_date(date(2026, 8, 29), 6 * 60, 6 * 60)
        with self.assertRaises(ValueError):
            active_sleep_window(
                datetime(2026, 8, 29, 6, 0, tzinfo=VIETNAM_TIMEZONE),
                6 * 60,
                6 * 60,
            )

    def test_next_bedtime_is_strictly_future(self) -> None:
        before = datetime(2026, 8, 29, 21, 59, tzinfo=VIETNAM_TIMEZONE)
        exact = datetime(2026, 8, 29, 22, 0, tzinfo=VIETNAM_TIMEZONE)

        self.assertEqual(
            next_bedtime(before, 22 * 60),
            datetime(2026, 8, 29, 22, 0, tzinfo=VIETNAM_TIMEZONE),
        )
        self.assertEqual(
            next_bedtime(exact, 22 * 60),
            datetime(2026, 8, 30, 22, 0, tzinfo=VIETNAM_TIMEZONE),
        )

    def test_deadline_catches_up_only_during_active_window(self) -> None:
        active = datetime(2026, 8, 30, 1, 0, tzinfo=VIETNAM_TIMEZONE)
        after_wake = datetime(2026, 8, 30, 7, 0, tzinfo=VIETNAM_TIMEZONE)

        self.assertEqual(
            next_reminder_deadline(active, 22 * 60, 6 * 60),
            datetime(2026, 8, 29, 22, 0, tzinfo=VIETNAM_TIMEZONE),
        )
        self.assertEqual(
            next_reminder_deadline(after_wake, 22 * 60, 6 * 60),
            datetime(2026, 8, 30, 22, 0, tzinfo=VIETNAM_TIMEZONE),
        )

    def test_to_mongo_utc_normalizes_aware_values_to_naive_utc(self) -> None:
        local = datetime(
            2026,
            8,
            29,
            22,
            0,
            0,
            123_456,
            tzinfo=VIETNAM_TIMEZONE,
        )

        normalized = to_mongo_utc(local)

        self.assertEqual(
            normalized,
            datetime(2026, 8, 29, 15, 0, 0, 123_000),
        )
        self.assertIsNone(normalized.tzinfo)
        self.assertEqual(
            to_mongo_utc(
                datetime(2026, 8, 29, 15, 0, 0, 123_999, tzinfo=UTC)
            ),
            normalized,
        )


class TestBedtimePersistenceSetup(unittest.TestCase):
    def test_constructor_uses_collection_creates_indexes_and_loads_valid_rows(
        self,
    ) -> None:
        valid = reminder_document()
        malformed = reminder_document(user_id=43)
        malformed["wake_minutes"] = malformed["bedtime_minutes"]
        fixture = BedtimeFixture([valid, malformed])

        self.assertEqual(
            fixture.database.requested_names,
            [BEDTIME_REMINDERS_COLLECTION],
        )
        self.assertEqual(
            set(fixture.cog.reminders_by_member),
            {(GUILD_ID, USER_ID)},
        )
        self.assertEqual(fixture.collection.find_calls, [{}])
        fixture.loop_start.assert_called_once()

        indexes = {
            kwargs["name"]: (args, kwargs)
            for args, kwargs in fixture.collection.index_calls
        }
        self.assertEqual(
            indexes["guild_user_bedtime_unique"][0][0],
            [("guild_id", ASCENDING), ("user_id", ASCENDING)],
        )
        self.assertTrue(indexes["guild_user_bedtime_unique"][1]["unique"])
        self.assertEqual(
            indexes["bedtime_next_mention_due"][0][0],
            [("next_mention_at", ASCENDING)],
        )

    def test_constructor_fails_closed_when_indexes_are_unavailable(self) -> None:
        collection = FakeCollection()
        collection.index_error = PyMongoError("offline")
        bot = SimpleNamespace(db=FakeDatabase(collection))

        with patch("discord.ext.tasks.Loop.start") as loop_start:
            with self.assertRaisesRegex(RuntimeError, "enforce.*indexes"):
                BedtimeReminderCog(bot)

        loop_start.assert_not_called()

    def test_constructor_fails_closed_when_initial_cache_load_fails(self) -> None:
        collection = FakeCollection()
        collection.find_error = PyMongoError("offline")
        bot = SimpleNamespace(db=FakeDatabase(collection))

        with patch("discord.ext.tasks.Loop.start") as loop_start:
            with self.assertRaisesRegex(RuntimeError, "load.*cache"):
                BedtimeReminderCog(bot)

        loop_start.assert_not_called()


class TestBedtimeCommands(unittest.IsolatedAsyncioTestCase):
    async def test_add_then_update_upserts_and_refreshes_cache(self) -> None:
        fixture = BedtimeFixture()
        ctx = fixture.make_context()

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            await fixture.cog.bedtime_add.callback(
                fixture.cog,
                ctx,
                fixture.member,
                "22:00",
                "6:00",
                fixture.channel,
            )

        self.assertEqual(len(fixture.collection.documents), 1)
        stored = fixture.collection.documents[0]
        self.assertEqual(stored["guild_id"], GUILD_ID)
        self.assertEqual(stored["user_id"], USER_ID)
        self.assertEqual(stored["channel_id"], CHANNEL_ID)
        self.assertEqual(stored["bedtime_minutes"], 22 * 60)
        self.assertEqual(stored["wake_minutes"], 6 * 60)
        self.assertEqual(stored["next_mention_at"], datetime(2026, 8, 29, 15, 0))
        self.assertEqual(stored["created_by"], ctx.author.id)
        self.assertEqual(stored["updated_by"], ctx.author.id)
        self.assertEqual(
            fixture.cog.reminders_by_member[(GUILD_ID, USER_ID)][
                "next_mention_at"
            ],
            datetime(2026, 8, 29, 15, 0),
        )
        self.assertTrue(fixture.collection.update_calls[0][2])
        first_response = ctx.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(first_response.users)

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            await fixture.cog.bedtime_add.callback(
                fixture.cog,
                ctx,
                fixture.member,
                "23:30",
                "07:00",
                fixture.channel,
            )

        self.assertEqual(len(fixture.collection.documents), 1)
        self.assertEqual(fixture.collection.documents[0]["bedtime_minutes"], 1410)
        self.assertEqual(fixture.collection.documents[0]["wake_minutes"], 420)
        self.assertEqual(
            fixture.cog.reminders_by_member[(GUILD_ID, USER_ID)][
                "next_mention_at"
            ],
            datetime(2026, 8, 29, 16, 30),
        )
        self.assertIn("cập nhật", ctx.send.await_args.args[0])

    async def test_add_normalizes_revision_to_bson_precision(self) -> None:
        fixture = BedtimeFixture()
        ctx = fixture.make_context()
        sub_millisecond_now = NOW.replace(microsecond=123_456)

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=sub_millisecond_now,
        ):
            await fixture.cog.bedtime_add.callback(
                fixture.cog,
                ctx,
                fixture.member,
                "22:00",
                "06:00",
                fixture.channel,
            )

        cached = fixture.cog.reminders_by_member[(GUILD_ID, USER_ID)]
        self.assertEqual(cached["updated_at"].microsecond, 123_000)
        self.assertEqual(
            await fixture.cog.process_due_reminders(sub_millisecond_now),
            1,
        )
        fixture.channel.send.assert_awaited_once()

    async def test_add_rejects_invalid_targets_channel_permissions_and_times(
        self,
    ) -> None:
        fixture = BedtimeFixture()
        ctx = fixture.make_context()
        another_guild = SimpleNamespace(id=999)
        cases = (
            (
                make_member(50, guild=fixture.guild, bot=True),
                "22:00",
                "06:00",
                fixture.channel,
            ),
            (
                make_member(50, guild=another_guild),
                "22:00",
                "06:00",
                fixture.channel,
            ),
            (
                fixture.member,
                "22:00",
                "06:00",
                SimpleNamespace(id=201, guild=another_guild),
            ),
            (fixture.member, "24:00", "06:00", fixture.channel),
            (fixture.member, "22:00", "22:00", fixture.channel),
        )

        for member, bedtime, wake, channel in cases:
            with self.subTest(bedtime=bedtime, member=member.id):
                await fixture.cog.bedtime_add.callback(
                    fixture.cog,
                    ctx,
                    member,
                    bedtime,
                    wake,
                    channel,
                )

        fixture.channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True,
            send_messages=False,
        )
        await fixture.cog.bedtime_add.callback(
            fixture.cog,
            ctx,
            fixture.member,
            "22:00",
            "06:00",
            fixture.channel,
        )

        self.assertEqual(fixture.collection.update_calls, [])
        self.assertNotIn((GUILD_ID, USER_ID), fixture.cog.reminders_by_member)
        self.assertEqual(ctx.send.await_count, len(cases) + 1)

    async def test_add_mongo_failure_does_not_mutate_existing_cache(self) -> None:
        original = reminder_document()
        fixture = BedtimeFixture([original])
        ctx = fixture.make_context()
        before = dict(fixture.cog.reminders_by_member[(GUILD_ID, USER_ID)])
        fixture.collection.write_error = PyMongoError("offline")

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            await fixture.cog.bedtime_add.callback(
                fixture.cog,
                ctx,
                fixture.member,
                "23:30",
                "07:00",
                fixture.channel,
            )

        self.assertEqual(
            fixture.cog.reminders_by_member[(GUILD_ID, USER_ID)],
            before,
        )
        self.assertEqual(fixture.collection.documents, [original])
        self.assertIn("database", ctx.send.await_args.args[0])

    async def test_schedule_change_clears_same_date_delivery_dedupe(self) -> None:
        original = reminder_document(
            bedtime_minutes=1 * 60,
            wake_minutes=6 * 60,
            last_announced="2026-08-29",
        )
        fixture = BedtimeFixture([original])
        ctx = fixture.make_context()

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            await fixture.cog.bedtime_add.callback(
                fixture.cog,
                ctx,
                fixture.member,
                "22:00",
                "06:00",
                fixture.channel,
            )

        stored = fixture.collection.documents[0]
        self.assertIsNone(stored["last_announced_bedtime_date"])
        self.assertEqual(stored["next_mention_at"], datetime(2026, 8, 29, 15, 0))

    async def test_identical_schedule_keeps_same_window_deduplicated(self) -> None:
        fixture = BedtimeFixture(
            [reminder_document(last_announced="2026-08-29")]
        )
        ctx = fixture.make_context()

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            await fixture.cog.bedtime_add.callback(
                fixture.cog,
                ctx,
                fixture.member,
                "22:00",
                "06:00",
                fixture.channel,
            )

        stored = fixture.collection.documents[0]
        self.assertEqual(stored["last_announced_bedtime_date"], "2026-08-29")
        self.assertEqual(stored["next_mention_at"], datetime(2026, 8, 30, 15, 0))

    async def test_channel_or_wake_change_does_not_repeat_same_bedtime(self) -> None:
        for changed_field in ("channel", "wake"):
            with self.subTest(changed_field=changed_field):
                fixture = BedtimeFixture(
                    [reminder_document(last_announced="2026-08-29")]
                )
                ctx = fixture.make_context()
                channel = fixture.channel
                wake = "06:00"
                if changed_field == "channel":
                    channel = Mock(spec=discord.TextChannel)
                    channel.id = CHANNEL_ID + 1
                    channel.name = "new-bed-time"
                    channel.guild = fixture.guild
                    channel.permissions_for = Mock(
                        return_value=SimpleNamespace(
                            view_channel=True,
                            send_messages=True,
                        )
                    )
                else:
                    wake = "07:00"

                with patch(
                    "cogs.bedtime_remind.bedtime_remind._utcnow",
                    return_value=NOW,
                ):
                    await fixture.cog.bedtime_add.callback(
                        fixture.cog,
                        ctx,
                        fixture.member,
                        "22:00",
                        wake,
                        channel,
                    )

                stored = fixture.collection.documents[0]
                self.assertEqual(
                    stored["last_announced_bedtime_date"],
                    "2026-08-29",
                )
                self.assertEqual(
                    stored["next_mention_at"],
                    datetime(2026, 8, 30, 15, 0),
                )

    async def test_remove_mutates_cache_only_after_database_success(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        ctx = fixture.make_context()
        fixture.collection.write_error = PyMongoError("offline")

        await fixture.cog.bedtime_remove.callback(
            fixture.cog,
            ctx,
            fixture.member,
        )

        self.assertIn((GUILD_ID, USER_ID), fixture.cog.reminders_by_member)

        fixture.collection.write_error = None
        await fixture.cog.bedtime_remove.callback(
            fixture.cog,
            ctx,
            fixture.member,
        )

        self.assertNotIn((GUILD_ID, USER_ID), fixture.cog.reminders_by_member)
        self.assertEqual(fixture.collection.documents, [])
        self.assertEqual(
            fixture.collection.delete_calls[-1],
            {"guild_id": GUILD_ID, "user_id": USER_ID},
        )

    async def test_remove_reports_member_without_a_schedule(self) -> None:
        fixture = BedtimeFixture()
        ctx = fixture.make_context()

        await fixture.cog.bedtime_remove.callback(
            fixture.cog,
            ctx,
            fixture.member,
        )

        self.assertIn("chưa có", ctx.send.await_args.args[0])

    async def test_remove_accepts_a_stored_user_id_after_member_leaves(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        ctx = fixture.make_context()
        fixture.members.pop(USER_ID)

        await fixture.cog.bedtime_remove.callback(
            fixture.cog,
            ctx,
            USER_ID,
        )

        self.assertEqual(fixture.collection.documents, [])
        self.assertNotIn((GUILD_ID, USER_ID), fixture.cog.reminders_by_member)
        self.assertIn(str(USER_ID), ctx.send.await_args.args[0])

    async def test_remove_rejects_an_integer_too_large_for_mongodb(self) -> None:
        fixture = BedtimeFixture()
        ctx = fixture.make_context()

        await fixture.cog.bedtime_remove.callback(
            fixture.cog,
            ctx,
            10**100,
        )

        self.assertEqual(fixture.collection.delete_calls, [])
        self.assertIn("Discord ID", ctx.send.await_args.args[0])

    async def test_list_is_paginated_guild_scoped_and_never_pings(self) -> None:
        documents = [
            reminder_document(user_id=user_id, channel_id=CHANNEL_ID)
            for user_id in range(1, 13)
        ]
        documents.append(reminder_document(guild_id=999, user_id=999))
        fixture = BedtimeFixture(documents)
        ctx = fixture.make_context()
        fixture.guild.get_member.side_effect = lambda user_id: make_member(
            user_id,
            guild=fixture.guild,
        )

        await fixture.cog.bedtime_list.callback(fixture.cog, ctx)

        self.assertEqual(ctx.send.await_count, 2)
        rendered = "\n".join(
            call.kwargs["embed"].description
            for call in ctx.send.await_args_list
        )
        self.assertIn("(`1`)", rendered)
        self.assertIn("(`12`)", rendered)
        self.assertNotIn("(`999`)", rendered)
        self.assertNotIn("<@", rendered)
        for call in ctx.send.await_args_list:
            mentions = call.kwargs["allowed_mentions"]
            self.assertFalse(mentions.everyone)
            self.assertFalse(mentions.users)
            self.assertFalse(mentions.roles)

    async def test_invalid_remove_target_has_a_safe_union_error(self) -> None:
        fixture = BedtimeFixture()
        ctx = fixture.make_context()
        parameter = fixture.cog.bedtime_remove.clean_params["member"]
        error = commands.BadUnionArgument(
            parameter,
            (discord.Member, int),
            [commands.MemberNotFound("missing"), commands.BadArgument("bad ID")],
        )

        handled = await fixture.cog._send_command_error(ctx, error)

        self.assertTrue(handled)
        self.assertIn("user ID", ctx.send.await_args.args[0])


class TestBedtimeScheduler(unittest.IsolatedAsyncioTestCase):
    async def test_due_reminder_mentions_once_and_advances_to_next_day(self) -> None:
        fixture = BedtimeFixture([reminder_document()])

        processed = await fixture.cog.process_due_reminders(NOW)

        self.assertEqual(processed, 1)
        fixture.channel.send.assert_awaited_once()
        args, kwargs = fixture.channel.send.await_args
        self.assertIn(f"<@{USER_ID}>", args[0])
        mentions = kwargs["allowed_mentions"]
        self.assertEqual(mentions.users, [fixture.member])
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.replied_user)

        stored = fixture.collection.documents[0]
        self.assertEqual(stored["last_announced_bedtime_date"], "2026-08-29")
        self.assertEqual(
            stored["next_mention_at"],
            datetime(2026, 8, 30, 15, 0),
        )

        self.assertEqual(await fixture.cog.process_due_reminders(NOW), 0)
        fixture.channel.send.assert_awaited_once()

    async def test_restart_deduplication_skips_an_announced_window(self) -> None:
        fixture = BedtimeFixture(
            [reminder_document(last_announced="2026-08-29")]
        )

        self.assertEqual(await fixture.cog.process_due_reminders(NOW), 1)

        fixture.channel.send.assert_not_awaited()
        self.assertEqual(
            fixture.collection.documents[0]["next_mention_at"],
            datetime(2026, 8, 30, 15, 0),
        )

    async def test_stale_deadline_after_wake_is_skipped(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        after_wake = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)

        self.assertEqual(
            await fixture.cog.process_due_reminders(after_wake),
            1,
        )

        fixture.channel.send.assert_not_awaited()
        stored = fixture.collection.documents[0]
        self.assertIsNone(stored["last_announced_bedtime_date"])
        self.assertEqual(stored["next_mention_at"], datetime(2026, 8, 30, 15, 0))

    async def test_missing_member_and_permanent_send_failure_skip_the_day(
        self,
    ) -> None:
        absent = BedtimeFixture([reminder_document()])
        absent.members.pop(USER_ID)

        await absent.cog.process_due_reminders(NOW)

        absent.channel.send.assert_not_awaited()
        self.assertEqual(
            absent.collection.documents[0]["next_mention_at"],
            datetime(2026, 8, 30, 15, 0),
        )

        forbidden = BedtimeFixture([reminder_document()])
        forbidden.channel.send.side_effect = make_http_error(403)

        await forbidden.cog.process_due_reminders(NOW)

        forbidden.channel.send.assert_awaited_once()
        self.assertEqual(
            forbidden.collection.documents[0]["next_mention_at"],
            datetime(2026, 8, 30, 15, 0),
        )

    async def test_missing_channel_and_permissions_skip_the_day(self) -> None:
        missing_channel = BedtimeFixture([reminder_document()])
        missing_channel.channels.pop(CHANNEL_ID)

        await missing_channel.cog.process_due_reminders(NOW)

        missing_channel.channel.send.assert_not_awaited()
        self.assertEqual(
            missing_channel.collection.documents[0]["next_mention_at"],
            datetime(2026, 8, 30, 15, 0),
        )

        denied = BedtimeFixture([reminder_document()])
        denied.channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True,
            send_messages=False,
        )

        await denied.cog.process_due_reminders(NOW)

        denied.channel.send.assert_not_awaited()
        self.assertEqual(
            denied.collection.documents[0]["next_mention_at"],
            datetime(2026, 8, 30, 15, 0),
        )

    async def test_transient_send_failure_retries_inside_the_window(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        fixture.channel.send.side_effect = make_http_error(500)

        await fixture.cog.process_due_reminders(NOW)

        self.assertEqual(
            fixture.collection.documents[0]["next_mention_at"],
            datetime(2026, 8, 29, 16, 1),
        )
        self.assertIsNone(
            fixture.collection.documents[0]["last_announced_bedtime_date"]
        )

        fixture.channel.send.side_effect = None
        retry_time = NOW + timedelta(minutes=1)
        await fixture.cog.process_due_reminders(retry_time)

        self.assertEqual(fixture.channel.send.await_count, 2)
        self.assertEqual(
            fixture.collection.documents[0]["last_announced_bedtime_date"],
            "2026-08-29",
        )

    async def test_due_query_failure_is_isolated(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        fixture.collection.find_error = PyMongoError("offline")

        self.assertEqual(await fixture.cog.process_due_reminders(NOW), 0)
        fixture.channel.send.assert_not_awaited()

    async def test_successful_send_is_not_repeated_when_state_write_fails(
        self,
    ) -> None:
        fixture = BedtimeFixture([reminder_document()])
        fixture.collection.write_error = PyMongoError("read-only")

        self.assertEqual(await fixture.cog.process_due_reminders(NOW), 0)
        self.assertEqual(await fixture.cog.process_due_reminders(NOW), 0)

        fixture.channel.send.assert_awaited_once()
        cached = fixture.cog.reminders_by_member[(GUILD_ID, USER_ID)]
        self.assertEqual(cached["last_announced_bedtime_date"], "2026-08-29")

        fixture.collection.write_error = None
        self.assertEqual(await fixture.cog.process_due_reminders(NOW), 1)
        fixture.channel.send.assert_awaited_once()
        self.assertEqual(
            fixture.collection.documents[0]["next_mention_at"],
            datetime(2026, 8, 30, 15, 0),
        )

    async def test_malformed_full_batch_is_quarantined_before_valid_row(
        self,
    ) -> None:
        malformed = []
        for user_id in range(1_000, 1_100):
            document = reminder_document(
                user_id=user_id,
                wake_minutes=22 * 60,
            )
            malformed.append(document)
        fixture = BedtimeFixture([*malformed, reminder_document()])

        processed = await fixture.cog.process_due_reminders(NOW)

        self.assertEqual(processed, 101)
        fixture.channel.send.assert_awaited_once()
        quarantined = fixture.collection.documents[:100]
        self.assertTrue(
            all("next_mention_at" not in document for document in quarantined)
        )
        self.assertTrue(
            all(
                document.get("invalid_reason") == "malformed bedtime reminder"
                for document in quarantined
            )
        )

    async def test_scheduler_drains_more_than_one_due_batch(self) -> None:
        short_now = datetime(2026, 8, 29, 15, 0, tzinfo=UTC)
        documents = [
            reminder_document(
                user_id=user_id,
                bedtime_minutes=22 * 60,
                wake_minutes=22 * 60 + 1,
            )
            for user_id in range(1_000, 1_101)
        ]
        fixture = BedtimeFixture(documents)
        for document in documents:
            user_id = int(document["user_id"])
            fixture.members[user_id] = make_member(user_id, guild=fixture.guild)

        processed = await fixture.cog.process_due_reminders(short_now)

        self.assertEqual(processed, 101)
        self.assertEqual(fixture.channel.send.await_count, 101)
        self.assertTrue(
            all(
                document["next_mention_at"] == datetime(2026, 8, 30, 15, 0)
                for document in fixture.collection.documents
            )
        )

    async def test_stale_due_snapshot_cannot_overwrite_admin_update(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        stale = dict(fixture.collection.documents[0])
        ctx = fixture.make_context()

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            await fixture.cog.bedtime_add.callback(
                fixture.cog,
                ctx,
                fixture.member,
                "23:30",
                "07:00",
                fixture.channel,
            )

        self.assertFalse(
            await fixture.cog._process_due_document(stale, NOW)
        )
        fixture.channel.send.assert_not_awaited()
        stored = fixture.collection.documents[0]
        self.assertEqual(stored["bedtime_minutes"], 23 * 60 + 30)
        self.assertEqual(stored["next_mention_at"], datetime(2026, 8, 29, 16, 30))

    async def test_in_flight_delivery_cannot_overwrite_admin_update(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        ctx = fixture.make_context()
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        async def delayed_send(*args: Any, **kwargs: Any) -> None:
            send_started.set()
            await release_send.wait()

        fixture.channel.send.side_effect = delayed_send
        scheduler = asyncio.create_task(fixture.cog.process_due_reminders(NOW))
        await send_started.wait()

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            update = asyncio.create_task(
                fixture.cog.bedtime_add.callback(
                    fixture.cog,
                    ctx,
                    fixture.member,
                    "23:30",
                    "07:00",
                    fixture.channel,
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(update.done())
            release_send.set()
            await asyncio.gather(scheduler, update)

        stored = fixture.collection.documents[0]
        self.assertEqual(stored["bedtime_minutes"], 23 * 60 + 30)
        self.assertEqual(stored["wake_minutes"], 7 * 60)
        self.assertEqual(stored["next_mention_at"], datetime(2026, 8, 29, 16, 30))


class TestBedtimeListener(unittest.IsolatedAsyncioTestCase):
    async def test_replies_to_text_commands_and_attachments_with_target_only_ping(
        self,
    ) -> None:
        fixture = BedtimeFixture([reminder_document()])
        messages = (
            fixture.make_message(content="hello"),
            fixture.make_message(content="!tf ping"),
            fixture.make_message(content="", attachments=[object()]),
        )

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            for message in messages:
                await fixture.cog.on_message(message)

        for message in messages:
            message.reply.assert_awaited_once()
            args, kwargs = message.reply.await_args
            self.assertIn(f"<@{USER_ID}>", args[0])
            self.assertFalse(kwargs["mention_author"])
            mentions = kwargs["allowed_mentions"]
            self.assertEqual(mentions.users, [fixture.member])
            self.assertFalse(mentions.everyone)
            self.assertFalse(mentions.roles)
            self.assertFalse(mentions.replied_user)

    async def test_ignores_outside_window_unlisted_other_guild_bot_webhook_and_dm(
        self,
    ) -> None:
        fixture = BedtimeFixture([reminder_document()])
        outside = fixture.make_message()
        unlisted = fixture.make_message(
            author=make_member(77, guild=fixture.guild)
        )
        other_guild = fixture.make_message(guild=SimpleNamespace(id=999))
        bot_message = fixture.make_message(
            author=make_member(88, guild=fixture.guild, bot=True)
        )
        webhook = fixture.make_message(webhook_id=123)
        dm = SimpleNamespace(
            guild=None,
            author=fixture.member,
            webhook_id=None,
            reply=AsyncMock(),
        )

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=datetime(2026, 8, 29, 23, 0, tzinfo=UTC),
        ):
            await fixture.cog.on_message(outside)

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            for message in (unlisted, other_guild, bot_message, webhook, dm):
                await fixture.cog.on_message(message)

        for message in (outside, unlisted, other_guild, bot_message, webhook, dm):
            message.reply.assert_not_awaited()

    async def test_discord_reply_failures_do_not_escape_listener(self) -> None:
        fixture = BedtimeFixture([reminder_document()])

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            for status in (403, 404, 500):
                message = fixture.make_message()
                message.reply.side_effect = make_http_error(status)
                with self.subTest(status=status):
                    await fixture.cog.on_message(message)
                    message.reply.assert_awaited_once()


class TestBedtimeLifecycle(unittest.TestCase):
    def test_cog_unload_cancels_scheduler(self) -> None:
        fixture = BedtimeFixture()

        with patch("discord.ext.tasks.Loop.cancel") as cancel:
            fixture.cog.cog_unload()

        cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
