import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.mod._member_state import ACTIVE_ROLE_MUTATION_TARGETS
from cogs.onboarding._role_exam_helpers import (
    RoleExamChoice,
    RoleExamConfig,
    RoleExamQuestion,
)
from cogs.onboarding.role_exam import (
    ExamFinalizeResult,
    RoleExamCog,
    RoleExamInvitationView,
    RoleExamView,
    build_invitation_embed,
    role_assignment_denial,
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
        permissions=None,
    ) -> None:
        self.guild = guild
        self.id = role_id
        self.position = position
        self.name = name or f"role-{role_id}"
        self.mention = f"<@&{role_id}>"
        self.managed = managed
        self.permissions = permissions or SimpleNamespace()
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
        self.mention = f"<@{member_id}>"
        self.roles = list(roles or [])
        self.bot = bot
        self.guild_permissions = SimpleNamespace(manage_roles=manage_roles)
        self.add_roles = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.owner_id = 1_000
        self.me: FakeMember | None = None
        self.roles: list[FakeRole] = []
        self.members: dict[int, FakeMember] = {}
        self.fetch_member = AsyncMock(side_effect=self._fetch_member)

    def _fetch_member(self, member_id: int):
        return self.members.get(member_id)

    def get_role(self, role_id: int):
        return next((role for role in self.roles if role.id == role_id), None)


def make_config(
    *,
    role_id: int | None = 500,
    required_percent: int = 50,
    question_count: int = 2,
) -> RoleExamConfig:
    questions = tuple(
        RoleExamQuestion(
            id=f"q{number:02d}",
            prompt=f"Nội dung câu hỏi {number}",
            choices=(
                RoleExamChoice(id="a", text="Đáp án A"),
                RoleExamChoice(id="b", text="Đáp án B"),
                RoleExamChoice(id="c", text="Đáp án C"),
                RoleExamChoice(id="d", text="Đáp án D"),
            ),
            correct_choice_id="a",
        )
        for number in range(1, question_count + 1)
    )
    return RoleExamConfig(
        schema_version=1,
        title="Bài kiểm tra nhận role",
        instructions="Trả lời đủ câu hỏi rồi nộp bài.",
        role_id=role_id,
        required_percent=required_percent,
        questions=questions,
    )


def make_fixture():
    guild = FakeGuild()
    everyone = FakeRole(guild, guild.id, 0, name="@everyone", default=True)
    target_top = FakeRole(guild, 700, 10, name="Target")
    reward = FakeRole(guild, 500, 20, name="Exam passed")
    moderator_top = FakeRole(guild, 800, 80, name="Moderator")
    bot_top = FakeRole(guild, 900, 100, name="Bot")
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
    target = FakeMember(
        guild,
        77,
        target_top,
        name="target",
        roles=[everyone, target_top],
    )
    guild.me = bot_member
    guild.roles = [everyone, target_top, reward, moderator_top, bot_top]
    guild.members = {
        bot_member.id: bot_member,
        moderator.id: moderator,
        target.id: target,
    }
    bot = SimpleNamespace(get_guild=Mock(return_value=guild))
    return guild, bot, moderator, target, reward


def make_cog(bot, config: RoleExamConfig) -> RoleExamCog:
    cog = object.__new__(RoleExamCog)
    cog.bot = bot
    cog.config = config
    cog.config_error = None
    cog.active_sessions = {}
    cog._starting_sessions = set()
    cog._cleanup_tasks = set()
    cog._unloading = False
    return cog


def make_interaction(user, *, private_message=None):
    return SimpleNamespace(
        user=user,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        original_response=AsyncMock(
            return_value=private_message
            or SimpleNamespace(edit=AsyncMock()),
        ),
    )


def make_context(guild, moderator, *, public_message=None):
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        clean_prefix="!tf ",
        reply=AsyncMock(
            return_value=public_message
            or SimpleNamespace(edit=AsyncMock()),
        ),
        send=AsyncMock(),
    )


def make_invitation(cog, config, moderator, target, *, public_message=None):
    key = (target.guild.id, target.id)
    view = RoleExamInvitationView(
        cog,
        config=config,
        guild_id=target.guild.id,
        moderator_id=moderator.id,
        target_id=target.id,
        target_mention=target.mention,
        session_key=key,
    )
    view.message = public_message or SimpleNamespace(edit=AsyncMock())
    cog.active_sessions[key] = view
    return view


def make_exam(cog, config, moderator, target, *, public_message=None):
    key = (target.guild.id, target.id)
    view = RoleExamView(
        cog,
        config=config,
        questions=config.questions,
        guild_id=target.guild.id,
        moderator_id=moderator.id,
        target_id=target.id,
        target_mention=target.mention,
        session_key=key,
        public_message=public_message or SimpleNamespace(edit=AsyncMock()),
    )
    view.message = SimpleNamespace(edit=AsyncMock())
    cog.active_sessions[key] = view
    return view


def make_http_exception(status: int = 500) -> discord.HTTPException:
    response = SimpleNamespace(
        status=status,
        reason="test failure",
        headers={},
    )
    if status == 403:
        return discord.Forbidden(response, "forbidden")
    return discord.HTTPException(response, "temporary failure")


class TestRoleExamInvitation(unittest.IsolatedAsyncioTestCase):
    async def test_public_invitation_hides_threshold_from_channel(self) -> None:
        config = make_config(required_percent=80)

        invitation = build_invitation_embed(config, "<@77>")
        rendered = str(invitation.to_dict())

        self.assertNotIn("80%", rendered)
        self.assertNotIn("2/2", rendered)

    async def test_only_target_can_start_and_exam_is_ephemeral(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        public_message = SimpleNamespace(edit=AsyncMock())
        invitation = make_invitation(
            cog,
            config,
            moderator,
            target,
            public_message=public_message,
        )

        outsider = FakeMember(
            guild,
            88,
            target.top_role,
            name="outsider",
            roles=list(target.roles),
        )
        denied = make_interaction(outsider)
        self.assertFalse(await invitation.interaction_check(denied))
        denied.response.send_message.assert_awaited_once()
        self.assertTrue(denied.response.send_message.await_args.kwargs["ephemeral"])

        private_message = SimpleNamespace(edit=AsyncMock())
        interaction = make_interaction(target, private_message=private_message)
        with patch(
            "cogs.onboarding.role_exam.shuffled_questions",
            return_value=config.questions,
        ):
            await invitation.start_exam(interaction)

        send_kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(send_kwargs["ephemeral"])
        self.assertIsInstance(send_kwargs["view"], RoleExamView)
        exam = send_kwargs["view"]
        self.assertIn("50%", exam.build_embed().footer.text)
        self.assertIs(cog.active_sessions[(guild.id, target.id)], exam)
        self.assertIs(exam.message, private_message)
        self.assertTrue(invitation.completed)
        self.assertTrue(invitation.start_button.disabled)
        public_embed = public_message.edit.await_args.kwargs["embed"]
        self.assertIn("Đang làm bài", public_embed.title)
        self.assertNotIn("%", public_embed.description)
        self.assertNotIn("/", public_embed.description)

    async def test_double_start_transfers_only_one_active_exam(self) -> None:
        _, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        invitation = make_invitation(cog, config, moderator, target)
        first = make_interaction(target)
        second = make_interaction(target)

        with patch(
            "cogs.onboarding.role_exam.shuffled_questions",
            return_value=config.questions,
        ):
            await invitation.start_exam(first)
            await invitation.start_exam(second)

        first.response.send_message.assert_awaited_once()
        self.assertIsInstance(
            cog.active_sessions[(target.guild.id, target.id)],
            RoleExamView,
        )
        second.response.send_message.assert_awaited_once()
        self.assertIn(
            "hoàn tất",
            second.response.send_message.await_args.args[0],
        )

    async def test_invitation_timeout_disables_panel_and_cleans_session(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        public_message = SimpleNamespace(edit=AsyncMock())
        invitation = make_invitation(
            cog,
            config,
            moderator,
            target,
            public_message=public_message,
        )

        await invitation.on_timeout()

        self.assertTrue(invitation.completed)
        self.assertTrue(invitation.start_button.disabled)
        self.assertNotIn((guild.id, target.id), cog.active_sessions)
        embed = public_message.edit.await_args.kwargs["embed"]
        self.assertIn("hết hạn", embed.title)


class TestRoleExamView(unittest.IsolatedAsyncioTestCase):
    async def test_choice_navigation_review_and_answer_change(self) -> None:
        _, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        view = make_exam(cog, config, moderator, target)

        first_choice = make_interaction(target)
        await view.choose_answer(first_choice, "b")
        self.assertEqual(view.answers, {"q01": "b"})
        self.assertFalse(view.next_button.disabled)
        edited_embed = first_choice.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("Đã chọn", edited_embed.fields[0].name)

        await view.next_question(make_interaction(target))
        self.assertEqual(view.current_index, 1)
        self.assertEqual(view.answers["q01"], "b")

        await view.choose_answer(make_interaction(target), "c")
        await view.choose_answer(make_interaction(target), "a")
        self.assertEqual(view.answers["q02"], "a")

        await view.previous_question(make_interaction(target))
        self.assertEqual(view.current_index, 0)
        self.assertEqual(view.answers["q01"], "b")
        self.assertIn("Đã chọn", view.build_embed().fields[0].name)

    async def test_fail_submission_never_mutates_roles_and_cleans_session(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config(required_percent=100)
        cog = make_cog(bot, config)
        public_message = SimpleNamespace(edit=AsyncMock())
        view = make_exam(
            cog,
            config,
            moderator,
            target,
            public_message=public_message,
        )
        view.answers = {"q01": "a", "q02": "b"}
        view.current_index = 1
        view._sync_components()
        interaction = make_interaction(target)

        await view.next_question(interaction)

        self.assertTrue(view.completed)
        self.assertNotIn((guild.id, target.id), cog.active_sessions)
        target.add_roles.assert_not_awaited()
        private_embed = interaction.edit_original_response.await_args.kwargs["embed"]
        public_embed = public_message.edit.await_args.kwargs["embed"]
        self.assertIn("chưa đạt", private_embed.title)
        self.assertIn("Chưa đạt", public_embed.title)
        self.assertNotIn("1/2", public_embed.description)

    async def test_pass_submission_grants_once_and_rejects_second_click(self) -> None:
        guild, bot, moderator, target, reward = make_fixture()
        config = make_config(required_percent=100)
        cog = make_cog(bot, config)
        view = make_exam(cog, config, moderator, target)
        view.answers = {question.id: "a" for question in config.questions}
        view.current_index = 1
        view._sync_components()
        first = make_interaction(target)

        await view.next_question(first)
        await view.next_question(make_interaction(target))

        target.add_roles.assert_awaited_once()
        self.assertIs(target.add_roles.await_args.args[0], reward)
        self.assertTrue(view.completed)
        self.assertNotIn((guild.id, target.id), cog.active_sessions)
        self.assertNotIn((guild.id, target.id), ACTIVE_ROLE_MUTATION_TARGETS)

    async def test_concurrent_submit_is_finalized_exactly_once(self) -> None:
        _, bot, moderator, target, _ = make_fixture()
        config = make_config(required_percent=100)
        cog = make_cog(bot, config)
        view = make_exam(cog, config, moderator, target)
        view.answers = {question.id: "a" for question in config.questions}
        view.current_index = 1
        view._sync_components()
        finalize_started = asyncio.Event()
        release_finalize = asyncio.Event()

        async def finalize(_view, _correct):
            finalize_started.set()
            await release_finalize.wait()
            return ExamFinalizeResult(
                outcome="granted",
                private_embed=discord.Embed(title="private"),
                public_embed=discord.Embed(title="public"),
            )

        cog.finalize_attempt = AsyncMock(side_effect=finalize)
        first = make_interaction(target)
        second = make_interaction(target)
        first_task = asyncio.create_task(view.next_question(first))
        await asyncio.wait_for(finalize_started.wait(), timeout=1)
        try:
            await view.next_question(second)
            cog.finalize_attempt.assert_awaited_once_with(view, 2)
            self.assertIn(
                "được chấm",
                second.response.send_message.await_args.args[0],
            )
        finally:
            release_finalize.set()
            await first_task

        cog.finalize_attempt.assert_awaited_once()

    async def test_cancel_is_target_only_public_and_cleans_session(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        public_message = SimpleNamespace(edit=AsyncMock())
        view = make_exam(
            cog,
            config,
            moderator,
            target,
            public_message=public_message,
        )
        outsider = replace_member_id(target, 88)
        denied = make_interaction(outsider)

        await view.cancel(denied)
        self.assertIn((guild.id, target.id), cog.active_sessions)
        denied.response.send_message.assert_awaited_once()

        accepted = make_interaction(target)
        await view.cancel(accepted)
        self.assertTrue(view.completed)
        self.assertNotIn((guild.id, target.id), cog.active_sessions)
        self.assertIn(
            "hủy",
            public_message.edit.await_args.kwargs["embed"].title.lower(),
        )

    async def test_exam_timeout_updates_private_and_public_then_cleans(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        public_message = SimpleNamespace(edit=AsyncMock())
        view = make_exam(
            cog,
            config,
            moderator,
            target,
            public_message=public_message,
        )

        await view.on_timeout()

        self.assertTrue(view.completed)
        self.assertNotIn((guild.id, target.id), cog.active_sessions)
        self.assertIn("hết hạn", view.message.edit.await_args.kwargs["embed"].title)
        self.assertIn(
            "hết hạn",
            public_message.edit.await_args.kwargs["embed"].title,
        )


def replace_member_id(member: FakeMember, member_id: int) -> FakeMember:
    return FakeMember(
        member.guild,
        member_id,
        member.top_role,
        name=f"member-{member_id}",
        roles=list(member.roles),
    )


class TestRoleExamCommandAndDenials(unittest.IsolatedAsyncioTestCase):
    async def test_null_role_config_fails_closed_without_session(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        cog = make_cog(bot, make_config(role_id=None))
        ctx = make_context(guild, moderator)

        with patch("cogs.onboarding.role_exam.discord.Member", FakeMember):
            await RoleExamCog.role_exam.callback(cog, ctx, target)

        self.assertEqual(cog.active_sessions, {})
        self.assertIn("null", ctx.reply.await_args.args[0])

    async def test_command_creates_one_public_invitation_per_target(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        public_message = SimpleNamespace(edit=AsyncMock())
        ctx = make_context(guild, moderator, public_message=public_message)

        with patch("cogs.onboarding.role_exam.discord.Member", FakeMember):
            await RoleExamCog.role_exam.callback(cog, ctx, target)
            await RoleExamCog.role_exam.callback(cog, ctx, target)

        key = (guild.id, target.id)
        self.assertIsInstance(cog.active_sessions[key], RoleExamInvitationView)
        self.assertEqual(ctx.reply.await_count, 2)
        duplicate_message = ctx.reply.await_args.args[0]
        self.assertIn("đang có", duplicate_message)
        first_kwargs = ctx.reply.await_args_list[0].kwargs
        self.assertIs(first_kwargs["view"], cog.active_sessions[key])
        self.assertEqual(first_kwargs["allowed_mentions"].users, [target])

    def test_preflight_rejects_privileged_roles_and_hierarchy(self) -> None:
        guild, _, moderator, target, reward = make_fixture()

        reward.permissions = SimpleNamespace(administrator=True)
        self.assertIn(
            "administrator",
            role_assignment_denial(guild, moderator, target, reward),
        )

        reward.permissions = SimpleNamespace()
        reward.position = guild.me.top_role.position
        self.assertIn(
            "bot",
            role_assignment_denial(guild, moderator, target, reward).lower(),
        )

        reward.position = moderator.top_role.position
        guild.me.top_role.position = 100
        self.assertIn(
            "của bạn",
            role_assignment_denial(guild, moderator, target, reward),
        )

    def test_preflight_rejects_lost_permission_and_existing_role(self) -> None:
        guild, _, moderator, target, reward = make_fixture()
        moderator.guild_permissions.manage_roles = False
        self.assertIn(
            "không còn quyền",
            role_assignment_denial(guild, moderator, target, reward),
        )

        moderator.guild_permissions.manage_roles = True
        target.roles.append(reward)
        self.assertIn(
            "đã có role",
            role_assignment_denial(guild, moderator, target, reward),
        )


class TestRoleExamRoleGrant(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ACTIVE_ROLE_MUTATION_TARGETS.clear()

    def tearDown(self) -> None:
        ACTIVE_ROLE_MUTATION_TARGETS.clear()

    async def test_already_held_role_is_idempotent(self) -> None:
        _, bot, moderator, target, reward = make_fixture()
        target.roles.append(reward)
        config = make_config()
        cog = make_cog(bot, config)
        view = make_exam(cog, config, moderator, target)

        result = await cog.finalize_attempt(view, 2)

        self.assertEqual(result.outcome, "already_has")
        target.add_roles.assert_not_awaited()
        self.assertIn(reward.mention, result.public_embed.description)

    async def test_final_revalidation_blocks_permission_or_unsafe_role(self) -> None:
        _, bot, moderator, target, reward = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        view = make_exam(cog, config, moderator, target)

        moderator.guild_permissions.manage_roles = False
        result = await cog.finalize_attempt(view, 2)
        self.assertEqual(result.outcome, "passed_pending")
        target.add_roles.assert_not_awaited()

        moderator.guild_permissions.manage_roles = True
        reward.permissions = SimpleNamespace(manage_threads=True)
        result = await cog.finalize_attempt(view, 2)
        self.assertEqual(result.outcome, "passed_pending")
        target.add_roles.assert_not_awaited()

    async def test_shared_guard_blocks_grant_without_stealing_guard(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        view = make_exam(cog, config, moderator, target)
        key = (guild.id, target.id)
        ACTIVE_ROLE_MUTATION_TARGETS.add(key)

        result = await cog.finalize_attempt(view, 2)

        self.assertEqual(result.outcome, "passed_pending")
        target.add_roles.assert_not_awaited()
        self.assertIn(key, ACTIVE_ROLE_MUTATION_TARGETS)

    async def test_discord_failures_are_pending_and_clean_guard(self) -> None:
        for status in (403, 500):
            with self.subTest(status=status):
                guild, bot, moderator, target, _ = make_fixture()
                config = make_config()
                cog = make_cog(bot, config)
                view = make_exam(cog, config, moderator, target)
                target.add_roles.side_effect = make_http_exception(status)

                result = await cog.finalize_attempt(view, 2)

                self.assertEqual(result.outcome, "passed_pending")
                target.add_roles.assert_awaited_once()
                self.assertNotIn(
                    (guild.id, target.id),
                    ACTIVE_ROLE_MUTATION_TARGETS,
                )

    async def test_member_refresh_failure_is_pending_without_mutation(self) -> None:
        guild, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        view = make_exam(cog, config, moderator, target)
        guild.fetch_member.side_effect = make_http_exception(403)

        result = await cog.finalize_attempt(view, 2)

        self.assertEqual(result.outcome, "passed_pending")
        target.add_roles.assert_not_awaited()
        self.assertEqual(ACTIVE_ROLE_MUTATION_TARGETS, set())


class TestRoleExamUnload(unittest.IsolatedAsyncioTestCase):
    async def test_unload_closes_invitation_and_exam_sessions(self) -> None:
        _, bot, moderator, target, _ = make_fixture()
        config = make_config()
        cog = make_cog(bot, config)
        invitation_message = SimpleNamespace(edit=AsyncMock())
        invitation = make_invitation(
            cog,
            config,
            moderator,
            target,
            public_message=invitation_message,
        )
        second_target = replace_member_id(target, 78)
        exam_public_message = SimpleNamespace(edit=AsyncMock())
        exam = make_exam(
            cog,
            config,
            moderator,
            second_target,
            public_message=exam_public_message,
        )

        cog.cog_unload()
        cleanup_tasks = list(cog._cleanup_tasks)
        await asyncio.gather(*cleanup_tasks)

        self.assertTrue(cog._unloading)
        self.assertEqual(cog.active_sessions, {})
        self.assertTrue(invitation.completed)
        self.assertTrue(exam.completed)
        self.assertIn(
            "dừng",
            invitation_message.edit.await_args.kwargs["embed"].title,
        )
        self.assertIn(
            "dừng",
            exam_public_message.edit.await_args.kwargs["embed"].title,
        )


if __name__ == "__main__":
    unittest.main()
