import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord

from cogs.mod.area_51_guard import (
    AREA_51_CHANNEL_ID_VARIABLE,
    Area51CancelBanView,
    Area51GuardCog,
)


def make_cog(bot) -> Area51GuardCog:
    cog = object.__new__(Area51GuardCog)
    cog.bot = bot
    cog.logger = logging.getLogger("test.area_51_guard")
    cog._pending_bans = set()
    return cog


def make_interaction(guild, user):
    return SimpleNamespace(
        guild=guild,
        user=user,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


class TestArea51CancelBanView(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_requires_same_guild_live_administrator(self) -> None:
        guild = SimpleNamespace(id=10)
        target = SimpleNamespace(
            id=77,
            name="intruder",
            mention="<@77>",
            guild=guild,
        )
        view = Area51CancelBanView(target)

        administrator = SimpleNamespace(
            id=1,
            name="admin",
            mention="<@1>",
            guild_permissions=SimpleNamespace(administrator=True),
        )
        wrong_guild = make_interaction(SimpleNamespace(id=11), administrator)
        self.assertFalse(await view.interaction_check(wrong_guild))
        wrong_guild.response.send_message.assert_awaited_once()

        moderator = SimpleNamespace(
            id=2,
            name="moderator",
            mention="<@2>",
            guild_permissions=SimpleNamespace(administrator=False),
        )
        missing_permission = make_interaction(guild, moderator)
        self.assertFalse(await view.interaction_check(missing_permission))
        missing_permission.response.send_message.assert_awaited_once()

        allowed = make_interaction(guild, administrator)
        self.assertTrue(await view.interaction_check(allowed))
        administrator.guild_permissions.administrator = False
        self.assertFalse(await view.interaction_check(allowed))


class TestArea51FireWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_configured_channel_is_rejected(self) -> None:
        invoking_guild = SimpleNamespace(id=10)
        foreign_channel = SimpleNamespace(
            id=555,
            name="area-51",
            guild=SimpleNamespace(id=20),
        )
        bot = SimpleNamespace(
            global_vars={AREA_51_CHANNEL_ID_VARIABLE: "555"},
            get_channel=Mock(return_value=None),
            fetch_channel=AsyncMock(return_value=foreign_channel),
        )
        cog = make_cog(bot)
        ctx = SimpleNamespace(
            guild=invoking_guild,
            author=SimpleNamespace(
                id=1,
                guild_permissions=SimpleNamespace(administrator=True),
            ),
            reply=AsyncMock(),
        )

        await Area51GuardCog.area_51_fire.callback(cog, ctx)

        bot.fetch_channel.assert_awaited_once_with(555)
        self.assertIn("không thuộc server này", ctx.reply.await_args.args[0])

    async def test_same_guild_alert_waits_for_confirmation(self) -> None:
        guild = SimpleNamespace(id=10)
        channel = SimpleNamespace(id=555, name="area-51", guild=guild)
        bot = SimpleNamespace(
            global_vars={AREA_51_CHANNEL_ID_VARIABLE: "555"},
            get_channel=Mock(return_value=channel),
            fetch_channel=AsyncMock(),
        )
        cog = make_cog(bot)
        cog._send_area_51_reminder_now = AsyncMock(return_value=True)
        moderator = SimpleNamespace(
            id=1,
            guild_permissions=SimpleNamespace(administrator=True),
        )
        sent_message = SimpleNamespace(edit=AsyncMock())
        ctx = SimpleNamespace(
            guild=guild,
            author=moderator,
            reply=AsyncMock(return_value=sent_message),
        )

        await Area51GuardCog.area_51_fire.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        cog._send_area_51_reminder_now.assert_not_awaited()
        interaction = make_interaction(guild, moderator)
        self.assertTrue(await view.interaction_check(interaction))
        await view.confirm(interaction)
        cog._send_area_51_reminder_now.assert_awaited_once_with()


class TestArea51PendingState(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_can_be_pending_independently_per_guild(self) -> None:
        bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            global_vars={AREA_51_CHANNEL_ID_VARIABLE: "101"},
        )
        cog = make_cog(bot)
        cog._delete_trigger_message = AsyncMock()
        cog._send_cancel_prompt = AsyncMock(return_value=None)
        cog._can_guard_ban = Mock(return_value=True)
        cog._prune_seconds = Mock(return_value=0)
        release_wait = asyncio.Event()

        async def wait_for_timeout(_view) -> bool:
            await release_wait.wait()
            return True

        def make_message(guild_id: int, channel_id: int):
            guild = SimpleNamespace(id=guild_id)
            member = MagicMock(spec=discord.Member)
            member.id = 77
            member.name = "intruder"
            member.mention = "<@77>"
            member.guild = guild
            member.ban = AsyncMock()
            channel = SimpleNamespace(
                id=channel_id,
                send=AsyncMock(),
            )
            return SimpleNamespace(
                guild=guild,
                author=member,
                channel=channel,
            )

        first = make_message(10, 101)
        second = make_message(20, 202)

        with patch.object(Area51CancelBanView, "wait", wait_for_timeout):
            first_task = asyncio.create_task(cog.guard_area_51(first))
            await asyncio.sleep(0)
            self.assertIn((10, 77), cog._pending_bans)

            bot.global_vars[AREA_51_CHANNEL_ID_VARIABLE] = "202"
            second_task = asyncio.create_task(cog.guard_area_51(second))
            await asyncio.sleep(0)
            try:
                self.assertEqual(
                    cog._pending_bans,
                    {(10, 77), (20, 77)},
                )
            finally:
                release_wait.set()
                await asyncio.gather(first_task, second_task)

        first.author.ban.assert_awaited_once()
        second.author.ban.assert_awaited_once()
        self.assertEqual(cog._pending_bans, set())


if __name__ == "__main__":
    unittest.main()
