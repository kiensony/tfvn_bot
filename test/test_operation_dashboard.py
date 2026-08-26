import csv
import io
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from cogs._beta_function import BetaFunctionError
from cogs.operation import operation_dashboard as dashboard_module
from cogs.operation._operation_helpers import (
    CSV_COLUMNS,
    MAX_ARGUMENT_LENGTH,
    MAX_EXPORT_ROWS,
    CsvPartTooLargeError,
    ExportRowLimitError,
    classify_command_error,
    command_error_type,
    get_audit_cutoff,
    get_prune_cutoff,
    sanitize_command_arguments,
    serialize_audit_csv,
    split_audit_csv,
)
from cogs.operation.operation_dashboard import (
    AuditLogView,
    DASHBOARD_TIMEOUT_SECONDS,
    ExportLogView,
    GuildAdminView,
    OperationDashboardCog,
    OperationDashboardView,
    PruneLogView,
)
from cogs.operation.server_stats import ServerStatsCog


UTC = timezone.utc


class AtomicCollection:
    """Small Mongo update model used to exercise $setOnInsert ordering."""

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.update_calls: list[tuple[dict, dict, bool]] = []

    def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> None:
        self.update_calls.append((query, update, upsert))
        event_id = query["_id"]
        inserted = event_id not in self.documents
        if inserted:
            if not upsert:
                return
            self.documents[event_id] = {}
            self.documents[event_id].update(update.get("$setOnInsert", {}))
        self.documents[event_id].update(update.get("$set", {}))


def make_cog(collection) -> OperationDashboardCog:
    cog = object.__new__(OperationDashboardCog)
    cog.bot = SimpleNamespace()
    cog.db = SimpleNamespace()
    cog.logs = collection
    cog.loaded_at = datetime(2026, 1, 1, tzinfo=UTC)
    return cog


def make_context(
    *,
    guild_id: int | None = 41,
    command_name: str | None = "moderation ban",
    message_id: int = 9001,
    content: str = "!tf moderation ban target https://secret.example/a  extra",
) -> SimpleNamespace:
    guild = None if guild_id is None else SimpleNamespace(id=guild_id)
    command = (
        None
        if command_name is None
        else SimpleNamespace(qualified_name=command_name)
    )
    message = SimpleNamespace(
        id=message_id,
        content=content,
        clean_content=content,
        created_at=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC),
    )
    return SimpleNamespace(
        guild=guild,
        command=command,
        message=message,
        channel=SimpleNamespace(id=501),
        author=SimpleNamespace(id=77, __str__=lambda self: "audit-admin"),
        prefix="!tf ",
        invoked_parents=("moderation",),
        invoked_with="ban",
    )


def make_interaction(
    *,
    guild_id: int | None = 41,
    user_id: int = 77,
    administrator: bool = True,
) -> SimpleNamespace:
    guild = (
        None
        if guild_id is None
        else SimpleNamespace(id=guild_id, filesize_limit=8 * 1024 * 1024)
    )
    response = SimpleNamespace(
        is_done=MagicMock(return_value=False),
        send_message=AsyncMock(),
        edit_message=AsyncMock(),
        defer=AsyncMock(),
    )
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(administrator=administrator),
        ),
        channel_id=501,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        original_response=AsyncMock(),
    )


def make_cursor(documents: list[dict]) -> MagicMock:
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.__iter__.return_value = iter(documents)
    return cursor


class TestOperationHelpers(unittest.TestCase):
    def test_sanitize_arguments_redacts_urls_normalizes_and_truncates(self) -> None:
        arguments = (
            "  first\nhttps://example.com/private?q=1   "
            "www.example.org/other\tdiscord.gg/private-code "
            "example.vn/token " + "x" * 700
        )

        result = sanitize_command_arguments(arguments)

        self.assertEqual(len(result), MAX_ARGUMENT_LENGTH)
        self.assertEqual(result.count("[url]"), 4)
        self.assertNotIn("example.com", result)
        self.assertNotIn("discord.gg", result)
        self.assertNotIn("\n", result)
        self.assertNotIn("\t", result)
        self.assertFalse(result.startswith(" "))
        self.assertEqual(sanitize_command_arguments(None), "")

    def test_error_classification_and_safe_type_unwrap(self) -> None:
        cooldown = commands.CommandOnCooldown(
            commands.Cooldown(1, 10),
            3.5,
            commands.BucketType.guild,
        )
        cases = (
            (cooldown, "cooldown"),
            (commands.MissingPermissions(["administrator"]), "denied"),
            (commands.DisabledCommand(), "denied"),
            (commands.BadArgument("bad input"), "invalid"),
            (commands.CommandNotFound("missing"), "invalid"),
            (commands.CommandInvokeError(ValueError("sensitive text")), "failed"),
        )

        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(classify_command_error(error), expected)

        wrapped = commands.CommandInvokeError(RuntimeError("do not persist me"))
        self.assertEqual(command_error_type(wrapped), "RuntimeError")
        self.assertNotIn("do not persist", command_error_type(wrapped))

    def test_audit_and_prune_ranges_have_exact_utc_boundaries(self) -> None:
        now = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)

        self.assertEqual(get_audit_cutoff("7d", now=now), now - timedelta(days=7))
        self.assertEqual(
            get_audit_cutoff("30d", now=now),
            now - timedelta(days=30),
        )
        self.assertEqual(
            get_prune_cutoff("180d", now=now),
            now - timedelta(days=180),
        )
        self.assertIsNone(get_audit_cutoff("all", now=now))
        self.assertIsNone(get_prune_cutoff("all", now=now))
        with self.assertRaises(ValueError):
            get_audit_cutoff("forever", now=now)
        with self.assertRaises(ValueError):
            get_prune_cutoff("7d", now=now)

    def test_csv_has_bom_stable_columns_escaping_and_formula_protection(self) -> None:
        created_at = datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC)
        document = {
            "created_at": created_at,
            "event_type": "command",
            "status": "succeeded",
            "command_name": "=HYPERLINK(\"bad\")",
            "arguments": "-1, \"quoted\"\nnext",
            "actor_id": 77,
            "actor_name": "@admin",
            "guild_id": 41,
            "details": {"formula": "+SUM(A1:A2)", "safe": True},
        }

        payload = serialize_audit_csv([document])

        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(rows[0]["created_at"], "2026-08-26T01:02:03Z")
        self.assertEqual(rows[0]["command_or_action"], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[0]["actor_name"], "'@admin")
        self.assertTrue(rows[0]["arguments"].startswith("'-1,"))
        self.assertIn("\nnext", rows[0]["arguments"])
        self.assertEqual(
            rows[0]["details"],
            '{"formula":"+SUM(A1:A2)","safe":true}',
        )

    def test_csv_split_produces_independent_bom_files_with_headers(self) -> None:
        document = {
            "created_at": datetime(2026, 8, 26, tzinfo=UTC),
            "event_type": "command",
            "status": "succeeded",
            "command_name": "ping",
            "guild_id": 41,
        }
        one_document = serialize_audit_csv([document])

        parts = split_audit_csv([document, document], max_bytes=len(one_document))

        self.assertEqual(len(parts), 2)
        for payload in parts:
            self.assertLessEqual(len(payload), len(one_document))
            self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
            rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["command_or_action"], "ping")

        with self.assertRaises(CsvPartTooLargeError):
            split_audit_csv([document], max_bytes=10)

    def test_csv_refuses_more_than_row_limit_without_silent_truncation(self) -> None:
        documents = ({} for _ in range(MAX_EXPORT_ROWS + 1))

        with self.assertRaises(ExportRowLimitError):
            serialize_audit_csv(documents)


class TestCommandAuditLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_group_fallback_does_not_drop_first_argument(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context(
            command_name="afk",
            message_id=9000,
            content="!tf afk lunch break",
        )
        ctx.invoked_parents = ("afk",)
        ctx.invoked_with = "afk"

        await cog.on_command_completion(ctx)

        persisted = collection.documents["command:9000"]
        self.assertEqual(persisted["invoked_with"], "afk")
        self.assertEqual(persisted["arguments"], "lunch break")

    async def test_running_then_completion_persists_sanitized_command_fields(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context()

        await cog.on_command(ctx)
        running = collection.documents["command:9001"]
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["guild_id"], 41)
        self.assertEqual(running["channel_id"], 501)
        self.assertEqual(running["actor_id"], 77)
        self.assertEqual(running["command_name"], "moderation ban")
        self.assertEqual(running["invoked_with"], "moderation ban")
        self.assertEqual(running["arguments"], "target [url] extra")
        self.assertIsNone(running["completed_at"])

        await cog.on_command_completion(ctx)

        completed = collection.documents["command:9001"]
        self.assertEqual(completed["status"], "succeeded")
        self.assertIsInstance(completed["completed_at"], datetime)
        self.assertIsNone(completed["error_type"])
        self.assertEqual(completed["created_at"], ctx.message.created_at)

    async def test_final_event_before_start_cannot_be_overwritten_by_running(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context(message_id=9002)

        await cog.on_command_completion(ctx)
        await cog.on_command(ctx)

        persisted = collection.documents["command:9002"]
        self.assertEqual(persisted["status"], "succeeded")
        self.assertIsNotNone(persisted["completed_at"])
        first_update = collection.update_calls[0][1]
        delayed_start_update = collection.update_calls[1][1]
        self.assertEqual(first_update["$set"]["status"], "succeeded")
        self.assertNotIn("$set", delayed_start_update)
        self.assertEqual(
            delayed_start_update["$setOnInsert"]["status"],
            "running",
        )

    async def test_error_event_stores_category_and_class_not_exception_text(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context(message_id=9003)
        error = commands.CommandInvokeError(RuntimeError("database password"))

        await cog.on_command_error(ctx, error)

        persisted = collection.documents["command:9003"]
        self.assertEqual(persisted["status"], "failed")
        self.assertEqual(persisted["error_type"], "RuntimeError")
        self.assertNotIn("database password", str(persisted))

    async def test_dm_and_unknown_command_events_are_ignored(self) -> None:
        collection = MagicMock()
        cog = make_cog(collection)

        await cog.on_command(make_context(guild_id=None))
        await cog.on_command_error(
            make_context(command_name=None),
            commands.CommandNotFound("not registered"),
        )

        collection.update_one.assert_not_called()

    async def test_pymongo_write_failure_never_escapes_command_listener(self) -> None:
        collection = MagicMock()
        collection.update_one.side_effect = PyMongoError("Mongo unavailable")
        cog = make_cog(collection)

        with patch.object(dashboard_module.logger, "exception") as log_failure:
            await cog.on_command(make_context())
            await cog.on_command_completion(make_context())
            await cog.on_command_error(
                make_context(),
                commands.BadArgument("bad input"),
            )

        self.assertEqual(collection.update_one.call_count, 3)
        self.assertEqual(log_failure.call_count, 3)


class TestGuildScopedMongoOperations(unittest.IsolatedAsyncioTestCase):
    def test_index_matches_guild_time_and_deterministic_sort(self) -> None:
        collection = MagicMock()
        cog = make_cog(collection)

        self.assertTrue(cog._ensure_indexes())

        collection.create_index.assert_called_once_with(
            [
                ("guild_id", ASCENDING),
                ("created_at", DESCENDING),
                ("_id", DESCENDING),
            ],
            name="guild_created_at_id",
        )

    async def test_page_and_export_queries_are_guild_scoped_and_newest_first(self) -> None:
        collection = MagicMock()
        page_cursor = make_cursor([{"_id": "page"}])
        export_cursor = make_cursor([{"_id": "export"}])
        collection.find.side_effect = [page_cursor, export_cursor]
        collection.count_documents.return_value = 1
        cog = make_cog(collection)

        documents, total = await cog.fetch_audit_page(
            guild_id=41,
            range_key="30d",
            offset=-10,
            limit=999,
        )
        exported = await cog.fetch_export_documents(
            guild_id=52,
            range_key="all",
        )

        self.assertEqual(documents, [{"_id": "page"}])
        self.assertEqual(total, 1)
        self.assertEqual(exported, [{"_id": "export"}])
        page_query = collection.find.call_args_list[0].args[0]
        export_query = collection.find.call_args_list[1].args[0]
        self.assertEqual(page_query["guild_id"], 41)
        self.assertEqual(set(page_query), {"guild_id", "created_at"})
        self.assertIn("$gte", page_query["created_at"])
        self.assertEqual(export_query, {"guild_id": 52})
        page_cursor.sort.assert_called_once_with(
            [("created_at", DESCENDING), ("_id", DESCENDING)]
        )
        page_cursor.skip.assert_called_once_with(0)
        page_cursor.limit.assert_called_once_with(dashboard_module.AUDIT_PAGE_SIZE)
        export_cursor.sort.assert_called_once_with(
            [("created_at", DESCENDING), ("_id", DESCENDING)]
        )
        export_cursor.limit.assert_called_once_with(MAX_EXPORT_ROWS + 1)
        count_query = collection.count_documents.call_args.args[0]
        self.assertEqual(count_query, page_query)

    async def test_export_query_detects_row_limit(self) -> None:
        collection = MagicMock()
        cursor = make_cursor([{}, {}, {}])
        collection.find.return_value = cursor
        cog = make_cog(collection)

        with patch.object(dashboard_module, "MAX_EXPORT_ROWS", 2):
            with self.assertRaises(ExportRowLimitError):
                await cog.fetch_export_documents(guild_id=41, range_key="all")

        cursor.limit.assert_called_once_with(3)
        self.assertEqual(collection.find.call_args.args[0], {"guild_id": 41})

    async def test_count_and_prune_filters_never_cross_guilds(self) -> None:
        collection = MagicMock()
        collection.count_documents.return_value = 8
        collection.delete_many.return_value = SimpleNamespace(deleted_count=7)
        cog = make_cog(collection)
        cutoff = datetime(2026, 5, 1, tzinfo=UTC)

        count = await cog.count_prunable_logs(guild_id=41, cutoff=cutoff)
        deleted = await cog.prune_logs(guild_id=52, cutoff=None)

        self.assertEqual(count, 8)
        self.assertEqual(deleted, 7)
        collection.count_documents.assert_called_once_with(
            {"guild_id": 41, "created_at": {"$lt": cutoff}}
        )
        collection.delete_many.assert_called_once_with({"guild_id": 52})

    async def test_admin_action_is_written_to_interaction_guild(self) -> None:
        collection = MagicMock()
        cog = make_cog(collection)
        interaction = make_interaction(guild_id=73)

        persisted = await cog.record_admin_action(
            interaction=interaction,
            action="export_logs",
            status="succeeded",
            details={"rows": 4},
        )

        self.assertTrue(persisted)
        document = collection.insert_one.call_args.args[0]
        self.assertEqual(document["guild_id"], 73)
        self.assertEqual(document["event_type"], "admin_action")
        self.assertEqual(document["action"], "export_logs")
        self.assertEqual(document["details"], {"rows": 4})

    async def test_admin_action_failure_is_best_effort(self) -> None:
        collection = MagicMock()
        collection.insert_one.side_effect = PyMongoError("Mongo unavailable")
        cog = make_cog(collection)

        with patch.object(dashboard_module.logger, "exception") as log_failure:
            persisted = await cog.record_admin_action(
                interaction=make_interaction(guild_id=73),
                action="prune_logs",
                status="failed",
                details={"reason": "database"},
            )

        self.assertFalse(persisted)
        log_failure.assert_called_once()


class TestDashboardSnapshot(unittest.IsolatedAsyncioTestCase):
    def make_dashboard_cog(self):
        current_guild = SimpleNamespace(
            id=41,
            member_count=20,
            members=[object()] * 4,
            channels=[object(), object(), object()],
        )
        other_guild = SimpleNamespace(
            id=52,
            member_count=7,
            members=[object()] * 2,
            channels=[],
        )
        started_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
        bot = SimpleNamespace(
            guilds=[current_guild, other_guild],
            latency=0.1236,
            environment="development",
            is_ready=MagicMock(return_value=True),
            get_cog=MagicMock(
                return_value=SimpleNamespace(start_time=started_at)
            ),
        )
        logs = MagicMock()
        logs.count_documents.return_value = 321
        logs.aggregate.return_value = [
            {"_id": "succeeded", "count": 8},
            {"_id": "failed", "count": 2},
        ]
        cog = make_cog(logs)
        cog.bot = bot
        cog.db = MagicMock()
        return cog, current_guild, started_at

    async def test_healthy_snapshot_and_embed_render_runtime_and_mongo_counts(
        self,
    ) -> None:
        cog, guild, started_at = self.make_dashboard_cog()
        now = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
        with (
            patch.object(dashboard_module.discord.utils, "utcnow", return_value=now),
            patch.object(
                dashboard_module.time,
                "perf_counter",
                side_effect=(10.0, 10.0124),
            ),
        ):
            snapshot = await cog.collect_snapshot(guild)

        self.assertTrue(snapshot.ready)
        self.assertTrue(snapshot.mongo_available)
        self.assertEqual(snapshot.started_at, started_at)
        self.assertEqual(snapshot.latency_ms, 124)
        self.assertEqual(snapshot.mongo_latency_ms, 12)
        self.assertEqual(snapshot.connected_guilds, 2)
        self.assertEqual(snapshot.cached_members, 6)
        self.assertEqual(snapshot.guild_members, 20)
        self.assertEqual(snapshot.guild_channels, 3)
        self.assertEqual(snapshot.retained_logs, 321)
        self.assertEqual(snapshot.recent_statuses, {"succeeded": 8, "failed": 2})
        cog.db.command.assert_called_once_with("ping")
        cog.logs.count_documents.assert_called_once_with({"guild_id": 41})
        match = cog.logs.aggregate.call_args.args[0][0]["$match"]
        self.assertEqual(match["guild_id"], 41)
        self.assertEqual(match["created_at"], {"$gte": now - timedelta(days=1)})

        cog.collect_snapshot = AsyncMock(return_value=snapshot)
        embed = await cog.build_dashboard_embed(guild)
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertEqual(embed.color, discord.Color.green())
        self.assertIn("development", rendered)
        self.assertIn("124 ms", rendered)
        self.assertIn("321", rendered)
        self.assertIn("✅ 8", rendered)
        self.assertIn("❌ 2", rendered)

    async def test_mongo_failure_returns_degraded_snapshot_and_embed(self) -> None:
        cog, guild, _ = self.make_dashboard_cog()
        cog.db.command.side_effect = PyMongoError("Mongo unavailable")

        with patch.object(dashboard_module.logger, "exception") as log_failure:
            snapshot = await cog.collect_snapshot(guild)

        self.assertFalse(snapshot.mongo_available)
        self.assertIsNone(snapshot.mongo_latency_ms)
        self.assertIsNone(snapshot.retained_logs)
        self.assertEqual(snapshot.recent_statuses, {})
        cog.logs.count_documents.assert_not_called()
        cog.logs.aggregate.assert_not_called()
        log_failure.assert_called_once()

        cog.collect_snapshot = AsyncMock(return_value=snapshot)
        embed = await cog.build_dashboard_embed(guild)
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertEqual(embed.color, discord.Color.orange())
        self.assertIn("❌ Ping: **Không khả dụng**", rendered)
        self.assertIn("Log đang giữ: **Không khả dụng**", rendered)


class TestOperationViews(unittest.IsolatedAsyncioTestCase):
    async def test_view_rechecks_same_guild_and_live_administrator(self) -> None:
        view = GuildAdminView(guild_id=41, author_id=77)

        wrong_guild = make_interaction(guild_id=99)
        stranger = make_interaction(guild_id=41, user_id=88)
        lost_permission = make_interaction(guild_id=41, administrator=False)
        allowed = make_interaction(guild_id=41)

        self.assertFalse(await view.interaction_check(wrong_guild))
        self.assertFalse(await view.interaction_check(stranger))
        self.assertFalse(await view.interaction_check(lost_permission))
        self.assertTrue(await view.interaction_check(allowed))
        for denied in (wrong_guild, stranger, lost_permission):
            denied.response.send_message.assert_awaited_once()
            self.assertTrue(
                denied.response.send_message.await_args.kwargs["ephemeral"]
            )
        allowed.response.send_message.assert_not_awaited()

    async def test_dashboard_timeout_disables_every_control_and_edits_message(
        self,
    ) -> None:
        view = OperationDashboardView(cog=SimpleNamespace(), guild_id=41)
        message = SimpleNamespace(edit=AsyncMock())
        view.message = message
        self.assertTrue(all(not child.disabled for child in view.children))

        await view.on_timeout()

        self.assertTrue(all(child.disabled for child in view.children))
        message.edit.assert_awaited_once_with(view=view)

    async def test_clear_all_requires_two_confirmations_then_audits_after_delete(
        self,
    ) -> None:
        calls = MagicMock()
        prune_logs = AsyncMock(return_value=12)
        record_admin_action = AsyncMock()
        calls.attach_mock(prune_logs, "prune_logs")
        calls.attach_mock(record_admin_action, "record_admin_action")
        cog = SimpleNamespace(
            count_prunable_logs=AsyncMock(return_value=12),
            prune_logs=prune_logs,
            record_admin_action=record_admin_action,
        )
        view = PruneLogView(cog=cog, guild_id=41, author_id=77)
        view.range_key = "all"
        await view.refresh_preview()
        interaction = make_interaction(guild_id=41)

        await view.confirm.callback(interaction)

        self.assertTrue(view.clear_all_armed)
        self.assertEqual(view.confirm.label, "Xác nhận xóa toàn bộ")
        prune_logs.assert_not_awaited()
        interaction.response.defer.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once()

        await view.confirm.callback(interaction)

        prune_logs.assert_awaited_once_with(guild_id=41, cutoff=None)
        record_admin_action.assert_awaited_once()
        action_kwargs = record_admin_action.await_args.kwargs
        self.assertEqual(action_kwargs["action"], "prune_logs")
        self.assertEqual(action_kwargs["status"], "succeeded")
        self.assertEqual(action_kwargs["details"]["deleted_count"], 12)
        self.assertEqual(
            [
                entry
                for entry in calls.mock_calls
                if entry[0] in {"prune_logs", "record_admin_action"}
            ],
            [
                call.prune_logs(guild_id=41, cutoff=None),
                call.record_admin_action(
                    interaction=interaction,
                    action="prune_logs",
                    status="succeeded",
                    details={
                        "range": "all",
                        "cutoff": None,
                        "deleted_count": 12,
                    },
                ),
            ],
        )
        self.assertTrue(view.completed)

    async def test_export_download_splits_files_and_records_admin_action(
        self,
    ) -> None:
        documents = [
            {
                "created_at": datetime(2026, 8, 26, tzinfo=UTC),
                "event_type": "command",
                "status": "succeeded",
                "command_name": "big_speaker",
                "arguments": "x" * 600,
                "guild_id": 41,
            },
            {
                "created_at": datetime(2026, 8, 25, tzinfo=UTC),
                "event_type": "command",
                "status": "succeeded",
                "command_name": "quote",
                "arguments": "y" * 600,
                "guild_id": 41,
            },
        ]
        cog = SimpleNamespace(
            fetch_export_documents=AsyncMock(return_value=documents),
            record_admin_action=AsyncMock(return_value=True),
        )
        view = ExportLogView(cog=cog, guild_id=41, author_id=77)
        interaction = make_interaction(guild_id=41)
        interaction.guild.filesize_limit = dashboard_module.UPLOAD_SIZE_RESERVE + 1_024

        await view.download.callback(interaction)

        self.assertEqual(view.range_key, "all")
        self.assertTrue(view.completed)
        self.assertEqual(interaction.followup.send.await_count, 2)
        filenames = [
            item.kwargs["file"].filename
            for item in interaction.followup.send.await_args_list
        ]
        self.assertTrue(all(name.endswith(".csv") for name in filenames))
        self.assertIn("part-01-of-02", filenames[0])
        self.assertIn("part-02-of-02", filenames[1])
        cog.record_admin_action.assert_awaited_once()
        action = cog.record_admin_action.await_args.kwargs
        self.assertEqual(action["action"], "export_logs")
        self.assertEqual(action["status"], "succeeded")
        self.assertEqual(action["details"], {"range": "all", "rows": 2, "parts": 2})

    async def test_prune_cancel_never_deletes_records(self) -> None:
        cog = SimpleNamespace(prune_logs=AsyncMock())
        view = PruneLogView(cog=cog, guild_id=41, author_id=77)
        interaction = make_interaction(guild_id=41)

        await view.cancel.callback(interaction)

        cog.prune_logs.assert_not_awaited()
        self.assertTrue(view.is_finished())
        interaction.edit_original_response.assert_awaited_once()

    def test_audit_embed_treats_naive_mongo_dates_as_utc(self) -> None:
        view = AuditLogView(
            cog=SimpleNamespace(),
            guild_id=41,
            author_id=77,
        )
        created_at = datetime(2026, 8, 26, 12, 0)
        view.documents = [
            {
                "created_at": created_at,
                "event_type": "command",
                "status": "succeeded",
                "command_name": "ping",
                "actor_id": 77,
                "actor_name": "admin",
                "channel_id": 501,
            }
        ]
        view.total = 1

        embed = view.build_embed()

        expected_epoch = int(created_at.replace(tzinfo=UTC).timestamp())
        self.assertIn(f"<t:{expected_epoch}:R>", embed.fields[0].value)

    async def test_failed_prune_range_preview_preserves_confirmed_cutoff(
        self,
    ) -> None:
        count_prunable_logs = AsyncMock(
            side_effect=[8, PyMongoError("Mongo unavailable")]
        )
        cog = SimpleNamespace(count_prunable_logs=count_prunable_logs)
        view = PruneLogView(cog=cog, guild_id=41, author_id=77)
        view.range_key = "180d"
        view.range_select.set_selected("180d")
        await view.refresh_preview()
        original_cutoff = view.cutoff
        interaction = make_interaction(guild_id=41)

        with patch.object(dashboard_module.logger, "exception"):
            await view._change_range(interaction, "30d")

        self.assertEqual(view.range_key, "180d")
        self.assertEqual(view.cutoff, original_cutoff)
        self.assertEqual(view.matching_count, 8)
        self.assertFalse(view.confirm.disabled)
        interaction.followup.send.assert_awaited_once()

    async def test_bot_status_command_opens_dashboard_without_replacing_server_stats(
        self,
    ) -> None:
        cog = object.__new__(OperationDashboardCog)
        cog.build_dashboard_embed = AsyncMock(
            return_value=discord.Embed(title="Bot status")
        )
        sent_message = SimpleNamespace(id=123)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=41),
            reply=AsyncMock(return_value=sent_message),
        )

        await OperationDashboardCog.show_bot_status.callback(cog, ctx)

        ctx.reply.assert_awaited_once()
        kwargs = ctx.reply.await_args.kwargs
        self.assertIsInstance(kwargs["view"], OperationDashboardView)
        self.assertEqual(kwargs["view"].timeout, DASHBOARD_TIMEOUT_SECONDS)
        self.assertIs(kwargs["view"].message, sent_message)
        self.assertEqual(
            [child.label for child in kwargs["view"].children],
            ["Làm mới", "Audit logs", "Tải CSV", "Dọn log"],
        )
        self.assertEqual(OperationDashboardCog.show_bot_status.name, "bot_status")
        self.assertEqual(ServerStatsCog.server_stats.name, "server_stats")


class TestServerStatsRegression(unittest.IsolatedAsyncioTestCase):
    async def test_existing_server_stats_counts_and_rendering_are_unchanged(self) -> None:
        cog = ServerStatsCog(SimpleNamespace())
        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        cog.start_time = now - timedelta(days=1, hours=2, minutes=3)
        ctx = SimpleNamespace(send=AsyncMock())

        await cog.on_command(ctx)
        await cog.on_command(ctx)
        await cog.on_command_error(ctx, BetaFunctionError("expected denial"))
        await cog.on_command_error(ctx, commands.CommandError("unexpected"))
        with patch("cogs.operation.server_stats.discord.utils.utcnow", return_value=now):
            await ServerStatsCog.server_stats.callback(cog, ctx)

        self.assertEqual(cog.command_count, 2)
        self.assertEqual(cog.exception_count, 1)
        ctx.send.assert_awaited_once_with(
            "**Thống kê máy chủ:**\n"
            "- Khởi chạy lúc: 2026-08-25 09:57:00 UTC\n"
            "- Thời gian hoạt động: 1 ngày, 2 giờ, 3 phút\n"
            "- Lệnh đã thực thi: 2\n"
            "- Lệnh lỗi: 1"
        )

    async def test_existing_server_stats_permission_and_cooldown_are_unchanged(
        self,
    ) -> None:
        command = ServerStatsCog.server_stats
        self.assertEqual(command.name, "server_stats")
        self.assertEqual(len(command.checks), 1)
        self.assertTrue(
            command.checks[0](
                SimpleNamespace(
                    permissions=discord.Permissions(administrator=True)
                )
            )
        )
        with self.assertRaises(commands.MissingPermissions):
            command.checks[0](
                SimpleNamespace(
                    permissions=discord.Permissions(administrator=False)
                )
            )

        buckets = command._buckets
        self.assertEqual(buckets._cooldown.rate, 1)
        self.assertEqual(buckets._cooldown.per, 10)
        self.assertIs(buckets._type, commands.BucketType.guild)


if __name__ == "__main__":
    unittest.main()
