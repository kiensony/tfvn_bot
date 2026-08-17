import asyncio
import unittest
from types import SimpleNamespace

from cogs.mod._interaction_ui import FormAnswer
from cogs.mod.role import RoleCopyWorkflowView, RoleRollView, RollCog, plan_role_copy
from test_role_roll import FakeMember, FakeRole, make_fixture, make_interaction


class TestRoleConfirmationRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_source_select_cannot_desync_frozen_plan(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        alternate_role = FakeRole(guild, 3_000, 30, name="Alternate")
        alternate_top = FakeRole(guild, 3_001, 50, name="Alternate top")
        alternate_source = FakeMember(
            guild,
            67,
            alternate_top,
            name="alternate-source",
            roles=[guild.roles[0], alternate_role, alternate_top],
        )
        guild.roles.extend((alternate_role, alternate_top))
        guild.members[alternate_source.id] = alternate_source
        view = RoleCopyWorkflowView(
            author_id=moderator.id,
            target=target,
            submitter=RollCog(SimpleNamespace())._submit_role_copy,
        )
        first = make_interaction(guild, moderator)
        second = make_interaction(guild, moderator)
        edit_started = asyncio.Event()
        release_edit = asyncio.Event()

        async def block_first_edit(**_kwargs) -> None:
            edit_started.set()
            await release_edit.wait()

        first.response.edit_message.side_effect = block_first_edit
        first_task = asyncio.create_task(
            view.accept_answer(
                first,
                "source_id",
                FormAnswer(source.id, str(source)),
            )
        )
        try:
            await asyncio.wait_for(edit_started.wait(), timeout=1)
            await view.accept_answer(
                second,
                "source_id",
                FormAnswer(alternate_source.id, str(alternate_source)),
            )

            self.assertEqual(view._frozen_source_id, source.id)
            self.assertEqual(view.values["source_id"].value, source.id)
            self.assertIn(
                "đang xử lý",
                second.response.send_message.await_args.args[0],
            )
        finally:
            release_edit.set()
            await first_task

    async def test_selecting_role_only_stages_the_request(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        view = RoleRollView(author_id=moderator.id, target=target)

        await view.assign_role(make_interaction(guild, moderator), eligible)

        self.assertEqual(view.values["role_id"].value, eligible.id)
        self.assertEqual(view.step, "reason")
        target.add_roles.assert_not_awaited()

    async def test_reply_source_cannot_equal_destination(self) -> None:
        guild, moderator, _, target, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        view = RoleCopyWorkflowView(
            author_id=moderator.id,
            target=target,
            submitter=cog._submit_role_copy,
        )
        interaction = make_interaction(guild, moderator)

        await view.accept_answer(
            interaction,
            "source_id",
            FormAnswer(target.id, str(target)),
        )

        self.assertEqual(view.step, "field:source_id")
        self.assertIsNone(view._frozen_source_id)
        target.add_roles.assert_not_awaited()
        self.assertIn(
            "phải khác nhau",
            interaction.response.send_message.await_args.args[0],
        )

    async def test_rolecopy_preview_stays_within_embed_field_limit(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        for index in range(40):
            role = FakeRole(
                guild,
                2_000 + index,
                30,
                name=f"preview-role-{index}-" + ("x" * 50),
            )
            guild.roles.append(role)
            source.roles.append(role)
        plan = plan_role_copy(guild, moderator, source, target)
        view = RoleCopyWorkflowView(
            author_id=moderator.id,
            target=target,
            source=source,
            plan=plan,
            submitter=RollCog(SimpleNamespace())._submit_role_copy,
        )

        embed = view.build_embed()
        preview_field = next(
            field for field in embed.fields if field.name.startswith("Role sẽ sao chép")
        )

        self.assertLessEqual(len(preview_field.value), 1024)
        target.add_roles.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
