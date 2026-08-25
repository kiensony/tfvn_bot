import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from pymongo.errors import PyMongoError

import cogs.minigames.crocodile_dentist as crocodile_package
from cogs.minigames.crocodile_dentist._crocodile_helpers import (
    GameStateError,
    apply_invitation_response,
    is_game_expired,
    parse_challenge_arguments,
    press_tooth,
    resolve_active_expiry,
    resolve_pending_game,
    tooth_layout,
    validate_invitees,
)
from cogs.minigames.crocodile_dentist.crocodile import (
    CONFIRM_CUSTOM_ID,
    DECLINE_CUSTOM_ID,
    NO_MENTIONS,
    TOOTH_CUSTOM_ID_PREFIX,
    CrocodileConfirmationView,
    CrocodileDentistCog,
    CrocodileGameView,
)


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def member(user_id: int, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, bot=bot)


def pending_game(**overrides) -> dict:
    game = {
        "status": "pending",
        "revision": 4,
        "host_id": 10,
        "original_player_ids": [10, 20, 30],
        "participant_ids": [10, 20, 30],
        "player_ids": [10],
        "responses": {
            "10": "accepted",
            "20": "pending",
            "30": "pending",
        },
        "tooth_count": 13,
        "dangerous_tooth": 7,
        "pressed_teeth": [],
        "current_turn": 0,
        "current_player_id": 10,
        "invitation_expires_at": NOW + timedelta(minutes=5),
        "last_activity_at": None,
        "activity_expires_at": None,
        "started_at": None,
        "updated_at": NOW,
        "completed_at": None,
        "result": None,
        "cancel_reason": None,
    }
    game.update(overrides)
    return game


def active_game(**overrides) -> dict:
    game = pending_game(
        status="active",
        revision=8,
        participant_ids=[10, 20, 30],
        player_ids=[10, 20, 30],
        responses={"10": "accepted", "20": "accepted", "30": "accepted"},
        invitation_expires_at=NOW - timedelta(minutes=1),
        last_activity_at=NOW,
        activity_expires_at=NOW + timedelta(days=7),
    )
    game.update(overrides)
    return game


def persisted_game(*, active: bool = False, **overrides) -> dict:
    game = active_game() if active else pending_game()
    game.update(
        {
            "_id": "mongo-game-7",
            "guild_id": 100,
            "game_id": 7,
            "panel_channel_id": 200,
            "panel_message_id": 300,
        }
    )
    game.update(overrides)
    return game


def make_cog(games: MagicMock | None = None) -> CrocodileDentistCog:
    cog = object.__new__(CrocodileDentistCog)
    cog.bot = SimpleNamespace(
        get_channel=MagicMock(return_value=None),
        fetch_channel=AsyncMock(),
    )
    cog.db = {}
    cog.games = games or MagicMock()
    cog.counters = MagicMock()
    cog._game_locks = {}
    return cog


def make_interaction(
    *,
    user_id: int = 10,
    guild_id: int = 100,
    channel_id: int = 200,
    message_id: int = 300,
) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=channel_id),
        message=SimpleNamespace(id=message_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
    )


def make_context(
    *,
    author_id: int = 10,
    guild_id: int = 100,
    channel_id: int = 200,
) -> SimpleNamespace:
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id),
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=channel_id),
        send=AsyncMock(),
        reply=AsyncMock(),
    )


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)
        self.sort_calls: list[tuple[str, int]] = []
        self.limit_calls: list[int] = []

    def sort(self, field: str, direction: int) -> "FakeCursor":
        self.sort_calls.append((field, direction))
        return self

    def limit(self, count: int) -> list[dict]:
        self.limit_calls.append(count)
        return self.documents[:count]


class TestChallengeArguments(unittest.TestCase):
    def test_omitted_tooth_count_defaults_to_thirteen(self) -> None:
        parsed = parse_challenge_arguments("<@101> <@!202>")

        self.assertEqual(parsed.teeth_count, 13)
        self.assertEqual(list(parsed.invitee_ids), [101, 202])

    def test_leading_tooth_count_is_optional_and_bounded(self) -> None:
        for teeth_count in (2, 13, 25):
            with self.subTest(teeth_count=teeth_count):
                parsed = parse_challenge_arguments(
                    [str(teeth_count), "<@101>", "<@202>"]
                )
                self.assertEqual(parsed.teeth_count, teeth_count)
                self.assertEqual(list(parsed.invitee_ids), [101, 202])

        for teeth_count in (1, 26):
            with self.subTest(teeth_count=teeth_count):
                with self.assertRaises(ValueError):
                    parse_challenge_arguments([str(teeth_count), "<@101>"])

    def test_count_must_precede_mentions_and_invitees_are_required(self) -> None:
        invalid_arguments = (
            "",
            "13",
            "<@101> 13",
            "not-a-mention",
            "<@101> <@202> <@303> <@404> <@505>",
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    parse_challenge_arguments(arguments)


class TestInviteeValidation(unittest.TestCase):
    def test_accepts_one_to_four_distinct_human_invitees(self) -> None:
        for count in range(1, 5):
            with self.subTest(count=count):
                invitees = [member(user_id) for user_id in range(101, 101 + count)]
                self.assertEqual(
                    validate_invitees(42, invitees),
                    tuple(range(101, 101 + count)),
                )

    def test_rejects_missing_self_duplicate_bot_and_too_many_invitees(self) -> None:
        invalid_invitee_sets = (
            [],
            [member(42)],
            [member(101), member(101)],
            [member(101, bot=True)],
            [member(user_id) for user_id in range(101, 106)],
        )
        for invitees in invalid_invitee_sets:
            with self.subTest(invitee_ids=[item.id for item in invitees]):
                with self.assertRaises(ValueError):
                    validate_invitees(42, invitees)


class TestToothLayout(unittest.TestCase):
    def test_every_supported_board_fits_discord_component_limits(self) -> None:
        for teeth_count in (2, 13, 25):
            with self.subTest(teeth_count=teeth_count):
                rows = tooth_layout(teeth_count)

                self.assertLessEqual(len(rows), 5)
                self.assertTrue(all(1 <= len(row) <= 5 for row in rows))
                self.assertEqual(
                    [tooth for row in rows for tooth in row],
                    list(range(1, teeth_count + 1)),
                )

    def test_rejects_board_sizes_outside_two_to_twenty_five(self) -> None:
        for teeth_count in (1, 26):
            with self.subTest(teeth_count=teeth_count):
                with self.assertRaises(ValueError):
                    tooth_layout(teeth_count)


class TestInvitationTransitions(unittest.TestCase):
    def test_final_responses_start_with_host_first_and_only_acceptors(self) -> None:
        original = pending_game()

        after_accept = apply_invitation_response(
            original,
            user_id=30,
            accepted=True,
            now=NOW + timedelta(seconds=10),
        )
        started = apply_invitation_response(
            after_accept,
            user_id=20,
            accepted=False,
            now=NOW + timedelta(seconds=20),
        )

        self.assertEqual(original["responses"]["30"], "pending")
        self.assertEqual(after_accept["status"], "pending")
        self.assertEqual(after_accept["participant_ids"], [10, 20, 30])
        self.assertEqual(after_accept["player_ids"], [10])
        self.assertEqual(started["status"], "active")
        self.assertEqual(started["player_ids"], [10, 30])
        self.assertEqual(started["current_turn"], 0)
        self.assertEqual(started["current_player_id"], 10)
        self.assertEqual(started["started_at"], NOW + timedelta(seconds=20))
        self.assertEqual(started["dangerous_tooth"], 7)
        self.assertEqual(started["revision"], original["revision"] + 2)
        self.assertEqual(
            started["activity_expires_at"],
            NOW + timedelta(seconds=20, days=7),
        )

    def test_all_invitees_declining_cancels_game(self) -> None:
        first = apply_invitation_response(
            pending_game(), user_id=20, accepted=False, now=NOW
        )
        self.assertEqual(first["participant_ids"], [10, 30])
        self.assertEqual(first["player_ids"], [10])
        cancelled = apply_invitation_response(
            first, user_id=30, accepted=False, now=NOW
        )

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["player_ids"], [10])
        self.assertEqual(cancelled["completed_at"], NOW)
        self.assertEqual(cancelled["cancel_reason"], "no_accepted_invitees")

    def test_invitation_responses_are_final_and_invitee_only(self) -> None:
        responded = apply_invitation_response(
            pending_game(), user_id=20, accepted=True, now=NOW
        )

        invalid_responses = (
            (responded, 20),
            (pending_game(), 10),
            (pending_game(), 999),
            (pending_game(status="active"), 20),
        )
        for game, user_id in invalid_responses:
            with self.subTest(user_id=user_id, status=game["status"]):
                with self.assertRaises(GameStateError):
                    apply_invitation_response(
                        game,
                        user_id=user_id,
                        accepted=False,
                        now=NOW,
                    )

    def test_deadline_times_out_nonresponders_and_starts_with_acceptors(self) -> None:
        game = pending_game(
            responses={"10": "accepted", "20": "accepted", "30": "pending"}
        )

        self.assertIsNone(
            resolve_pending_game(game, game["invitation_expires_at"] - timedelta(seconds=1))
        )
        resolved = resolve_pending_game(game, game["invitation_expires_at"])

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["status"], "active")
        self.assertEqual(resolved["responses"]["30"], "timed_out")
        self.assertEqual(resolved["participant_ids"], [10, 20])
        self.assertEqual(resolved["player_ids"], [10, 20])
        self.assertEqual(resolved["current_player_id"], 10)
        self.assertEqual(resolved["revision"], game["revision"] + 1)

    def test_deadline_cancels_when_only_host_remains(self) -> None:
        game = pending_game(
            responses={"10": "accepted", "20": "declined", "30": "pending"}
        )

        resolved = resolve_pending_game(game, game["invitation_expires_at"])

        self.assertEqual(resolved["status"], "cancelled")
        self.assertEqual(resolved["responses"]["30"], "timed_out")
        self.assertEqual(resolved["participant_ids"], [10])
        self.assertEqual(resolved["player_ids"], [10])
        self.assertEqual(resolved["completed_at"], game["invitation_expires_at"])
        self.assertEqual(resolved["cancel_reason"], "no_accepted_invitees")


class TestToothTransitions(unittest.TestCase):
    def test_safe_press_advances_cyclically_and_refreshes_expiry(self) -> None:
        game = active_game(current_turn=2, current_player_id=30)
        pressed_at = NOW + timedelta(hours=1)

        updated = press_tooth(game, user_id=30, tooth_number=6, now=pressed_at)

        self.assertEqual(game["pressed_teeth"], [])
        self.assertEqual(updated["status"], "active")
        self.assertEqual(updated["pressed_teeth"], [6])
        self.assertEqual(updated["current_turn"], 0)
        self.assertEqual(updated["current_player_id"], 10)
        self.assertEqual(updated["last_activity_at"], pressed_at)
        self.assertEqual(updated["activity_expires_at"], pressed_at + timedelta(days=7))
        self.assertEqual(updated["dangerous_tooth"], 7)
        self.assertEqual(updated["revision"], game["revision"] + 1)

    def test_dangerous_press_finishes_with_one_loser_and_other_winners(self) -> None:
        game = active_game(current_turn=1, current_player_id=20)

        finished = press_tooth(game, user_id=20, tooth_number=7, now=NOW)

        self.assertEqual(finished["status"], "finished")
        self.assertEqual(finished["pressed_teeth"], [7])
        self.assertEqual(finished["completed_at"], NOW)
        self.assertIsNone(finished["current_turn"])
        self.assertIsNone(finished["current_player_id"])
        self.assertEqual(
            finished["result"],
            {
                "loser_id": 20,
                "winner_ids": [10, 30],
                "dangerous_tooth": 7,
                "pressed_at": NOW,
            },
        )
        self.assertEqual(finished["dangerous_tooth"], 7)

    def test_wrong_player_outsider_and_pressed_tooth_are_rejected(self) -> None:
        cases = (
            (active_game(), 20, 1),
            (active_game(), 999, 1),
            (active_game(pressed_teeth=[1]), 10, 1),
            (active_game(), 10, 0),
            (active_game(), 10, 14),
            (active_game(status="finished"), 10, 1),
        )
        for game, user_id, tooth_number in cases:
            with self.subTest(
                user_id=user_id,
                tooth_number=tooth_number,
                status=game["status"],
            ):
                before = copy.deepcopy(game)
                with self.assertRaises(GameStateError):
                    press_tooth(
                        game,
                        user_id=user_id,
                        tooth_number=tooth_number,
                        now=NOW + timedelta(hours=1),
                    )
                self.assertEqual(game, before)


class TestActiveExpiry(unittest.TestCase):
    def test_active_game_expires_only_at_its_persisted_deadline(self) -> None:
        game = active_game()
        deadline = game["activity_expires_at"]

        self.assertFalse(is_game_expired(game, deadline - timedelta(seconds=1)))
        self.assertIsNone(
            resolve_active_expiry(game, deadline - timedelta(seconds=1))
        )
        self.assertTrue(is_game_expired(game, deadline))

        cancelled = resolve_active_expiry(game, deadline)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["completed_at"], deadline)
        self.assertEqual(cancelled["cancel_reason"], "inactivity_timeout")
        self.assertIsNone(cancelled["current_turn"])
        self.assertIsNone(cancelled["current_player_id"])
        self.assertEqual(cancelled["revision"], game["revision"] + 1)

    def test_terminal_games_do_not_use_active_expiry(self) -> None:
        for status in ("finished", "cancelled"):
            with self.subTest(status=status):
                game = active_game(status=status)
                self.assertFalse(is_game_expired(game, game["activity_expires_at"]))
                with self.assertRaises(GameStateError):
                    resolve_active_expiry(game, game["activity_expires_at"])


class TestPersistentViews(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_view_is_persistent_and_routes_both_answers(
        self,
    ) -> None:
        handler = AsyncMock()
        cog = SimpleNamespace(handle_invitation_response=handler)
        view = CrocodileConfirmationView(cog)
        interaction = make_interaction(user_id=20)

        self.assertIsNone(view.timeout)
        self.assertEqual(
            {item.custom_id for item in view.children},
            {CONFIRM_CUSTOM_ID, DECLINE_CUSTOM_ID},
        )

        await view.confirm.callback(interaction)
        handler.assert_awaited_once_with(interaction, accepted=True)
        handler.reset_mock()
        await view.decline.callback(interaction)
        handler.assert_awaited_once_with(interaction, accepted=False)
        view.stop()

    async def test_tooth_dispatcher_has_all_stable_ids_and_routes_number(
        self,
    ) -> None:
        handler = AsyncMock()
        cog = SimpleNamespace(handle_tooth_press=handler)
        view = CrocodileGameView(
            cog,
            tooth_count=25,
            pressed_teeth=[2, 25],
        )

        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 25)
        self.assertEqual(
            [item.custom_id for item in view.children],
            [f"{TOOTH_CUSTOM_ID_PREFIX}{number}" for number in range(1, 26)],
        )
        self.assertEqual({item.row for item in view.children}, set(range(5)))
        self.assertTrue(view.children[1].disabled)
        self.assertTrue(view.children[24].disabled)
        self.assertFalse(view.children[0].disabled)

        interaction = make_interaction(user_id=10)
        await view.children[5].callback(interaction)
        handler.assert_awaited_once_with(interaction, 6)
        view.stop()

    async def test_terminal_board_disables_every_tooth(self) -> None:
        cog = make_cog()
        game = persisted_game(
            active=True,
            status="finished",
            current_turn=None,
            current_player_id=None,
            result={
                "loser_id": 10,
                "winner_ids": [20, 30],
                "dangerous_tooth": 7,
                "pressed_at": NOW,
            },
        )

        view = cog.build_view(game)

        self.assertEqual(len(view.children), 13)
        self.assertTrue(all(item.disabled for item in view.children))
        view.stop()


class TestCogPersistenceSetup(unittest.TestCase):
    def test_constructor_creates_indexes_and_registers_persistent_dispatchers(
        self,
    ) -> None:
        games = MagicMock()
        counters = MagicMock()
        bot = SimpleNamespace(
            db={"crocodile_games": games, "feature_counters": counters},
            add_view=MagicMock(),
        )

        with patch("discord.ext.tasks.Loop.start") as start:
            cog = CrocodileDentistCog(bot)

        start.assert_called_once()
        registered = [call.args[0] for call in bot.add_view.call_args_list]
        self.assertEqual(len(registered), 2)
        self.assertIsInstance(registered[0], CrocodileConfirmationView)
        self.assertIsInstance(registered[1], CrocodileGameView)
        self.assertIsNone(registered[0].timeout)
        self.assertIsNone(registered[1].timeout)
        self.assertEqual(len(registered[1].children), 25)

        index_calls = {call.kwargs.get("name"): call for call in games.create_index.call_args_list}
        self.assertEqual(
            index_calls["guild_game_unique"].args[0],
            [("guild_id", 1), ("game_id", 1)],
        )
        participant_fields = [
            field for field, _direction in index_calls["guild_participant_open"].args[0]
        ]
        self.assertIn("participant_ids", participant_fields)
        self.assertIn("status", participant_fields)
        self.assertIn("updated_at", participant_fields)
        for view in registered:
            view.stop()


class TestPackageEntrypoint(unittest.IsolatedAsyncioTestCase):
    async def test_package_profile_path_delegates_to_cog_module(self) -> None:
        bot = MagicMock()
        setup_cog = AsyncMock()

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile.setup",
            new=setup_cog,
        ):
            await crocodile_package.setup(bot)

        setup_cog.assert_awaited_once_with(bot)


class TestPersistentInteractions(unittest.IsolatedAsyncioTestCase):
    async def test_panel_lookup_binds_guild_channel_and_message(self) -> None:
        games = MagicMock()
        games.find_one.return_value = None
        cog = make_cog(games)
        interaction = make_interaction()

        self.assertIsNone(cog._game_for_panel(interaction))

        games.find_one.assert_called_once_with(
            {
                "guild_id": 100,
                "panel_channel_id": 200,
                "panel_message_id": 300,
            }
        )

    async def test_stale_panel_is_ephemeral_and_cannot_mutate_state(self) -> None:
        games = MagicMock()
        games.find_one.return_value = None
        cog = make_cog(games)
        interaction = make_interaction()

        await cog.handle_tooth_press(interaction, 6)

        games.find_one_and_update.assert_not_called()
        interaction.response.edit_message.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_tooth_press_uses_full_cas_guard_and_updates_panel(self) -> None:
        game = persisted_game(active=True)
        updated = press_tooth(game, user_id=10, tooth_number=6, now=NOW)
        games = MagicMock()
        games.find_one.side_effect = [game, game]
        games.find_one_and_update.return_value = updated
        cog = make_cog(games)
        interaction = make_interaction(user_id=10)

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.handle_tooth_press(interaction, 6)

        cas_filter = games.find_one_and_update.call_args.args[0]
        self.assertEqual(cas_filter["_id"], game["_id"])
        self.assertEqual(cas_filter["revision"], game["revision"])
        self.assertEqual(cas_filter["status"], "active")
        self.assertEqual(cas_filter["panel_channel_id"], 200)
        self.assertEqual(cas_filter["panel_message_id"], 300)
        self.assertEqual(cas_filter["current_player_id"], 10)
        self.assertEqual(cas_filter["pressed_teeth"], {"$ne": 6})

        interaction.response.send_message.assert_not_awaited()
        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIs(kwargs["allowed_mentions"], NO_MENTIONS)
        self.assertEqual(kwargs["embed"].title, "🐊 Cá sấu nha sĩ · Ván #7")
        tooth_six = kwargs["view"].children[5]
        self.assertTrue(tooth_six.disabled)
        self.assertEqual(updated["current_player_id"], 20)
        kwargs["view"].stop()

    async def test_cas_conflict_cannot_apply_a_duplicate_tooth_press(self) -> None:
        game = persisted_game(active=True)
        games = MagicMock()
        games.find_one.side_effect = [game, game]
        games.find_one_and_update.return_value = None
        cog = make_cog(games)
        interaction = make_interaction(user_id=10)

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.handle_tooth_press(interaction, 6)

        games.find_one_and_update.assert_called_once()
        interaction.response.edit_message.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_committed_press_reports_recovery_when_panel_edit_fails(
        self,
    ) -> None:
        game = persisted_game(active=True)
        updated = press_tooth(game, user_id=10, tooth_number=6, now=NOW)
        games = MagicMock()
        games.find_one.side_effect = [game, game]
        games.find_one_and_update.return_value = updated
        cog = make_cog(games)
        interaction = make_interaction(user_id=10)
        http_response = SimpleNamespace(status=500, reason="Server Error")
        interaction.response.edit_message.side_effect = discord.HTTPException(
            http_response,
            "edit failed",
        )

        with (
            patch(
                "cogs.minigames.crocodile_dentist.crocodile._utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.minigames.crocodile_dentist.crocodile.logger.exception"
            ),
        ):
            await cog.handle_tooth_press(interaction, 6)

        games.find_one_and_update.assert_called_once()
        interaction.response.send_message.assert_awaited_once()
        recovery = interaction.response.send_message.await_args.args[0]
        self.assertIn("đã được lưu", recovery)
        self.assertIn("crocodile fire 7", recovery)
        self.assertTrue(
            interaction.response.send_message.await_args.kwargs["ephemeral"]
        )

    async def test_duplicate_invitation_answer_is_rejected_without_write(self) -> None:
        game = persisted_game(
            responses={"10": "accepted", "20": "accepted", "30": "pending"}
        )
        games = MagicMock()
        games.find_one.side_effect = [game, game]
        cog = make_cog(games)
        interaction = make_interaction(user_id=20)

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.handle_invitation_response(interaction, accepted=False)

        games.find_one_and_update.assert_not_called()
        interaction.response.edit_message.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        self.assertIn(
            "đã trả lời",
            interaction.response.send_message.await_args.args[0],
        )

    async def test_exact_invitation_deadline_is_settled_before_response(self) -> None:
        game = persisted_game(invitation_expires_at=NOW)
        settled = resolve_pending_game(game, NOW)
        games = MagicMock()
        games.find_one.side_effect = [game, game]
        games.find_one_and_update.return_value = settled
        cog = make_cog(games)
        interaction = make_interaction(user_id=20)

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.handle_invitation_response(interaction, accepted=True)

        interaction.response.send_message.assert_not_awaited()
        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIn("đã bị hủy", kwargs["embed"].title)
        self.assertTrue(all(item.disabled for item in kwargs["view"].children))
        self.assertEqual(
            games.find_one_and_update.call_args.args[1]["$set"]["status"],
            "cancelled",
        )
        kwargs["view"].stop()


class TestCrocodileCommands(unittest.IsolatedAsyncioTestCase):
    def challenge_context(self) -> tuple[SimpleNamespace, list[SimpleNamespace]]:
        invitees = [member(20), member(30)]
        members_by_id = {item.id: item for item in invitees}
        guild = SimpleNamespace(
            id=100,
            get_member=lambda user_id: members_by_id.get(user_id),
            fetch_member=AsyncMock(),
        )
        ctx = make_context()
        ctx.guild = guild
        ctx.send.return_value = SimpleNamespace(id=300, delete=AsyncMock())
        return ctx, invitees

    async def test_challenge_persists_hidden_tooth_and_binds_initial_panel(
        self,
    ) -> None:
        games = MagicMock()
        games.insert_one.return_value = SimpleNamespace(inserted_id="mongo-game-7")
        games.find_one_and_update.return_value = {"status": "pending"}
        cog = make_cog(games)
        cog.counters.find_one_and_update.return_value = {"value": 7}
        ctx, _invitees = self.challenge_context()

        with (
            patch(
                "cogs.minigames.crocodile_dentist.crocodile._utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.minigames.crocodile_dentist.crocodile.random.randint",
                return_value=9,
            ),
        ):
            await cog.crocodile_challenge.callback(
                cog,
                ctx,
                arguments="20 <@20> <@!30>",
            )

        inserted = games.insert_one.call_args.args[0]
        self.assertEqual(inserted["game_id"], 7)
        self.assertEqual(inserted["status"], "pending")
        self.assertEqual(inserted["revision"], 0)
        self.assertEqual(inserted["host_id"], 10)
        self.assertEqual(inserted["original_player_ids"], [10, 20, 30])
        self.assertEqual(inserted["participant_ids"], [10, 20, 30])
        self.assertEqual(inserted["player_ids"], [10])
        self.assertEqual(
            inserted["responses"],
            {"10": "accepted", "20": "pending", "30": "pending"},
        )
        self.assertEqual(inserted["tooth_count"], 20)
        self.assertEqual(inserted["dangerous_tooth"], 9)
        self.assertEqual(inserted["invitation_expires_at"], NOW + timedelta(minutes=5))

        ctx.send.assert_awaited_once()
        send_kwargs = ctx.send.await_args.kwargs
        self.assertEqual(send_kwargs["content"], "<@20> <@30>")
        self.assertTrue(send_kwargs["allowed_mentions"].users)
        self.assertFalse(send_kwargs["allowed_mentions"].everyone)

        bind_filter, bind_update = games.find_one_and_update.call_args.args[:2]
        self.assertEqual(
            bind_filter,
            {
                "_id": "mongo-game-7",
                "status": "pending",
                "revision": 0,
                "panel_message_id": None,
            },
        )
        self.assertEqual(bind_update["$set"]["panel_channel_id"], 200)
        self.assertEqual(bind_update["$set"]["panel_message_id"], 300)
        self.assertEqual(bind_update["$inc"], {"revision": 1})
        send_kwargs["view"].stop()

    async def test_initial_send_failure_cancels_persisted_game_for_recovery(
        self,
    ) -> None:
        games = MagicMock()
        games.insert_one.return_value = SimpleNamespace(inserted_id="mongo-game-7")
        cog = make_cog(games)
        cog.counters.find_one_and_update.return_value = {"value": 7}
        cog._cancel_setup_game = MagicMock()
        ctx, _invitees = self.challenge_context()
        http_response = SimpleNamespace(status=500, reason="Server Error")
        ctx.send.side_effect = discord.HTTPException(http_response, "send failed")

        with (
            patch(
                "cogs.minigames.crocodile_dentist.crocodile._utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.minigames.crocodile_dentist.crocodile.logger.exception"
            ),
        ):
            await cog.crocodile_challenge.callback(
                cog,
                ctx,
                arguments="<@20> <@30>",
            )

        cog._cancel_setup_game.assert_called_once_with(
            "mongo-game-7",
            "setup_send_failed",
        )
        games.find_one_and_update.assert_not_called()

    async def test_panel_bind_conflict_cancels_game_and_deletes_orphan(self) -> None:
        games = MagicMock()
        games.insert_one.return_value = SimpleNamespace(inserted_id="mongo-game-7")
        games.find_one_and_update.return_value = None
        games.find_one.return_value = {
            "_id": "mongo-game-7",
            "status": "pending",
            "revision": 0,
            "panel_message_id": None,
        }
        cog = make_cog(games)
        cog.counters.find_one_and_update.return_value = {"value": 7}
        cog._cancel_setup_game = MagicMock()
        ctx, _invitees = self.challenge_context()
        sent_message = ctx.send.return_value

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.crocodile_challenge.callback(
                cog,
                ctx,
                arguments="<@20> <@30>",
            )

        cog._cancel_setup_game.assert_called_once_with(
            "mongo-game-7",
            "setup_bind_failed",
        )
        sent_message.delete.assert_awaited_once()

    async def test_setup_bind_race_preserves_newer_canonical_state(self) -> None:
        games = MagicMock()
        games.insert_one.return_value = SimpleNamespace(inserted_id="mongo-game-7")
        games.find_one_and_update.return_value = None
        games.find_one.return_value = {
            "_id": "mongo-game-7",
            "status": "pending",
            "revision": 1,
            "panel_channel_id": 777,
            "panel_message_id": 888,
        }
        cog = make_cog(games)
        cog.counters.find_one_and_update.return_value = {"value": 7}
        cog._cancel_setup_game = MagicMock()
        ctx, _invitees = self.challenge_context()
        sent_message = ctx.send.return_value

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.crocodile_challenge.callback(
                cog,
                ctx,
                arguments="<@20> <@30>",
            )

        cog._cancel_setup_game.assert_not_called()
        sent_message.delete.assert_awaited_once()
        ctx.reply.assert_not_awaited()

    async def test_status_query_is_scoped_bounded_and_excludes_terminal_games(
        self,
    ) -> None:
        documents = [
            persisted_game(
                active=True,
                _id=f"game-{number}",
                game_id=number,
                panel_message_id=300 + number,
            )
            for number in range(12, 0, -1)
        ]
        first_cursor = FakeCursor(documents)
        second_cursor = FakeCursor(documents)
        games = MagicMock()
        games.find.side_effect = [first_cursor, second_cursor]
        cog = make_cog(games)
        ctx = make_context()

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.crocodile.callback(cog, ctx)

        expected_query = {
            "guild_id": 100,
            "participant_ids": 10,
            "status": {"$in": ["pending", "active"]},
        }
        self.assertEqual(games.find.call_args_list[0].args[0], expected_query)
        self.assertEqual(games.find.call_args_list[1].args[0], expected_query)
        self.assertEqual(first_cursor.limit_calls, [10])
        self.assertEqual(second_cursor.limit_calls, [10])
        embed = ctx.reply.await_args.kwargs["embed"]
        self.assertEqual(len(embed.fields), 10)
        self.assertIn("Ván #12", embed.fields[0].name)
        self.assertIs(ctx.reply.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

    async def test_fire_is_host_only_and_same_guild(self) -> None:
        games = MagicMock()
        games.find_one.return_value = persisted_game(active=True)
        cog = make_cog(games)
        ctx = make_context(author_id=999)

        await cog.crocodile_fire.callback(cog, ctx, game_id=7)

        games.find_one.assert_called_once_with({"guild_id": 100, "game_id": 7})
        games.find_one_and_update.assert_not_called()
        ctx.send.assert_not_awaited()
        self.assertIn("Chỉ chủ phòng", ctx.reply.await_args.args[0])

    async def test_fire_preserves_game_state_and_disables_previous_panel(
        self,
    ) -> None:
        game = persisted_game(
            active=True,
            pressed_teeth=[1, 4],
            current_turn=1,
            current_player_id=20,
        )
        new_message = SimpleNamespace(id=888, delete=AsyncMock())
        games = MagicMock()
        games.find_one.side_effect = [game, game]
        cog = make_cog(games)
        cog._edit_saved_panel = AsyncMock()
        ctx = make_context(channel_id=777)
        ctx.send.return_value = new_message

        def persist(_query, update, **_kwargs):
            persisted = copy.deepcopy(game)
            persisted.update(update["$set"])
            return persisted

        games.find_one_and_update.side_effect = persist
        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW + timedelta(hours=2),
        ):
            await cog.crocodile_fire.callback(cog, ctx, game_id=7)

        ctx.reply.assert_not_awaited()
        new_message.delete.assert_not_awaited()
        ctx.send.assert_awaited_once()
        self.assertIs(ctx.send.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        cas_filter, update = games.find_one_and_update.call_args.args[:2]
        self.assertEqual(cas_filter["panel_channel_id"], 200)
        self.assertEqual(cas_filter["panel_message_id"], 300)
        replacement = update["$set"]
        self.assertEqual(replacement["panel_channel_id"], 777)
        self.assertEqual(replacement["panel_message_id"], 888)
        self.assertEqual(replacement["dangerous_tooth"], 7)
        self.assertEqual(replacement["pressed_teeth"], [1, 4])
        self.assertEqual(replacement["current_turn"], 1)
        self.assertEqual(replacement["current_player_id"], 20)
        self.assertEqual(replacement["activity_expires_at"], game["activity_expires_at"])
        cog._edit_saved_panel.assert_awaited_once_with(game, disabled=True)
        ctx.send.await_args.kwargs["view"].stop()

    async def test_fire_cas_loss_deletes_replacement_panel(self) -> None:
        game = persisted_game(active=True)
        new_message = SimpleNamespace(id=888, delete=AsyncMock())
        games = MagicMock()
        games.find_one.side_effect = [game, game]
        games.find_one_and_update.return_value = None
        cog = make_cog(games)
        cog._edit_saved_panel = AsyncMock()
        ctx = make_context(channel_id=777)
        ctx.send.return_value = new_message

        with patch(
            "cogs.minigames.crocodile_dentist.crocodile._utcnow",
            return_value=NOW,
        ):
            await cog.crocodile_fire.callback(cog, ctx, game_id=7)

        new_message.delete.assert_awaited_once()
        cog._edit_saved_panel.assert_not_awaited()
        ctx.reply.assert_awaited_once()
        self.assertIn("trạng thái ván vừa thay đổi", ctx.reply.await_args.args[0])

    async def test_fire_lost_ack_keeps_new_authoritative_panel(self) -> None:
        game = persisted_game(active=True)
        authoritative = copy.deepcopy(game)
        authoritative.update(
            {
                "revision": game["revision"] + 1,
                "panel_channel_id": 777,
                "panel_message_id": 888,
            }
        )
        new_message = SimpleNamespace(id=888, delete=AsyncMock())
        games = MagicMock()
        games.find_one.side_effect = [game, game, authoritative]
        games.find_one_and_update.side_effect = PyMongoError("lost acknowledgement")
        cog = make_cog(games)
        cog._edit_saved_panel = AsyncMock()
        ctx = make_context(channel_id=777)
        ctx.send.return_value = new_message

        with (
            patch(
                "cogs.minigames.crocodile_dentist.crocodile._utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.minigames.crocodile_dentist.crocodile.logger.exception"
            ),
        ):
            await cog.crocodile_fire.callback(cog, ctx, game_id=7)

        new_message.delete.assert_not_awaited()
        ctx.reply.assert_not_awaited()
        cog._edit_saved_panel.assert_awaited_once_with(game, disabled=True)

    async def test_fire_rejects_terminal_game_without_creating_panel(self) -> None:
        games = MagicMock()
        games.find_one.return_value = persisted_game(
            active=True,
            status="finished",
            current_turn=None,
            current_player_id=None,
            activity_expires_at=None,
        )
        cog = make_cog(games)
        ctx = make_context()

        await cog.crocodile_fire.callback(cog, ctx, game_id=7)

        ctx.send.assert_not_awaited()
        games.find_one_and_update.assert_not_called()
        self.assertIn("đã kết thúc", ctx.reply.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
