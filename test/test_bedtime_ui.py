import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.bedtime_remind._bedtime_ui import (
    ADD_BACK_CUSTOM_ID,
    ADD_CHANNEL_CUSTOM_ID,
    ADD_MEMBER_CUSTOM_ID,
    ADD_SAVE_CUSTOM_ID,
    ADD_TIMES_CUSTOM_ID,
    BEDTIME_UI_TIMEOUT_SECONDS,
    PANEL_ADD_CUSTOM_ID,
    PANEL_NEXT_CUSTOM_ID,
    PANEL_PREV_CUSTOM_ID,
    PANEL_REFRESH_CUSTOM_ID,
    PANEL_REMOVE_CUSTOM_ID,
    REMOVE_BACK_CUSTOM_ID,
    REMOVE_CONFIRM_CUSTOM_ID,
    REMOVE_SCHEDULE_CUSTOM_ID,
    REMINDER_CHANNEL_TYPES,
    BedtimeAddView,
    BedtimePanelView,
    BedtimeRemoveView,
    BedtimeTimesModal,
)
from test_bedtime_reminder import (
    CHANNEL_ID,
    GUILD_ID,
    NOW,
    USER_ID,
    BedtimeFixture,
    make_member,
    reminder_document,
)


AUTHOR_ID = 7


def make_interaction(
    *,
    user_id: int = AUTHOR_ID,
    guild_id: int = GUILD_ID,
    administrator: bool = True,
    guild: object | None = None,
) -> SimpleNamespace:
    acknowledged = {"done": False}

    def is_done() -> bool:
        return acknowledged["done"]

    async def defer(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    async def send_message(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    async def edit_message(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    async def send_modal(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    return SimpleNamespace(
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(administrator=administrator),
        ),
        guild=SimpleNamespace(id=guild_id) if guild is None else guild,
        response=SimpleNamespace(
            defer=AsyncMock(side_effect=defer),
            edit_message=AsyncMock(side_effect=edit_message),
            send_message=AsyncMock(side_effect=send_message),
            send_modal=AsyncMock(side_effect=send_modal),
            is_done=is_done,
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


class TestBedtimePanelView(unittest.IsolatedAsyncioTestCase):
    async def _stop_view(self, view: discord.ui.View) -> None:
        view.stop()

    def _panel(
        self,
        fixture: BedtimeFixture,
        *,
        page: int = 0,
    ) -> BedtimePanelView:
        view = BedtimePanelView(
            fixture.cog,
            guild_id=GUILD_ID,
            author_id=AUTHOR_ID,
            prefix="!tf ",
            page=page,
        )
        self.addAsyncCleanup(self._stop_view, view)
        return view

    async def test_command_opens_owner_locked_panel(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        ctx = fixture.make_context()
        sent = SimpleNamespace(id=55, edit=AsyncMock())
        ctx.send = AsyncMock(return_value=sent)

        await fixture.cog.bedtime.callback(fixture.cog, ctx)

        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        view = kwargs["view"]
        self.addAsyncCleanup(self._stop_view, view)
        self.assertIsInstance(view, BedtimePanelView)
        self.assertIs(view.message, sent)
        self.assertEqual(view.timeout, BEDTIME_UI_TIMEOUT_SECONDS)
        self.assertEqual(
            [item.custom_id for item in view.children],
            [
                PANEL_ADD_CUSTOM_ID,
                PANEL_REMOVE_CUSTOM_ID,
                PANEL_REFRESH_CUSTOM_ID,
                PANEL_PREV_CUSTOM_ID,
                PANEL_NEXT_CUSTOM_ID,
            ],
        )
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertIn("(`42`)", kwargs["embed"].description)
        self.assertNotIn("<@", kwargs["embed"].description)

        owner = make_interaction()
        stranger = make_interaction(user_id=99)
        lost_admin = make_interaction(administrator=False)
        wrong_guild = make_interaction(guild_id=999)
        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        self.assertFalse(await view.interaction_check(lost_admin))
        self.assertFalse(await view.interaction_check(wrong_guild))
        stranger.response.send_message.assert_awaited_once()

    async def test_empty_panel_disables_remove_and_pagination(self) -> None:
        fixture = BedtimeFixture()
        view = self._panel(fixture)

        self.assertTrue(view.remove_button.disabled)
        self.assertTrue(view.prev_button.disabled)
        self.assertTrue(view.next_button.disabled)
        self.assertIn("chưa có lịch", view.build_embed().description)

    async def test_pagination_and_refresh_stay_guild_scoped(self) -> None:
        documents = [
            reminder_document(user_id=user_id)
            for user_id in range(1, 13)
        ]
        documents.append(reminder_document(guild_id=999, user_id=999))
        fixture = BedtimeFixture(documents)
        fixture.guild.get_member.side_effect = lambda user_id: make_member(
            user_id,
            guild=fixture.guild,
        )
        view = self._panel(fixture)

        self.assertFalse(view.remove_button.disabled)
        self.assertTrue(view.prev_button.disabled)
        self.assertFalse(view.next_button.disabled)
        first_page = view.build_embed().description
        self.assertIn("(`1`)", first_page)
        self.assertIn("(`10`)", first_page)
        self.assertNotIn("(`11`)", first_page)
        self.assertNotIn("(`999`)", first_page)

        await view.turn_page(make_interaction(), 1)

        self.assertEqual(view.page, 1)
        second_page = view.build_embed().description
        self.assertIn("(`11`)", second_page)
        self.assertIn("(`12`)", second_page)
        self.assertNotIn("(`1`)", second_page)

        await view.refresh(make_interaction())
        self.assertEqual(view.page, 1)

    async def test_add_button_replaces_panel_with_add_view(self) -> None:
        fixture = BedtimeFixture()
        view = self._panel(fixture)
        interaction = make_interaction()

        await view.add_button.callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        replacement = interaction.response.edit_message.await_args.kwargs["view"]
        self.addAsyncCleanup(self._stop_view, replacement)
        self.assertIsInstance(replacement, BedtimeAddView)
        self.assertTrue(view.is_finished())
        self.assertEqual(
            [item.custom_id for item in replacement.children],
            [
                ADD_MEMBER_CUSTOM_ID,
                ADD_CHANNEL_CUSTOM_ID,
                ADD_TIMES_CUSTOM_ID,
                ADD_SAVE_CUSTOM_ID,
                ADD_BACK_CUSTOM_ID,
            ],
        )
        self.assertIsInstance(replacement.member_select, discord.ui.UserSelect)
        self.assertIsInstance(replacement.channel_select, discord.ui.ChannelSelect)
        self.assertEqual(
            list(replacement.channel_select.channel_types),
            list(REMINDER_CHANNEL_TYPES),
        )
        self.assertTrue(replacement.save_button.disabled)

    async def test_remove_without_schedules_stays_on_panel(self) -> None:
        fixture = BedtimeFixture()
        view = self._panel(fixture)
        interaction = make_interaction()

        await view.remove_button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()
        self.assertFalse(view.is_finished())


class TestBedtimeAddView(unittest.IsolatedAsyncioTestCase):
    async def _stop_view(self, view: discord.ui.View) -> None:
        view.stop()

    def _view(self, fixture: BedtimeFixture) -> BedtimeAddView:
        view = BedtimeAddView(
            fixture.cog,
            guild_id=GUILD_ID,
            author_id=AUTHOR_ID,
            prefix="!tf ",
        )
        self.addAsyncCleanup(self._stop_view, view)
        return view

    async def test_selects_modal_and_save_create_a_schedule(self) -> None:
        fixture = BedtimeFixture()
        view = self._view(fixture)

        await view.choose_member(make_interaction(), fixture.member)
        await view.choose_channel(make_interaction(), fixture.channel)
        self.assertTrue(view.save_button.disabled)

        modal_interaction = make_interaction()
        await view.open_times_modal(modal_interaction)
        modal_interaction.response.send_modal.assert_awaited_once()
        modal = modal_interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, BedtimeTimesModal)

        modal.bedtime_input._value = "22:00"
        modal.wake_input._value = "6:00"
        await modal.on_submit(make_interaction())
        self.assertEqual(view.bedtime_minutes, 22 * 60)
        self.assertEqual(view.wake_minutes, 6 * 60)
        self.assertFalse(view.save_button.disabled)

        with patch(
            "cogs.bedtime_remind.bedtime_remind._utcnow",
            return_value=NOW,
        ):
            confirm = make_interaction()
            await view.confirm(confirm)

        self.assertTrue(view.is_finished())
        stored = fixture.collection.documents[0]
        self.assertEqual(stored["user_id"], USER_ID)
        self.assertEqual(stored["channel_id"], CHANNEL_ID)
        self.assertEqual(stored["bedtime_minutes"], 22 * 60)
        self.assertEqual(stored["wake_minutes"], 6 * 60)
        self.assertEqual(stored["updated_by"], AUTHOR_ID)
        confirm.edit_original_response.assert_awaited_once()
        panel = confirm.edit_original_response.await_args.kwargs["view"]
        self.addAsyncCleanup(self._stop_view, panel)
        self.assertIsInstance(panel, BedtimePanelView)
        confirm.followup.send.assert_awaited_once()
        self.assertIn("thêm", confirm.followup.send.await_args.args[0])

    async def test_existing_member_prefills_times_and_channel(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        view = self._view(fixture)

        await view.choose_member(make_interaction(), fixture.member)

        self.assertIs(view.member, fixture.member)
        self.assertIs(view.channel, fixture.channel)
        self.assertEqual(view.bedtime_minutes, 22 * 60)
        self.assertEqual(view.wake_minutes, 6 * 60)
        self.assertIn("22:00–06:00", view.build_embed().to_dict()["fields"][2]["value"])

    async def test_rejects_bots_invalid_times_and_missing_channel_permission(
        self,
    ) -> None:
        fixture = BedtimeFixture()
        view = self._view(fixture)
        bot_member = make_member(50, guild=fixture.guild, bot=True)
        fixture.members[50] = bot_member

        await view.choose_member(make_interaction(), bot_member)
        self.assertIsNone(view.member)

        fixture.channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True,
            send_messages=False,
        )
        await view.choose_channel(make_interaction(), fixture.channel)
        self.assertIsNone(view.channel)

        modal = BedtimeTimesModal(view)
        modal.bedtime_input._value = "24:00"
        modal.wake_input._value = "06:00"
        invalid = make_interaction()
        await modal.on_submit(invalid)
        invalid.response.send_message.assert_awaited_once()
        self.assertIsNone(view.bedtime_minutes)

        modal.bedtime_input._value = "22:00"
        modal.wake_input._value = "22:00"
        same = make_interaction()
        await modal.on_submit(same)
        self.assertIn(
            "khác nhau",
            same.response.send_message.await_args.kwargs["content"],
        )

    async def test_back_returns_to_panel_without_saving(self) -> None:
        fixture = BedtimeFixture()
        view = self._view(fixture)
        interaction = make_interaction()

        await view.go_back(interaction)

        self.assertTrue(view.is_finished())
        self.assertEqual(fixture.collection.documents, [])
        panel = interaction.response.edit_message.await_args.kwargs["view"]
        self.addAsyncCleanup(self._stop_view, panel)
        self.assertIsInstance(panel, BedtimePanelView)


class TestBedtimeRemoveView(unittest.IsolatedAsyncioTestCase):
    async def _stop_view(self, view: discord.ui.View) -> None:
        view.stop()

    def _view(self, fixture: BedtimeFixture) -> BedtimeRemoveView:
        view = BedtimeRemoveView(
            fixture.cog,
            guild_id=GUILD_ID,
            author_id=AUTHOR_ID,
            prefix="!tf ",
        )
        self.addAsyncCleanup(self._stop_view, view)
        return view

    async def test_select_and_confirm_deletes_schedule_and_returns_to_panel(
        self,
    ) -> None:
        fixture = BedtimeFixture([reminder_document()])
        view = self._view(fixture)

        self.assertIsNotNone(view.schedule_select)
        self.assertEqual(
            view.schedule_select.custom_id,
            REMOVE_SCHEDULE_CUSTOM_ID,
        )
        self.assertTrue(view.confirm_button.disabled)
        self.assertEqual(
            [option.value for option in view.schedule_select.options],
            [str(USER_ID)],
        )

        await view.choose_user_id(make_interaction(), str(USER_ID))
        self.assertEqual(view.selected_user_id, USER_ID)
        self.assertFalse(view.confirm_button.disabled)

        confirm = make_interaction()
        await view.confirm(confirm)

        self.assertEqual(fixture.collection.documents, [])
        self.assertNotIn((GUILD_ID, USER_ID), fixture.cog.reminders_by_member)
        self.assertTrue(view.is_finished())
        panel = confirm.edit_original_response.await_args.kwargs["view"]
        self.addAsyncCleanup(self._stop_view, panel)
        self.assertIsInstance(panel, BedtimePanelView)
        confirm.followup.send.assert_awaited_once()
        self.assertIn("Đã xóa", confirm.followup.send.await_args.args[0])

    async def test_remove_view_keeps_remaining_schedules_after_delete(
        self,
    ) -> None:
        other_id = 77
        fixture = BedtimeFixture(
            [
                reminder_document(),
                reminder_document(user_id=other_id),
            ]
        )
        fixture.members[other_id] = make_member(other_id, guild=fixture.guild)
        view = self._view(fixture)

        await view.choose_user_id(make_interaction(), str(USER_ID))
        confirm = make_interaction()
        await view.confirm(confirm)

        self.assertFalse(view.is_finished())
        self.assertEqual(
            [int(document["user_id"]) for document in fixture.collection.documents],
            [other_id],
        )
        confirm.edit_original_response.assert_awaited_once()
        self.assertIs(confirm.edit_original_response.await_args.kwargs["view"], view)
        self.assertIn("Đã xóa", view.build_embed().description)

    async def test_back_button_custom_id_is_stable(self) -> None:
        fixture = BedtimeFixture([reminder_document()])
        view = self._view(fixture)
        self.assertEqual(view.back_button.custom_id, REMOVE_BACK_CUSTOM_ID)
        self.assertEqual(view.confirm_button.custom_id, REMOVE_CONFIRM_CUSTOM_ID)


if __name__ == "__main__":
    unittest.main()
