import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from cogs.mod.role import (
    ROLE_ROLL_SELECT_CUSTOM_ID,
    ROLE_UNROLL_SELECT_CUSTOM_ID,
    RoleChangeView,
    RoleRollView,
    RoleUnrollView,
    RollCog,
    role_assignment_denial,
    role_removal_denial,
)


class FakeRole:
    def __init__(
        self,
        guild,
        role_id: int,
        position: int,
        *,
        default: bool = False,
        managed: bool = False,
    ) -> None:
        self.guild = guild
        self.id = role_id
        self.position = position
        self.managed = managed
        self.mention = f"<@&{role_id}>"
        self._default = default

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other) -> bool:
        return self.position >= other.position


class FakeMember:
    def __init__(
        self,
        member_id: int,
        top_role: FakeRole,
        *,
        manage_roles: bool = True,
        roles: list[FakeRole] | None = None,
        name: str | None = None,
    ) -> None:
        self.id = member_id
        self.top_role = top_role
        self.guild_permissions = SimpleNamespace(manage_roles=manage_roles)
        self.roles = [] if roles is None else roles
        self.mention = f"<@{member_id}>"
        self.name = name or f"member-{member_id}"
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self, guild_id: int = 10, owner_id: int = 999) -> None:
        self.id = guild_id
        self.owner_id = owner_id
        self.me = None
        self._members = {}

    def get_member(self, member_id: int):
        return self._members.get(member_id)


def make_role_context():
    guild = FakeGuild()
    bot_top_role = FakeRole(guild, 500, 100)
    moderator_top_role = FakeRole(guild, 400, 80)
    target_top_role = FakeRole(guild, 300, 10)
    guild.me = FakeMember(50, bot_top_role, name="role-bot")
    moderator = FakeMember(42, moderator_top_role, name="moderator")
    target = FakeMember(77, target_top_role, name="target")
    guild._members[target.id] = target
    role = FakeRole(guild, 200, 20)
    return guild, moderator, target, role


def make_interaction(guild, user):
    return SimpleNamespace(
        guild=guild,
        user=user,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def make_http_exception() -> discord.HTTPException:
    response = SimpleNamespace(status=500, reason="Test failure")
    return discord.HTTPException(
        response,
        {"code": 0, "message": "deterministic test failure"},
    )


def make_forbidden_exception() -> discord.Forbidden:
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(
        response,
        {"code": 50013, "message": "Missing Permissions"},
    )


class TestRoleAssignmentDenial(unittest.TestCase):
    def test_valid_role_is_allowed(self) -> None:
        guild, moderator, target, role = make_role_context()

        self.assertIsNone(
            role_assignment_denial(guild, moderator, target, role)
        )

    def test_default_role_is_denied(self) -> None:
        guild, moderator, target, _ = make_role_context()
        role = FakeRole(guild, guild.id, 0, default=True)

        self.assertEqual(
            role_assignment_denial(guild, moderator, target, role),
            "Không thể gán role mặc định `@everyone`.",
        )

    def test_managed_role_is_denied(self) -> None:
        guild, moderator, target, _ = make_role_context()
        role = FakeRole(guild, 201, 20, managed=True)

        self.assertEqual(
            role_assignment_denial(guild, moderator, target, role),
            "Role này do Discord hoặc integration quản lý nên không thể gán thủ công.",
        )

    def test_missing_bot_permission_is_denied(self) -> None:
        guild, moderator, target, role = make_role_context()
        guild.me.guild_permissions.manage_roles = False

        self.assertEqual(
            role_assignment_denial(guild, moderator, target, role),
            "Bot không có quyền Manage Roles để gán role.",
        )

    def test_bot_hierarchy_is_enforced(self) -> None:
        guild, moderator, target, _ = make_role_context()
        role = FakeRole(guild, 201, guild.me.top_role.position)

        self.assertEqual(
            role_assignment_denial(guild, moderator, target, role),
            "Role đã chọn phải thấp hơn role cao nhất của bot.",
        )

    def test_moderator_hierarchy_is_enforced(self) -> None:
        guild, moderator, target, _ = make_role_context()
        role = FakeRole(guild, 201, moderator.top_role.position)

        self.assertEqual(
            role_assignment_denial(guild, moderator, target, role),
            "Bạn chỉ có thể gán role thấp hơn role cao nhất của mình.",
        )

    def test_duplicate_role_is_denied(self) -> None:
        guild, moderator, target, role = make_role_context()
        target.roles.append(role)

        self.assertEqual(
            role_assignment_denial(guild, moderator, target, role),
            f"{target.mention} đã có role {role.mention} rồi.",
        )


class TestRoleRemovalDenial(unittest.TestCase):
    def test_role_held_by_target_is_allowed(self) -> None:
        guild, moderator, target, role = make_role_context()
        target.roles.append(role)

        self.assertIsNone(
            role_removal_denial(guild, moderator, target, role)
        )

    def test_role_not_held_by_target_is_denied(self) -> None:
        guild, moderator, target, role = make_role_context()

        self.assertEqual(
            role_removal_denial(guild, moderator, target, role),
            f"{target.mention} không có role {role.mention}.",
        )


class TestRoleRollCommand(unittest.IsolatedAsyncioTestCase):
    async def test_command_sends_native_role_select_and_stores_message(
        self,
    ) -> None:
        guild, moderator, target, _ = make_role_context()
        sent_message = SimpleNamespace(id=123, edit=AsyncMock())
        ctx = SimpleNamespace(
            guild=guild,
            author=moderator,
            reply=AsyncMock(return_value=sent_message),
        )
        cog = RollCog(SimpleNamespace())

        await cog.give_role.callback(cog, ctx, target)

        ctx.reply.assert_awaited_once()
        args = ctx.reply.await_args.args
        kwargs = ctx.reply.await_args.kwargs
        self.assertEqual(
            args[0],
            f"Chọn role muốn gán cho {target.mention}:",
        )
        view = kwargs["view"]
        self.assertIsInstance(view, RoleRollView)
        self.assertEqual(view.author_id, moderator.id)
        self.assertEqual(view.target_id, target.id)
        self.assertIs(view.message, sent_message)
        self.assertEqual(len(view.children), 1)
        self.assertIsInstance(view.children[0], discord.ui.RoleSelect)
        self.assertIs(view.children[0], view.role_select)
        self.assertEqual(view.role_select.custom_id, ROLE_ROLL_SELECT_CUSTOM_ID)
        self.assertEqual(view.role_select.min_values, 1)
        self.assertEqual(view.role_select.max_values, 1)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        self.assertFalse(kwargs["mention_author"])
        view.stop()

    async def test_error_handler_reports_missing_permissions(self) -> None:
        cog = RollCog(SimpleNamespace())
        ctx = SimpleNamespace(reply=AsyncMock())

        await cog.give_role_error(
            ctx,
            commands.MissingPermissions(["manage_roles"]),
        )

        ctx.reply.assert_awaited_once_with(
            "Bạn không có quyền Manage Roles để gán role.",
            mention_author=False,
        )

    async def test_error_handler_shows_usage_when_member_is_missing(self) -> None:
        cog = RollCog(SimpleNamespace())
        ctx = SimpleNamespace(clean_prefix="!tf ", reply=AsyncMock())
        parameter = commands.Parameter(
            "member",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )

        await cog.give_role_error(
            ctx,
            commands.MissingRequiredArgument(parameter),
        )

        ctx.reply.assert_awaited_once_with(
            "Cách dùng: `!tf roleroll @user`",
            mention_author=False,
        )


class TestRoleUnrollCommand(unittest.IsolatedAsyncioTestCase):
    async def test_command_sends_native_role_select_and_stores_message(
        self,
    ) -> None:
        guild, moderator, target, _ = make_role_context()
        sent_message = SimpleNamespace(id=124, edit=AsyncMock())
        ctx = SimpleNamespace(
            guild=guild,
            author=moderator,
            reply=AsyncMock(return_value=sent_message),
        )
        cog = RollCog(SimpleNamespace())

        await cog.remove_role.callback(cog, ctx, target)

        ctx.reply.assert_awaited_once()
        args = ctx.reply.await_args.args
        kwargs = ctx.reply.await_args.kwargs
        self.assertEqual(
            args[0],
            f"Chọn role muốn gỡ khỏi {target.mention}:",
        )
        view = kwargs["view"]
        self.assertIsInstance(view, RoleUnrollView)
        self.assertIsInstance(view, RoleChangeView)
        self.assertTrue(view.remove)
        self.assertEqual(view.author_id, moderator.id)
        self.assertEqual(view.target_id, target.id)
        self.assertIs(view.message, sent_message)
        self.assertEqual(len(view.children), 1)
        self.assertIsInstance(view.role_select, discord.ui.RoleSelect)
        self.assertIs(view.children[0], view.role_select)
        self.assertEqual(
            view.role_select.custom_id,
            ROLE_UNROLL_SELECT_CUSTOM_ID,
        )
        self.assertEqual(view.role_select.min_values, 1)
        self.assertEqual(view.role_select.max_values, 1)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        self.assertFalse(kwargs["mention_author"])
        view.stop()

    async def test_error_handler_reports_missing_permissions(self) -> None:
        cog = RollCog(SimpleNamespace())
        ctx = SimpleNamespace(reply=AsyncMock())

        await cog.remove_role_error(
            ctx,
            commands.MissingPermissions(["manage_roles"]),
        )

        ctx.reply.assert_awaited_once_with(
            "Bạn không có quyền Manage Roles để gỡ role.",
            mention_author=False,
        )

    async def test_error_handler_shows_usage_when_member_is_missing(self) -> None:
        cog = RollCog(SimpleNamespace())
        ctx = SimpleNamespace(clean_prefix="!tf ", reply=AsyncMock())
        parameter = commands.Parameter(
            "member",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )

        await cog.remove_role_error(
            ctx,
            commands.MissingRequiredArgument(parameter),
        )

        ctx.reply.assert_awaited_once_with(
            "Cách dùng: `!tf roleunroll @user`",
            mention_author=False,
        )


class TestRoleRollView(unittest.IsolatedAsyncioTestCase):
    async def test_only_command_author_can_interact_and_permission_is_rechecked(
        self,
    ) -> None:
        guild, moderator, target, _ = make_role_context()
        view = RoleRollView(author_id=moderator.id, target=target)
        author_interaction = make_interaction(guild, moderator)

        self.assertTrue(await view.interaction_check(author_interaction))
        author_interaction.response.send_message.assert_not_awaited()

        stranger = FakeMember(43, moderator.top_role, name="stranger")
        stranger_interaction = make_interaction(guild, stranger)

        self.assertFalse(await view.interaction_check(stranger_interaction))
        stranger_interaction.response.send_message.assert_awaited_once_with(
            "Chỉ người đã gọi lệnh roleroll mới có thể chọn role.",
            ephemeral=True,
        )

        moderator.guild_permissions.manage_roles = False
        lost_permission = make_interaction(guild, moderator)
        self.assertFalse(await view.interaction_check(lost_permission))
        lost_permission.response.send_message.assert_awaited_once_with(
            "Bạn không còn quyền Manage Roles để dùng menu này.",
            ephemeral=True,
        )
        view.stop()

    async def test_successful_assignment_finishes_and_disables_view(self) -> None:
        guild, moderator, target, role = make_role_context()
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        await view.assign_role(interaction, role)

        interaction.response.defer.assert_awaited_once_with()
        target.add_roles.assert_awaited_once_with(
            role,
            reason=f"roleroll by moderator {moderator.id}",
        )
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        interaction.edit_original_response.assert_awaited_once()
        kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertEqual(
            kwargs["content"],
            f"Đã gán {role.mention} cho {target.mention} thành công!",
        )
        self.assertIs(kwargs["view"], view)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_failed_defer_releases_completion_lock(self) -> None:
        guild, moderator, target, role = make_role_context()
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)
        interaction.response.defer.side_effect = make_http_exception()

        with self.assertRaises(discord.HTTPException):
            await view.assign_role(interaction, role)

        interaction.response.defer.assert_awaited_once_with()
        self.assertFalse(view.completed)
        self.assertFalse(view.is_finished())
        self.assertTrue(all(not item.disabled for item in view.children))
        target.add_roles.assert_not_awaited()
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()
        view.stop()

    async def test_failed_success_edit_stops_view_and_sends_followup(self) -> None:
        guild, moderator, target, role = make_role_context()
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)
        interaction.edit_original_response.side_effect = make_http_exception()

        with self.assertLogs("cogs.mod.role", level="ERROR") as captured:
            await view.assign_role(interaction, role)

        target.add_roles.assert_awaited_once_with(
            role,
            reason=f"roleroll by moderator {moderator.id}",
        )
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_awaited_once_with(
            "Đã gán role thành công nhưng không thể cập nhật menu.",
            ephemeral=True,
        )
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        self.assertTrue(
            any(
                "Could not update roleroll message" in message
                for message in captured.output
            )
        )

    async def test_duplicate_role_keeps_view_active(self) -> None:
        guild, moderator, target, role = make_role_context()
        target.roles.append(role)
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        await view.assign_role(interaction, role)

        interaction.response.send_message.assert_awaited_once()
        args = interaction.response.send_message.await_args.args
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertEqual(
            args[0],
            f"{target.mention} đã có role {role.mention} rồi.",
        )
        self.assertTrue(kwargs["ephemeral"])
        self.assertFalse(view.completed)
        self.assertFalse(view.is_finished())
        self.assertTrue(all(not item.disabled for item in view.children))
        target.add_roles.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()
        interaction.edit_original_response.assert_not_awaited()
        view.stop()

    async def test_timeout_disables_view_and_edits_stored_message(self) -> None:
        _, moderator, target, _ = make_role_context()
        view = RoleRollView(author_id=moderator.id, target=target)
        message = SimpleNamespace(edit=AsyncMock())
        view.message = message

        await view.on_timeout()

        self.assertTrue(all(item.disabled for item in view.children))
        message.edit.assert_awaited_once_with(view=view)
        view.stop()


class TestRoleUnrollView(unittest.IsolatedAsyncioTestCase):
    async def test_role_select_callback_removes_role_and_finishes_view(
        self,
    ) -> None:
        guild, moderator, target, role = make_role_context()
        target.roles.append(role)
        view = RoleUnrollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)
        view.role_select._values = [role]

        await view.role_select.callback(interaction)

        interaction.response.defer.assert_awaited_once_with()
        target.remove_roles.assert_awaited_once_with(
            role,
            reason=f"roleunroll by moderator {moderator.id}",
        )
        target.add_roles.assert_not_awaited()
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        interaction.edit_original_response.assert_awaited_once()
        kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertEqual(
            kwargs["content"],
            f"Đã gỡ {role.mention} khỏi {target.mention} thành công!",
        )
        self.assertIs(kwargs["view"], view)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_unowned_role_keeps_view_retryable(self) -> None:
        guild, moderator, target, role = make_role_context()
        view = RoleUnrollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        await view.remove_role(interaction, role)

        interaction.response.send_message.assert_awaited_once()
        args = interaction.response.send_message.await_args.args
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertEqual(
            args[0],
            f"{target.mention} không có role {role.mention}.",
        )
        self.assertTrue(kwargs["ephemeral"])
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        self.assertFalse(view.completed)
        self.assertFalse(view.is_finished())
        self.assertTrue(all(not item.disabled for item in view.children))
        target.remove_roles.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()
        interaction.edit_original_response.assert_not_awaited()
        view.stop()

    async def test_forbidden_removal_releases_completion_lock(self) -> None:
        guild, moderator, target, role = make_role_context()
        target.roles.append(role)
        target.remove_roles.side_effect = make_forbidden_exception()
        view = RoleUnrollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        await view.remove_role(interaction, role)

        interaction.response.defer.assert_awaited_once_with()
        target.remove_roles.assert_awaited_once_with(
            role,
            reason=f"roleunroll by moderator {moderator.id}",
        )
        interaction.followup.send.assert_awaited_once_with(
            "Bot không thể gỡ role này. Hãy kiểm tra quyền và thứ bậc role.",
            ephemeral=True,
        )
        self.assertFalse(view.completed)
        self.assertFalse(view.is_finished())
        self.assertTrue(all(not item.disabled for item in view.children))
        interaction.edit_original_response.assert_not_awaited()
        view.stop()

    async def test_http_removal_failure_releases_completion_lock(self) -> None:
        guild, moderator, target, role = make_role_context()
        target.roles.append(role)
        target.remove_roles.side_effect = make_http_exception()
        view = RoleUnrollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        with self.assertLogs("cogs.mod.role", level="ERROR") as captured:
            await view.remove_role(interaction, role)

        interaction.response.defer.assert_awaited_once_with()
        target.remove_roles.assert_awaited_once_with(
            role,
            reason=f"roleunroll by moderator {moderator.id}",
        )
        interaction.followup.send.assert_awaited_once_with(
            "Discord từ chối cập nhật role. Vui lòng thử lại.",
            ephemeral=True,
        )
        self.assertFalse(view.completed)
        self.assertFalse(view.is_finished())
        self.assertTrue(all(not item.disabled for item in view.children))
        interaction.edit_original_response.assert_not_awaited()
        self.assertTrue(
            any(
                "Discord rejected roleunroll" in message
                for message in captured.output
            )
        )
        view.stop()


if __name__ == "__main__":
    unittest.main()
