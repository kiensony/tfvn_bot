import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from cogs.mod._member_state import ACTIVE_ROLE_MUTATION_TARGETS
from cogs.mod.verified import (
    SELF_UNVERIFIED_CANCEL_CUSTOM_ID,
    SELF_UNVERIFIED_CONFIRM_CUSTOM_ID,
    SELF_UNVERIFIED_COOLDOWN_SECONDS,
    SELF_UNVERIFIED_TIMEOUT_SECONDS,
    SelfUnverifiedConfirmView,
    SelfUnverifiedResult,
    VerifiedCog,
    build_self_unverified_cancel_embed,
    build_self_unverified_confirm_embed,
    build_self_unverified_timeout_embed,
)


VERIFY_ROLE_ID = 200


def embed_text(embed: discord.Embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    for field in embed.fields:
        parts.extend((field.name, field.value))
    return "\n".join(parts)


class FakeRole:
    def __init__(
        self,
        guild,
        role_id: int,
        position: int,
        *,
        name: str,
        managed: bool = False,
    ) -> None:
        self.guild = guild
        self.id = role_id
        self.position = position
        self.name = name
        self.managed = managed
        self.mention = f"<@&{role_id}>"

    def __lt__(self, other) -> bool:
        return self.position < other.position

    def __gt__(self, other) -> bool:
        return self.position > other.position


class FakeMember:
    def __init__(
        self,
        guild,
        member_id: int,
        top_role: FakeRole,
        *,
        name: str,
        roles: list[FakeRole] | None = None,
        manage_roles: bool = True,
        bot: bool = False,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.top_role = top_role
        self.name = name
        self.roles = list(roles or [])
        self.guild_permissions = SimpleNamespace(
            manage_roles=manage_roles,
            administrator=False,
        )
        self.mention = f"<@{member_id}>"
        self.bot = bot
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.owner_id = 1_000
        self.me: FakeMember | None = None
        self.roles: list[FakeRole] = []
        self.members: dict[int, FakeMember] = {}
        self.fetch_member = AsyncMock()

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((role for role in self.roles if role.id == role_id), None)


def make_fixture(*, has_verify_role: bool = True, bot_can_assign: bool = True):
    guild = FakeGuild()
    everyone = FakeRole(guild, guild.id, 0, name="@everyone")
    verify_role = FakeRole(guild, VERIFY_ROLE_ID, 20, name="Fallen Femboy")
    bot_top = FakeRole(
        guild,
        900,
        100 if bot_can_assign else 10,
        name="Bot",
    )
    member_top = FakeRole(guild, 800, 30, name="Member top")
    guild.roles = [everyone, verify_role, member_top, bot_top]
    bot_member = FakeMember(
        guild,
        999,
        bot_top,
        name="verify-bot",
        roles=[everyone, bot_top],
        manage_roles=bot_can_assign,
        bot=True,
    )
    member_roles = [everyone, member_top]
    if has_verify_role:
        member_roles.append(verify_role)
    member = FakeMember(
        guild,
        42,
        member_top,
        name="member",
        roles=member_roles,
        manage_roles=False,
    )
    guild.me = bot_member
    guild.members = {bot_member.id: bot_member, member.id: member}
    guild.fetch_member.return_value = member
    bot = SimpleNamespace(
        global_vars={"FALLEN_FEMBOY_ROLE_ID": str(VERIFY_ROLE_ID)},
        user=SimpleNamespace(id=bot_member.id),
        command_prefix="!tf ",
    )
    return guild, member, verify_role, VerifiedCog(bot)


def make_context(guild: FakeGuild, member: FakeMember):
    return SimpleNamespace(
        guild=guild,
        author=member,
        clean_prefix="!tf ",
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


def make_interaction(guild: FakeGuild, member: FakeMember):
    return SimpleNamespace(
        guild=guild,
        user=member,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


class TestSelfUnverifiedEmbeds(unittest.TestCase):
    def test_confirm_embed_warns_that_staff_must_restore_access(self) -> None:
        guild, member, role, _cog = make_fixture()
        embed = build_self_unverified_confirm_embed(
            member,
            role,
            command_display="!tf self_unverified",
        )
        text = embed_text(embed)
        self.assertIn("hỏi min mót", text.lower())
        self.assertIn("!tf self_unverified", text)
        self.assertIn(str(SELF_UNVERIFIED_TIMEOUT_SECONDS), text)
        self.assertIn("gọi lại", text.lower())

    def test_cancel_and_timeout_require_running_the_command_again(self) -> None:
        cancel_text = embed_text(
            build_self_unverified_cancel_embed(
                command_display="!tf self_unverified",
            )
        )
        timeout_text = embed_text(
            build_self_unverified_timeout_embed(
                command_display="!tf self_unverified",
            )
        )
        self.assertIn("!tf self_unverified", cancel_text)
        self.assertIn("gọi lại", cancel_text.lower())
        self.assertIn("!tf self_unverified", timeout_text)
        self.assertIn("gọi lại", timeout_text.lower())


class TestVerifyRoleResolution(unittest.TestCase):
    def test_parse_and_resolve_configured_role(self) -> None:
        guild, _member, role, cog = make_fixture()
        self.assertEqual(cog._parse_verify_role_id(" 200 "), 200)
        self.assertIsNone(cog._parse_verify_role_id("not-an-id"))

        resolved, error = cog._resolve_verify_role(guild)
        self.assertIs(resolved, role)
        self.assertIsNone(error)

        cog.bot.global_vars = {}
        missing, missing_error = cog._resolve_verify_role(guild)
        self.assertIsNone(missing)
        self.assertIn("FALLEN_FEMBOY_ROLE_ID", missing_error or "")


class TestSelfUnverifiedView(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        ACTIVE_ROLE_MUTATION_TARGETS.clear()

    async def test_view_structure_and_owner_lock(self) -> None:
        view = SelfUnverifiedConfirmView(
            author_id=42,
            command_display="!tf self_unverified",
            submitter=AsyncMock(),
        )
        self.assertEqual(view.timeout, SELF_UNVERIFIED_TIMEOUT_SECONDS)
        self.assertEqual(
            [view.confirm_button, view.cancel_button],
            list(view.children),
        )
        self.assertEqual(
            view.confirm_button.custom_id,
            SELF_UNVERIFIED_CONFIRM_CUSTOM_ID,
        )
        self.assertEqual(
            view.cancel_button.custom_id,
            SELF_UNVERIFIED_CANCEL_CUSTOM_ID,
        )

        guild, owner, _role, _cog = make_fixture()
        stranger = FakeMember(
            guild,
            99,
            owner.top_role,
            name="stranger",
            roles=[],
            manage_roles=False,
        )
        self.assertTrue(await view.interaction_check(make_interaction(guild, owner)))
        stranger_interaction = make_interaction(guild, stranger)
        self.assertFalse(await view.interaction_check(stranger_interaction))
        stranger_interaction.response.send_message.assert_awaited_once()
        self.assertTrue(
            stranger_interaction.response.send_message.await_args.kwargs["ephemeral"]
        )
        view.stop()

    async def test_cancel_keeps_the_role_and_requires_a_new_request(self) -> None:
        submitter = AsyncMock()
        view = SelfUnverifiedConfirmView(
            author_id=42,
            command_display="!tf self_unverified",
            submitter=submitter,
        )
        guild, member, _role, _cog = make_fixture()
        interaction = make_interaction(guild, member)

        await view.cancel(interaction)

        submitter.assert_not_awaited()
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIn("gọi lại", embed_text(kwargs["embed"]).lower())
        self.assertIn("!tf self_unverified", embed_text(kwargs["embed"]))
        self.assertFalse(kwargs["allowed_mentions"].users)

    async def test_timeout_disables_buttons_and_asks_to_run_again(self) -> None:
        view = SelfUnverifiedConfirmView(
            author_id=42,
            command_display="!tf self_unverified",
            submitter=AsyncMock(),
        )
        message = SimpleNamespace(edit=AsyncMock())
        view.message = message

        await view.on_timeout()

        self.assertTrue(all(item.disabled for item in view.children))
        kwargs = message.edit.await_args.kwargs
        self.assertIn("gọi lại", embed_text(kwargs["embed"]).lower())
        self.assertIn("!tf self_unverified", embed_text(kwargs["embed"]))
        view.stop()

    async def test_successful_confirm_lifecycle(self) -> None:
        embed = discord.Embed(title="done")
        submitter = AsyncMock(
            return_value=SelfUnverifiedResult(True, embed=embed)
        )
        view = SelfUnverifiedConfirmView(
            author_id=42,
            command_display="!tf self_unverified",
            submitter=submitter,
        )
        guild, member, _role, _cog = make_fixture()
        interaction = make_interaction(guild, member)

        await view.confirm(interaction)

        submitter.assert_awaited_once_with(interaction)
        interaction.response.defer.assert_awaited_once_with()
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertIs(kwargs["embed"], embed)
        self.assertFalse(kwargs["allowed_mentions"].users)

    async def test_retryable_confirm_leaves_the_view_open(self) -> None:
        submitter = AsyncMock(
            return_value=SelfUnverifiedResult(False, message="Hãy thử lại.")
        )
        view = SelfUnverifiedConfirmView(
            author_id=42,
            command_display="!tf self_unverified",
            submitter=submitter,
        )
        guild, member, _role, _cog = make_fixture()
        interaction = make_interaction(guild, member)

        await view.confirm(interaction)

        self.assertFalse(view.completed)
        self.assertFalse(view.submitting)
        self.assertFalse(view.is_finished())
        self.assertTrue(all(not item.disabled for item in view.children))
        interaction.followup.send.assert_awaited_once()
        self.assertEqual(
            interaction.followup.send.await_args.args[0],
            "Hãy thử lại.",
        )
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        interaction.edit_original_response.assert_not_awaited()
        view.stop()

    async def test_second_confirm_is_rejected_after_success(self) -> None:
        submitter = AsyncMock(
            return_value=SelfUnverifiedResult(True, message="ok")
        )
        view = SelfUnverifiedConfirmView(
            author_id=42,
            command_display="!tf self_unverified",
            submitter=submitter,
        )
        guild, member, _role, _cog = make_fixture()
        first = make_interaction(guild, member)
        second = make_interaction(guild, member)

        await view.confirm(first)
        await view.confirm(second)

        submitter.assert_awaited_once()
        second.response.send_message.assert_awaited_once()
        self.assertIn(
            "gọi lại",
            second.response.send_message.await_args.args[0].lower(),
        )


class TestSelfUnverifiedCommand(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        ACTIVE_ROLE_MUTATION_TARGETS.clear()

    def test_command_is_guild_only_with_member_cooldown(self) -> None:
        command = VerifiedCog.self_unverified
        self.assertEqual(command.name, "self_unverified")
        self.assertTrue(
            any("guild_only" in getattr(check, "__qualname__", "") for check in command.checks)
        )
        cooldown = command.cooldown
        self.assertIsNotNone(cooldown)
        self.assertEqual(cooldown.rate, 1)
        self.assertEqual(cooldown.per, SELF_UNVERIFIED_COOLDOWN_SECONDS)
        self.assertEqual(command._buckets.type, commands.BucketType.member)

    async def test_open_requires_the_verified_role(self) -> None:
        guild, member, role, cog = make_fixture(has_verify_role=False)
        ctx = make_context(guild, member)

        await cog._open_self_unverified(ctx, member)

        ctx.reply.assert_awaited_once()
        message = ctx.reply.await_args.args[0]
        self.assertIn(role.name, message)
        self.assertIn("hỏi min mót", message.lower())
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_open_sends_confirmation_when_the_member_is_verified(self) -> None:
        guild, member, role, cog = make_fixture()
        ctx = make_context(guild, member)

        await cog._open_self_unverified(ctx, member)

        kwargs = ctx.reply.await_args.kwargs
        self.assertIsInstance(kwargs["view"], SelfUnverifiedConfirmView)
        self.assertIs(kwargs["view"].message, ctx.reply.return_value)
        self.assertIn(role.name, embed_text(kwargs["embed"]))
        self.assertIn("hỏi min mót", embed_text(kwargs["embed"]).lower())
        self.assertFalse(kwargs["mention_author"])
        kwargs["view"].stop()

    async def test_open_rejects_when_bot_cannot_remove_the_role(self) -> None:
        guild, member, role, cog = make_fixture(bot_can_assign=False)
        ctx = make_context(guild, member)

        await cog._open_self_unverified(ctx, member)

        message = ctx.reply.await_args.args[0]
        self.assertIn(role.name, message)
        self.assertIn("min mót", message.lower())
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_confirm_removes_the_role_and_requires_staff_to_restore(self) -> None:
        guild, member, role, cog = make_fixture()
        interaction = make_interaction(guild, member)

        result = await cog._submit_self_unverified(interaction)

        member.remove_roles.assert_awaited_once()
        reason = member.remove_roles.await_args.kwargs["reason"]
        self.assertIn("Self unverified", reason)
        self.assertTrue(result.completed)
        self.assertIsNotNone(result.embed)
        text = embed_text(result.embed)
        self.assertIn("hỏi min mót", text.lower())
        self.assertIn("không tự lấy", text.lower())

    async def test_confirm_without_the_role_completes_and_points_to_staff(self) -> None:
        guild, member, role, cog = make_fixture(has_verify_role=False)
        interaction = make_interaction(guild, member)

        result = await cog._submit_self_unverified(interaction)

        member.remove_roles.assert_not_awaited()
        self.assertTrue(result.completed)
        text = embed_text(result.embed)
        self.assertIn(role.name, text)
        self.assertIn("hỏi min mót", text.lower())

    async def test_confirm_waits_when_another_role_mutation_is_running(self) -> None:
        guild, member, _role, cog = make_fixture()
        key = (guild.id, member.id)
        ACTIVE_ROLE_MUTATION_TARGETS.add(key)
        interaction = make_interaction(guild, member)
        try:
            result = await cog._submit_self_unverified(interaction)
        finally:
            ACTIVE_ROLE_MUTATION_TARGETS.discard(key)

        member.remove_roles.assert_not_awaited()
        self.assertFalse(result.completed)
        self.assertIn("thử lại", (result.message or "").lower())

    async def test_cooldown_error_tells_the_member_to_wait(self) -> None:
        guild, member, _role, cog = make_fixture()
        ctx = make_context(guild, member)
        error = commands.CommandOnCooldown(
            commands.Cooldown(1, SELF_UNVERIFIED_COOLDOWN_SECONDS),
            12.3,
            commands.BucketType.member,
        )

        await cog.self_unverified_error(ctx, error)

        ctx.reply.assert_awaited_once()
        self.assertIn("12.3", ctx.reply.await_args.args[0])
        self.assertFalse(ctx.reply.await_args.kwargs["mention_author"])


if __name__ == "__main__":
    unittest.main()
