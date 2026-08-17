import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from cogs.mod._interaction_ui import FormAnswer
from cogs.mod.role import (
    ROLE_ROLL_SELECT_CUSTOM_ID,
    ROLE_UNROLL_SELECT_CUSTOM_ID,
    RoleCopyRequest,
    RoleCopyWorkflowView,
    RoleRollView,
    RoleUnrollView,
    RollCog,
    plan_role_copy,
    role_assignment_denial,
    role_removal_denial,
    role_target_denial,
)


class FakeRole:
    def __init__(
        self,
        guild,
        role_id: int,
        position: int,
        *,
        name: str | None = None,
        default: bool = False,
        managed: bool = False,
    ) -> None:
        self.guild = guild
        self.id = role_id
        self.position = position
        self.name = name or f"role-{role_id}"
        self.mention = f"<@&{role_id}>"
        self.managed = managed
        self._default = default

    def is_default(self) -> bool:
        return self._default

    def __lt__(self, other) -> bool:
        return self.position < other.position

    def __le__(self, other) -> bool:
        return self.position <= other.position

    def __gt__(self, other) -> bool:
        return self.position > other.position

    def __ge__(self, other) -> bool:
        return self.position >= other.position


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
        self.guild_permissions = SimpleNamespace(manage_roles=manage_roles)
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


class FakeChannel:
    def __init__(self) -> None:
        self.id = 555
        self.fetch_message = AsyncMock()


def make_fixture():
    guild = FakeGuild()
    everyone = FakeRole(guild, guild.id, 0, name="@everyone", default=True)
    bot_top = FakeRole(guild, 900, 100, name="Bot")
    moderator_top = FakeRole(guild, 800, 80, name="Moderator")
    source_top = FakeRole(guild, 600, 60, name="Source top")
    target_top = FakeRole(guild, 700, 10, name="Target top")
    eligible = FakeRole(guild, 200, 20, name="Raider")
    second = FakeRole(guild, 201, 25, name="Veteran")
    existing = FakeRole(guild, 202, 15, name="Existing")
    managed = FakeRole(guild, 203, 5, name="Integration", managed=True)
    guild.roles = [
        everyone,
        managed,
        target_top,
        existing,
        eligible,
        second,
        source_top,
        moderator_top,
        bot_top,
    ]
    bot_member = FakeMember(
        guild,
        999,
        bot_top,
        name="role-bot",
        roles=[everyone, bot_top],
        bot=True,
    )
    moderator = FakeMember(
        guild,
        42,
        moderator_top,
        name="moderator",
        roles=[everyone, moderator_top],
    )
    source = FakeMember(
        guild,
        66,
        source_top,
        name="source",
        roles=[everyone, eligible, second, existing, managed, source_top],
    )
    target = FakeMember(
        guild,
        77,
        target_top,
        name="target",
        roles=[everyone, target_top, existing],
    )
    guild.me = bot_member
    guild.members = {
        bot_member.id: bot_member,
        moderator.id: moderator,
        source.id: source,
        target.id: target,
    }
    guild.fetch_member.return_value = target
    return guild, moderator, source, target, eligible, second, managed


def make_context(
    guild: FakeGuild,
    moderator: FakeMember,
    *,
    reference=None,
):
    channel = FakeChannel()
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=channel,
        message=SimpleNamespace(reference=reference),
        clean_prefix="!tf ",
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


def make_reply_reference(ctx, target: FakeMember):
    message = SimpleNamespace(
        id=123,
        guild=ctx.guild,
        channel=ctx.channel,
        author=target,
        webhook_id=None,
    )
    return SimpleNamespace(
        message_id=message.id,
        channel_id=ctx.channel.id,
        resolved=None,
        cached_message=message,
    )


def make_interaction(guild: FakeGuild, moderator: FakeMember):
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


class TestRoleSafetyHelpers(unittest.TestCase):
    def test_assignment_and_removal_validate_current_membership(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()

        self.assertIsNone(
            role_assignment_denial(guild, moderator, target, eligible)
        )
        target.roles.append(eligible)
        self.assertIn(
            "đã có role",
            role_assignment_denial(guild, moderator, target, eligible),
        )
        self.assertIsNone(role_removal_denial(guild, moderator, target, eligible))
        target.roles.remove(eligible)
        self.assertIn(
            "không có role",
            role_removal_denial(guild, moderator, target, eligible),
        )

    def test_default_managed_and_higher_roles_are_denied(self) -> None:
        guild, moderator, _, target, _, _, managed = make_fixture()
        everyone = guild.roles[0]
        above_moderator = FakeRole(guild, 300, 80, name="Too high")

        self.assertIn(
            "@everyone",
            role_assignment_denial(guild, moderator, target, everyone),
        )
        self.assertIn(
            "integration",
            role_assignment_denial(guild, moderator, target, managed),
        )
        self.assertIn(
            "role cao nhất của mình",
            role_assignment_denial(guild, moderator, target, above_moderator),
        )

    def test_target_hierarchy_is_checked_for_moderator_and_bot(self) -> None:
        guild, moderator, _, target, _, _, _ = make_fixture()
        target.top_role.position = moderator.top_role.position
        self.assertIn("ngang hoặc cao hơn", role_target_denial(guild, moderator, target))

        target.top_role.position = guild.me.top_role.position
        guild.owner_id = moderator.id
        self.assertIn("bot phải cao hơn", role_target_denial(guild, moderator, target))

    def test_plan_preserves_only_safe_missing_roles_in_source_order(self) -> None:
        guild, moderator, source, target, eligible, second, managed = make_fixture()
        existing = guild.get_role(202)

        plan = plan_role_copy(guild, moderator, source, target)

        self.assertEqual(plan.eligible, (eligible, second, source.top_role))
        self.assertEqual(plan.already_present, (existing,))
        self.assertIn(managed, plan.unmanageable)
        self.assertIn(guild.roles[0], plan.unmanageable)

    def test_guild_owner_bypasses_only_moderator_role_hierarchy(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        high = FakeRole(guild, 300, 90, name="High")
        guild.roles.append(high)
        source.roles = [high]

        self.assertEqual(
            plan_role_copy(guild, moderator, source, target).unmanageable,
            (high,),
        )
        guild.owner_id = moderator.id
        self.assertEqual(
            plan_role_copy(guild, moderator, source, target).eligible,
            (high,),
        )


class TestRoleCommandConfiguration(unittest.TestCase):
    def test_rolecopy_cooldown_and_concurrency_remain_scoped(self) -> None:
        command = RollCog.copy_roles

        self.assertTrue(command.cooldown_after_parsing)
        self.assertEqual(command._buckets._type, commands.BucketType.user)
        self.assertIsNotNone(command._max_concurrency)
        self.assertEqual(command._max_concurrency.number, 1)
        self.assertEqual(command._max_concurrency.per, commands.BucketType.guild)
        self.assertFalse(command._max_concurrency.wait)

    def test_reply_capable_parameters_are_optional(self) -> None:
        self.assertFalse(RollCog.give_role.clean_params["member"].required)
        self.assertFalse(RollCog.remove_role.clean_params["member"].required)
        self.assertFalse(RollCog.copy_roles.clean_params["source"].required)
        self.assertFalse(RollCog.copy_roles.clean_params["target"].required)


class TestRoleChangeWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_assignment_waits_for_reason_and_yes(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        self.assertEqual(view.role_select.custom_id, ROLE_ROLL_SELECT_CUSTOM_ID)
        await view.assign_role(interaction, eligible)

        target.add_roles.assert_not_awaited()
        self.assertEqual(view.step, "reason")
        await view.accept_reason(interaction, "Vi phạm nội quy")
        target.add_roles.assert_not_awaited()
        await view.confirm(interaction)

        target.add_roles.assert_awaited_once()
        self.assertIn("Vi phạm nội quy", target.add_roles.await_args.kwargs["reason"])
        self.assertTrue(view.completed)

    async def test_removal_waits_for_confirmation(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        target.roles.append(eligible)
        view = RoleUnrollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        self.assertEqual(view.role_select.custom_id, ROLE_UNROLL_SELECT_CUSTOM_ID)
        await view.remove_role(interaction, eligible)
        await view.accept_reason(interaction, "Role cleanup")

        target.remove_roles.assert_not_awaited()
        await view.confirm(interaction)
        target.remove_roles.assert_awaited_once()
        self.assertTrue(view.completed)

    async def test_hierarchy_is_rechecked_at_confirmation(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)
        await view.assign_role(interaction, eligible)
        await view.accept_reason(interaction, "Test")

        target.top_role.position = moderator.top_role.position
        await view.confirm(interaction)

        target.add_roles.assert_not_awaited()
        self.assertFalse(view.completed)
        self.assertIn(
            "ngang hoặc cao hơn",
            interaction.followup.send.await_args.args[0],
        )

    async def test_invalid_selection_does_not_advance_or_mutate(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        target.roles.append(eligible)
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        await view.assign_role(interaction, eligible)

        self.assertEqual(view.step, "field:role_id")
        target.add_roles.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()


class TestRoleCommands(unittest.IsolatedAsyncioTestCase):
    async def test_direct_and_reply_commands_open_views_without_mutation(self) -> None:
        guild, moderator, _, target, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())

        direct_ctx = make_context(guild, moderator)
        await cog.give_role.callback(cog, direct_ctx, target)
        direct_view = direct_ctx.reply.await_args.kwargs["view"]
        self.assertIsInstance(direct_view, RoleRollView)

        reply_ctx = make_context(guild, moderator)
        reply_ctx.message.reference = make_reply_reference(reply_ctx, target)
        await cog.remove_role.callback(cog, reply_ctx)
        reply_view = reply_ctx.reply.await_args.kwargs["view"]
        self.assertIsInstance(reply_view, RoleUnrollView)
        self.assertEqual(reply_view.target.id, target.id)

        target.add_roles.assert_not_awaited()
        target.remove_roles.assert_not_awaited()
        direct_view.stop()
        reply_view.stop()

    async def test_reply_with_member_argument_is_rejected(self) -> None:
        guild, moderator, _, target, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        ctx.message.reference = make_reply_reference(ctx, target)

        await cog.give_role.callback(cog, ctx, target)

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("không kèm member", ctx.reply.await_args.args[0])

    async def test_missing_argument_error_still_has_safe_usage(self) -> None:
        guild, moderator, _, _, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        await cog.give_role_error(
            ctx,
            commands.MissingRequiredArgument(RollCog.give_role.params["member"]),
        )

        self.assertIn("roleroll @user", ctx.reply.await_args.args[0])
        self.assertIn("reply", ctx.reply.await_args.args[0])


class TestRoleCopyWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_direct_command_freezes_preview_and_waits_for_yes(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.copy_roles.callback(cog, ctx, source, target)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertIsInstance(view, RoleCopyWorkflowView)
        self.assertEqual(view.step, "reason")
        self.assertEqual(
            tuple(role_id for role_id, _ in view._frozen_roles),
            (eligible.id, second.id, source.top_role.id),
        )
        target.add_roles.assert_not_awaited()

        # A role that appears after preview must never silently join the action.
        late = FakeRole(guild, 250, 30, name="Late")
        guild.roles.append(late)
        source.roles.append(late)
        interaction = make_interaction(guild, moderator)
        await view.accept_reason(interaction, "Approved copy")
        await view.confirm(interaction)

        copied_ids = [call.args[0].id for call in target.add_roles.await_args_list]
        self.assertEqual(
            copied_ids,
            [eligible.id, second.id, source.top_role.id],
        )
        self.assertNotIn(late.id, copied_ids)
        self.assertTrue(view.completed)

    async def test_reply_treats_author_as_destination_and_selects_source(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        ctx.message.reference = make_reply_reference(ctx, target)

        await cog.copy_roles.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.target.id, target.id)
        self.assertEqual(view.step, "field:source_id")
        interaction = make_interaction(guild, moderator)
        await view.accept_answer(
            interaction,
            "source_id",
            FormAnswer(source.id, str(source)),
        )

        self.assertEqual(view._frozen_source_id, source.id)
        self.assertEqual(view.step, "reason")
        target.add_roles.assert_not_awaited()

    async def test_confirmation_uses_frozen_roles_and_rechecks_live_state(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        await cog.copy_roles.callback(cog, ctx, source, target)
        view = ctx.reply.await_args.kwargs["view"]

        source.roles.remove(eligible)
        second.position = guild.me.top_role.position
        interaction = make_interaction(guild, moderator)
        await view.accept_reason(interaction, "Copy")
        await view.confirm(interaction)

        target.add_roles.assert_awaited_once()
        self.assertIs(target.add_roles.await_args.args[0], source.top_role)
        result = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("nguồn đã thay đổi", result)
        self.assertIn("không thể quản lý", result)

    async def test_partial_failure_is_terminal_and_reports_remaining_roles(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        target.add_roles.side_effect = [None, make_forbidden_exception()]
        interaction = make_interaction(guild, moderator)
        request = RoleCopyRequest(
            source_id=source.id,
            target_id=target.id,
            role_ids=(eligible.id, second.id),
            reason="Copy",
        )

        with self.assertLogs("cogs.mod.role", level="WARNING"):
            result = await cog._submit_role_copy(interaction, request)

        self.assertTrue(result.completed)
        self.assertIn("Đã sao chép **1**", result.message)
        self.assertIn("Lỗi: 1 role", result.message)

    async def test_execution_lock_rejects_a_second_confirmation(self) -> None:
        guild, moderator, source, target, eligible, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        lock = cog._role_copy_locks.setdefault(guild.id, asyncio.Lock())
        await lock.acquire()
        try:
            result = await cog._submit_role_copy(
                make_interaction(guild, moderator),
                RoleCopyRequest(
                    source_id=source.id,
                    target_id=target.id,
                    role_ids=(eligible.id,),
                    reason="Copy",
                ),
            )
        finally:
            lock.release()

        self.assertFalse(result.completed)
        self.assertIn("đang được xử lý", result.message)
        target.add_roles.assert_not_awaited()

    async def test_no_eligible_direct_copy_returns_summary_without_view(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        source.roles = [guild.roles[0], guild.get_role(202)]
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.copy_roles.callback(cog, ctx, source, target)

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("Không có role mới", ctx.reply.await_args.args[0])
        target.add_roles.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
