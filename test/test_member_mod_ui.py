import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from cogs.mod._interaction_ui import ConfigurableModerationView
from cogs.mod.kick import KickCog, KickRequest
from cogs.mod.mute import MuteCog, MuteRequest
from cogs.mod.softban import SoftbanCog, SoftbanRequest
from cogs.mod.timeout import TimeoutCog, TimeoutRequest
from cogs.mod.warn import WarnCommandCog, WarnRequest


class FakeRole:
    def __init__(
        self,
        role_id: int,
        position: int,
        *,
        name: str,
        managed: bool = False,
        default: bool = False,
    ) -> None:
        self.id = role_id
        self.position = position
        self.name = name
        self.managed = managed
        self._default = default
        self.mention = f"<@&{role_id}>"

    def is_default(self) -> bool:
        return self._default

    def __lt__(self, other: "FakeRole") -> bool:
        return self.position < other.position

    def __le__(self, other: "FakeRole") -> bool:
        return self.position <= other.position

    def __gt__(self, other: "FakeRole") -> bool:
        return self.position > other.position

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


class FakeMember:
    def __init__(
        self,
        guild: "FakeGuild",
        member_id: int,
        position: int,
        *,
        name: str,
        bot: bool = False,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.name = name
        self.bot = bot
        self.top_role = FakeRole(member_id, position, name=f"role-{name}")
        self.guild_permissions = SimpleNamespace(
            kick_members=True,
            ban_members=True,
            manage_roles=True,
            moderate_members=True,
            manage_messages=True,
        )
        self.roles: list[FakeRole] = []
        self.mention = f"<@{member_id}>"
        self.kick = AsyncMock()
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()
        self.edit = AsyncMock()
        self.timeout = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.owner_id = 1000
        self.me: FakeMember | None = None
        self.roles: list[FakeRole] = []
        self.members: dict[int, FakeMember] = {}
        self.fetch_member = AsyncMock()

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((role for role in self.roles if role.id == role_id), None)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, MagicMock] = {}

    def __getitem__(self, name: str) -> MagicMock:
        return self.collections.setdefault(name, MagicMock())


class FakeChannel:
    def __init__(self) -> None:
        self.id = 555
        self.fetch_message = AsyncMock()


def make_fixture() -> tuple[
    FakeGuild,
    FakeMember,
    FakeMember,
    FakeChannel,
    FakeRole,
    FakeRole,
]:
    guild = FakeGuild()
    everyone = FakeRole(1, 0, name="@everyone", default=True)
    original = FakeRole(2, 5, name="Member")
    muted = FakeRole(3, 6, name="Muted")
    handcuffed = FakeRole(4, 7, name="Tù ngay")
    guild.roles = [everyone, original, muted, handcuffed]
    bot_member = FakeMember(guild, 999, 100, name="mod-bot", bot=True)
    moderator = FakeMember(guild, 42, 80, name="moderator")
    target = FakeMember(guild, 77, 10, name="target")
    target.roles = [everyone, original]
    guild.me = bot_member
    guild.members = {
        bot_member.id: bot_member,
        moderator.id: moderator,
        target.id: target,
    }
    guild.fetch_member.return_value = target
    return guild, moderator, target, FakeChannel(), muted, handcuffed


def make_context(
    guild: FakeGuild,
    moderator: FakeMember,
    channel: FakeChannel,
    *,
    reference: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=channel,
        message=SimpleNamespace(reference=reference),
        clean_prefix="!tf ",
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        send=AsyncMock(),
    )


def make_reply_reference(
    guild: FakeGuild,
    channel: FakeChannel,
    target: FakeMember,
) -> SimpleNamespace:
    message = SimpleNamespace(
        id=123,
        guild=guild,
        channel=channel,
        author=target,
        webhook_id=None,
    )
    return SimpleNamespace(
        message_id=123,
        channel_id=channel.id,
        resolved=None,
        cached_message=message,
    )


def make_interaction(
    guild: FakeGuild,
    moderator: FakeMember,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        user=moderator,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def make_forbidden_exception() -> discord.Forbidden:
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(
        response,
        {"code": 50013, "message": "Missing Permissions"},
    )


class TestMemberModerationCommands(unittest.IsolatedAsyncioTestCase):
    async def test_reply_opens_each_member_workflow_without_mutating(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        reference = make_reply_reference(guild, channel, target)
        database = FakeDatabase()
        bot = SimpleNamespace(db=database)
        cases = (
            (KickCog(bot), KickCog.kick_member),
            (MuteCog(bot), MuteCog.mute_member),
            (MuteCog(bot), MuteCog.unmute_member),
            (TimeoutCog(bot), TimeoutCog.timeout),
            (TimeoutCog(bot), TimeoutCog.untimeout),
            (WarnCommandCog(bot), WarnCommandCog.warn_user),
            (SoftbanCog(bot), SoftbanCog.softban_member),
            (SoftbanCog(bot), SoftbanCog.unsoftban_member),
        )

        for cog, command in cases:
            with self.subTest(command=command.name):
                ctx = make_context(
                    guild,
                    moderator,
                    channel,
                    reference=reference,
                )
                await command.callback(cog, ctx)
                view = ctx.reply.await_args.kwargs["view"]
                self.assertIsInstance(view, ConfigurableModerationView)
                self.assertEqual(view.target.id, target.id)
                view.stop()

        target.kick.assert_not_awaited()
        target.add_roles.assert_not_awaited()
        target.remove_roles.assert_not_awaited()
        target.edit.assert_not_awaited()
        target.timeout.assert_not_awaited()

    async def test_reply_with_arguments_is_rejected(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        ctx = make_context(
            guild,
            moderator,
            channel,
            reference=make_reply_reference(guild, channel, target),
        )
        cog = KickCog(SimpleNamespace())

        await cog.kick_member.callback(cog, ctx, target, reason="spam")

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("không kèm đối số", ctx.reply.await_args.args[0])

    async def test_direct_reason_is_prefilled_but_kick_waits_for_confirmation(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        bot = SimpleNamespace()
        cog = KickCog(bot)
        ctx = make_context(guild, moderator, channel)

        await cog.kick_member.callback(
            cog,
            ctx,
            target,
            reason="  repeated   spam  ",
        )
        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.initial_reason, "repeated spam")
        self.assertEqual(view.step, "reason")
        target.kick.assert_not_awaited()

        reason_select = next(
            item for item in view.children if isinstance(item, discord.ui.Select)
        )
        reason_select._values = ["provided"]
        await reason_select.callback(make_interaction(guild, moderator))
        self.assertEqual(view.step, "confirm")
        target.kick.assert_not_awaited()

        with patch(
            "cogs.mod.kick.record_case",
            new_callable=AsyncMock,
            return_value=12,
        ):
            await view.children[0].callback(make_interaction(guild, moderator))

        guild.fetch_member.assert_awaited_once_with(target.id)
        target.kick.assert_awaited_once_with(
            reason="repeated spam (Requested by moderator)"
        )
        self.assertTrue(view.completed)

    async def test_timeout_direct_duration_is_prefilled(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        cog = TimeoutCog(SimpleNamespace())
        ctx = make_context(guild, moderator, channel)

        await cog.timeout.callback(cog, ctx, target, 90, reason="spam")

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.step, "field:duration_minutes")
        self.assertEqual(view.values["duration_minutes"].value, 90)
        self.assertEqual(view.initial_reason, "spam")
        target.timeout.assert_not_awaited()
        view.stop()

    async def test_live_permission_loss_blocks_workflow(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        cog = KickCog(SimpleNamespace())
        ctx = make_context(guild, moderator, channel)
        await cog.kick_member.callback(cog, ctx, target)
        view = ctx.reply.await_args.kwargs["view"]
        moderator.guild_permissions.kick_members = False
        interaction = make_interaction(guild, moderator)

        self.assertFalse(await view.interaction_check(interaction))
        self.assertIn(
            "Kick Members",
            interaction.response.send_message.await_args.args[0],
        )
        view.stop()


class TestMemberModerationSubmission(unittest.IsolatedAsyncioTestCase):
    async def test_kick_refetches_and_rechecks_bot_hierarchy(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        target.top_role = FakeRole(50, 101, name="too-high")
        cog = KickCog(SimpleNamespace())

        with patch("cogs.mod.kick.record_case", new_callable=AsyncMock) as record_case:
            result = await cog._submit_kick(
                make_interaction(guild, moderator),
                KickRequest(target.id, "spam"),
            )

        guild.fetch_member.assert_awaited_once_with(target.id)
        self.assertFalse(result.completed)
        target.kick.assert_not_awaited()
        record_case.assert_not_awaited()

    async def test_mute_and_unmute_preserve_cases(self) -> None:
        guild, moderator, target, _, muted, _ = make_fixture()
        bot = SimpleNamespace()
        cog = MuteCog(bot)
        with patch(
            "cogs.mod.mute.record_case",
            new_callable=AsyncMock,
            side_effect=[3, 4],
        ) as record_case:
            muted_result = await cog._submit_mute(
                make_interaction(guild, moderator),
                MuteRequest(target.id, False, "spam"),
            )
            target.roles.append(muted)
            unmuted_result = await cog._submit_mute(
                make_interaction(guild, moderator),
                MuteRequest(target.id, True, "appeal"),
            )

        self.assertTrue(muted_result.completed)
        self.assertTrue(unmuted_result.completed)
        target.add_roles.assert_awaited_once()
        target.remove_roles.assert_awaited_once()
        self.assertEqual(
            [call.kwargs["action"] for call in record_case.await_args_list],
            ["mute", "unmute"],
        )

    async def test_timeout_and_untimeout_preserve_duration_and_cases(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        bot = SimpleNamespace()
        cog = TimeoutCog(bot)
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        with (
            patch("cogs.mod.timeout.discord.utils.utcnow", return_value=now),
            patch(
                "cogs.mod.timeout.record_case",
                new_callable=AsyncMock,
                side_effect=[7, 8],
            ) as record_case,
        ):
            timed = await cog._submit_timeout(
                make_interaction(guild, moderator),
                TimeoutRequest(target.id, 60, "spam"),
            )
            untimed = await cog._submit_timeout(
                make_interaction(guild, moderator),
                TimeoutRequest(target.id, None, "appeal"),
            )

        self.assertTrue(timed.completed)
        self.assertTrue(untimed.completed)
        self.assertEqual(target.timeout.await_args_list[0].args[0].hour, 1)
        self.assertIsNone(target.timeout.await_args_list[1].args[0])
        self.assertEqual(record_case.await_args_list[0].kwargs["duration_seconds"], 3600)
        self.assertNotIn("duration_seconds", record_case.await_args_list[1].kwargs)

    async def test_warn_writes_only_after_confirm_submit_and_records_case(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        database = FakeDatabase()
        bot = SimpleNamespace(db=database)
        cog = WarnCommandCog(bot)
        with patch(
            "cogs.mod.warn.record_case",
            new_callable=AsyncMock,
            return_value=9,
        ) as record_case:
            result = await cog._submit_warn(
                make_interaction(guild, moderator),
                WarnRequest(target.id, "  repeated   spam  "),
            )

        document = database["warnings"].insert_one.call_args.args[0]
        self.assertEqual(document["user_id"], target.id)
        self.assertEqual(document["reason"], "repeated spam")
        record_case.assert_awaited_once()
        self.assertTrue(result.completed)

    async def test_softban_preserves_role_snapshot_and_case(self) -> None:
        guild, moderator, target, _, _, handcuffed = make_fixture()
        database = FakeDatabase()
        bot = SimpleNamespace(db=database)
        cog = SoftbanCog(bot)
        database["old_roles"].find_one.return_value = None
        with patch(
            "cogs.mod.softban.record_case",
            new_callable=AsyncMock,
            return_value=11,
        ) as record_case:
            result = await cog._submit_softban(
                make_interaction(guild, moderator),
                SoftbanRequest(target.id, False, "spam"),
            )

        snapshot = database["old_roles"].update_one.call_args.args[1]["$setOnInsert"]
        self.assertEqual(snapshot["old_roles"], [2])
        target.edit.assert_awaited_once()
        target.add_roles.assert_awaited_once_with(
            handcuffed,
            reason="spam (Requested by moderator)",
        )
        record_case.assert_awaited_once()
        self.assertTrue(result.completed)

    async def test_softban_handcuff_failure_rolls_roles_back(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        database = FakeDatabase()
        cog = SoftbanCog(SimpleNamespace(db=database))
        database["old_roles"].find_one.return_value = None
        target.add_roles.side_effect = [make_forbidden_exception(), None]

        with patch("cogs.mod.softban.record_case", new_callable=AsyncMock) as record_case:
            result = await cog._submit_softban(
                make_interaction(guild, moderator),
                SoftbanRequest(target.id, False, "spam"),
            )

        self.assertFalse(result.completed)
        self.assertEqual(target.add_roles.await_count, 2)
        rollback_roles = target.add_roles.await_args_list[1].args
        self.assertEqual([role.id for role in rollback_roles], [2])
        database["old_roles"].delete_one.assert_called_once()
        record_case.assert_not_awaited()

    async def test_softban_retry_never_overwrites_existing_role_snapshot(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        database = FakeDatabase()
        cog = SoftbanCog(SimpleNamespace(db=database))
        database["old_roles"].find_one.return_value = {"old_roles": [2]}
        target.roles = [guild.roles[0]]
        target.add_roles.side_effect = make_forbidden_exception()

        result = await cog._submit_softban(
            make_interaction(guild, moderator),
            SoftbanRequest(target.id, False, "retry"),
        )

        self.assertFalse(result.completed)
        database["old_roles"].update_one.assert_not_called()
        database["old_roles"].delete_one.assert_not_called()

    async def test_unsoftban_restores_saved_roles_and_records_case(self) -> None:
        guild, moderator, target, _, _, handcuffed = make_fixture()
        database = FakeDatabase()
        cog = SoftbanCog(SimpleNamespace(db=database))
        database["old_roles"].find_one.return_value = {"old_roles": [2]}
        target.roles.append(handcuffed)
        with patch(
            "cogs.mod.softban.record_case",
            new_callable=AsyncMock,
            return_value=13,
        ) as record_case:
            result = await cog._submit_softban(
                make_interaction(guild, moderator),
                SoftbanRequest(target.id, True, "appeal"),
            )

        target.remove_roles.assert_awaited_once()
        self.assertEqual(target.add_roles.await_args.args[0].id, 2)
        database["old_roles"].delete_one.assert_called_once()
        record_case.assert_awaited_once()
        self.assertTrue(result.completed)

    async def test_per_target_lock_rejects_parallel_submit(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        cog = KickCog(SimpleNamespace())
        cog._active_targets.add((guild.id, target.id))

        result = await cog._submit_kick(
            make_interaction(guild, moderator),
            KickRequest(target.id, "spam"),
        )

        self.assertFalse(result.completed)
        guild.fetch_member.assert_not_awaited()

    def test_stateful_commands_use_five_second_member_cooldowns(self) -> None:
        command_objects = (
            KickCog.kick_member,
            MuteCog.mute_member,
            MuteCog.unmute_member,
            TimeoutCog.timeout,
            TimeoutCog.untimeout,
            WarnCommandCog.warn_user,
            SoftbanCog.softban_member,
            SoftbanCog.unsoftban_member,
        )
        for command in command_objects:
            with self.subTest(command=command.qualified_name):
                self.assertEqual(command._buckets._cooldown.per, 5.0)
                self.assertIs(command._buckets.type, commands.BucketType.member)


if __name__ == "__main__":
    unittest.main()
