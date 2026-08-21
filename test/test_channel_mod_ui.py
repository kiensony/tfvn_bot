import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.mod._interaction_ui import FormAnswer
from cogs.mod.janitor import JanitorCog
from cogs.mod.purge import PURGE_USER_SCAN_LIMIT, PruneCommandCog
from cogs.mod.slowmode import SlowmodeCog


class FakeRole:
    def __init__(self, position: int) -> None:
        self.position = position

    def __gt__(self, other: "FakeRole") -> bool:
        return self.position > other.position

    def __le__(self, other: "FakeRole") -> bool:
        return self.position <= other.position


class FakeMember:
    def __init__(
        self,
        guild,
        member_id: int,
        position: int,
        *,
        name: str,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.top_role = FakeRole(position)
        self.name = name
        self.mention = f"<@{member_id}>"
        self.guild_permissions = SimpleNamespace(
            manage_messages=True,
            manage_roles=True,
        )

    def __str__(self) -> str:
        return self.name


class FakeChannel:
    def __init__(self, guild, channel_id: int = 555) -> None:
        self.guild = guild
        self.id = channel_id
        self.name = "moderation"
        self.type = discord.ChannelType.text
        self.purge = AsyncMock(return_value=[])
        self.set_permissions = AsyncMock()
        self._overwrite = discord.PermissionOverwrite()

    def permissions_for(self, _member):
        return SimpleNamespace(manage_messages=True, manage_roles=True)

    def overwrites_for(self, _member):
        return self._overwrite


class FakeThread:
    def __init__(self, guild, parent: object, channel_id: int = 556) -> None:
        self.guild = guild
        self.id = channel_id
        self.name = "discussion"
        self.type = discord.ChannelType.public_thread
        self.parent = parent


class FakeUnsupportedChannel:
    def __init__(
        self,
        guild,
        channel_type: discord.ChannelType,
        *,
        parent: object | None = None,
    ) -> None:
        self.guild = guild
        self.id = 557
        self.name = "unsupported"
        self.type = channel_type
        self.parent = parent


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.owner_id = 1_000
        self._members = {}
        self._channels = {}
        self.me = None

    def get_member(self, member_id: int):
        return self._members.get(member_id)

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


def make_fixture():
    guild = FakeGuild()
    bot_member = FakeMember(guild, 999, 100, name="bot")
    moderator = FakeMember(guild, 42, 80, name="moderator")
    target = FakeMember(guild, 77, 10, name="target")
    guild.me = bot_member
    for member in (bot_member, moderator, target):
        guild._members[member.id] = member
    channel = FakeChannel(guild)
    guild._channels[channel.id] = channel
    anchor = SimpleNamespace(id=123)
    ctx = SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=channel,
        message=anchor,
        clean_prefix="!tf ",
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )
    return guild, moderator, target, channel, anchor, ctx


def make_interaction(guild, moderator):
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


def make_http_exception(*, forbidden: bool) -> discord.HTTPException:
    status = 403 if forbidden else 500
    response = SimpleNamespace(
        status=status,
        reason="Forbidden" if forbidden else "Server Error",
    )
    payload = {
        "code": 50013 if forbidden else 0,
        "message": "Missing Permissions" if forbidden else "Server Error",
    }
    exception_type = discord.Forbidden if forbidden else discord.HTTPException
    return exception_type(response, payload)


class TestPurgeWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_purge_does_nothing_until_yes_and_uses_fixed_anchor(self) -> None:
        guild, moderator, _, channel, anchor, ctx = make_fixture()
        channel.purge.return_value = [object(), object(), object()]
        cog = PruneCommandCog(SimpleNamespace())

        await cog._open_purge_view(ctx, target=None, initial_count=3)

        channel.purge.assert_not_awaited()
        view = ctx.reply.await_args.kwargs["view"]
        view.values["count"] = FormAnswer(3, "3")
        view._show_confirm_step()
        await view.confirm(make_interaction(guild, moderator))

        channel.purge.assert_awaited_once_with(limit=3, before=anchor)
        self.assertTrue(view.completed)

    async def test_purge_user_caps_deletions_but_scans_history(self) -> None:
        guild, moderator, target, channel, anchor, ctx = make_fixture()
        other = FakeMember(guild, 88, 5, name="other")

        async def purge_side_effect(**kwargs):
            check = kwargs["check"]
            messages = [
                SimpleNamespace(author=target),
                SimpleNamespace(author=other),
                SimpleNamespace(author=target),
                SimpleNamespace(author=target),
            ]
            return [message for message in messages if check(message)]

        channel.purge.side_effect = purge_side_effect
        cog = PruneCommandCog(SimpleNamespace())
        await cog._open_purge_view(ctx, target=target, initial_count=2)
        view = ctx.reply.await_args.kwargs["view"]
        view.values["count"] = FormAnswer(2, "2")
        view._show_confirm_step()

        await view.confirm(make_interaction(guild, moderator))

        kwargs = channel.purge.await_args.kwargs
        self.assertEqual(kwargs["limit"], PURGE_USER_SCAN_LIMIT)
        self.assertIs(kwargs["before"], anchor)
        self.assertTrue(view.completed)

    async def test_purge_user_uses_stored_id_after_target_leaves(self) -> None:
        guild, moderator, target, channel, anchor, ctx = make_fixture()
        other = FakeMember(guild, 88, 5, name="other")

        async def purge_side_effect(**kwargs):
            check = kwargs["check"]
            messages = [
                SimpleNamespace(author=target),
                SimpleNamespace(author=other),
                SimpleNamespace(author=target),
            ]
            return [message for message in messages if check(message)]

        channel.purge.side_effect = purge_side_effect
        cog = PruneCommandCog(SimpleNamespace())
        await cog._open_purge_view(ctx, target=target, initial_count=2)
        view = ctx.reply.await_args.kwargs["view"]
        view.values["count"] = FormAnswer(2, "2")
        view._show_confirm_step()
        guild._members.pop(target.id)
        interaction = make_interaction(guild, moderator)

        await view.confirm(interaction)

        channel.purge.assert_awaited_once()
        self.assertTrue(view.completed)
        content = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn(f"`{target.id}`", content)
        self.assertIn("2/2", content)

    async def test_started_purge_errors_are_terminal_to_prevent_retries(self) -> None:
        for forbidden in (True, False):
            with self.subTest(forbidden=forbidden):
                guild, moderator, _, channel, _, ctx = make_fixture()
                channel.purge.side_effect = make_http_exception(
                    forbidden=forbidden
                )
                cog = PruneCommandCog(SimpleNamespace())
                await cog._open_purge_view(
                    ctx,
                    target=None,
                    initial_count=10,
                )
                view = ctx.reply.await_args.kwargs["view"]
                view.values["count"] = FormAnswer(10, "10")
                view._show_confirm_step()
                interaction = make_interaction(guild, moderator)

                await view.confirm(interaction)

                self.assertTrue(view.completed)
                self.assertTrue(view.is_finished())
                self.assertIn(
                    "có thể đã được xóa",
                    interaction.edit_original_response.await_args.kwargs[
                        "content"
                    ],
                )
                await view.confirm(make_interaction(guild, moderator))
                channel.purge.assert_awaited_once()


class TestJanitorWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_is_confirmed_and_excludes_command_message(self) -> None:
        guild, moderator, _, channel, anchor, ctx = make_fixture()
        channel.purge.return_value = [object()]
        cog = JanitorCog(SimpleNamespace())

        await cog.clean_messages_created_before.callback(cog, ctx, 30)

        channel.purge.assert_not_awaited()
        view = ctx.reply.await_args.kwargs["view"]
        view.values["days"] = FormAnswer(30, "30")
        view._show_confirm_step()
        await view.confirm(make_interaction(guild, moderator))

        kwargs = channel.purge.await_args.kwargs
        self.assertIsNone(kwargs["limit"])
        self.assertIs(kwargs["before"], anchor)
        recent = SimpleNamespace(
            created_at=discord.utils.utcnow() - timedelta(days=1)
        )
        old = SimpleNamespace(
            created_at=discord.utils.utcnow() - timedelta(days=40)
        )
        self.assertFalse(kwargs["check"](recent))
        self.assertTrue(kwargs["check"](old))


class TestSlowmodeWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_thread_uses_parent_text_channel_overwrite(self) -> None:
        guild, moderator, target, channel, _, ctx = make_fixture()
        thread = FakeThread(guild, channel)
        ctx.channel = thread
        cog = SlowmodeCog(SimpleNamespace())

        await cog._open_override_view(
            ctx,
            member=target,
            immune=True,
            initial_reason="thread exception",
        )

        view = ctx.reply.await_args.kwargs["view"]
        view.reason = "thread exception"
        view._show_confirm_step()
        await view.confirm(make_interaction(guild, moderator))

        channel.set_permissions.assert_awaited_once()
        self.assertEqual(
            view.request_builder({}, view.reason).channel_id,
            channel.id,
        )

    async def test_unsupported_channel_is_rejected_before_overwrite_access(self) -> None:
        guild, moderator, target, channel, _, ctx = make_fixture()
        ctx.channel = FakeUnsupportedChannel(guild, discord.ChannelType.voice)
        cog = SlowmodeCog(SimpleNamespace())

        await cog._open_override_view(
            ctx,
            member=target,
            immune=True,
            initial_reason=None,
        )

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("kênh text", ctx.reply.await_args.args[0])
        channel.set_permissions.assert_not_awaited()

    async def test_forum_thread_is_rejected_instead_of_using_parent_overwrites(
        self,
    ) -> None:
        guild, moderator, target, channel, _, ctx = make_fixture()
        forum = FakeUnsupportedChannel(guild, discord.ChannelType.forum)
        ctx.channel = FakeThread(guild, forum)
        cog = SlowmodeCog(SimpleNamespace())

        await cog._open_override_view(
            ctx,
            member=target,
            immune=True,
            initial_reason=None,
        )

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        channel.set_permissions.assert_not_awaited()

    async def test_confirm_preserves_other_overwrite_fields(self) -> None:
        guild, moderator, target, channel, _, ctx = make_fixture()
        channel._overwrite = discord.PermissionOverwrite(send_messages=False)
        cog = SlowmodeCog(SimpleNamespace())

        await cog._open_override_view(
            ctx,
            member=target,
            immune=True,
            initial_reason="approved exception",
        )

        channel.set_permissions.assert_not_awaited()
        view = ctx.reply.await_args.kwargs["view"]
        view.reason = "approved exception"
        view._show_confirm_step()
        await view.confirm(make_interaction(guild, moderator))

        overwrite = channel.set_permissions.await_args.kwargs["overwrite"]
        self.assertIs(overwrite.bypass_slowmode, True)
        self.assertIs(overwrite.send_messages, False)
        self.assertTrue(view.completed)

    async def test_remove_immunity_clears_only_bypass_overwrite(self) -> None:
        guild, moderator, target, channel, _, ctx = make_fixture()
        channel._overwrite = discord.PermissionOverwrite(
            bypass_slowmode=True,
            view_channel=True,
        )
        cog = SlowmodeCog(SimpleNamespace())
        await cog._open_override_view(
            ctx,
            member=target,
            immune=False,
            initial_reason=None,
        )
        view = ctx.reply.await_args.kwargs["view"]
        view.reason = "remove exception"
        view._show_confirm_step()

        await view.confirm(make_interaction(guild, moderator))

        overwrite = channel.set_permissions.await_args.kwargs["overwrite"]
        self.assertIsNone(overwrite.bypass_slowmode)
        self.assertIs(overwrite.view_channel, True)


if __name__ == "__main__":
    unittest.main()
