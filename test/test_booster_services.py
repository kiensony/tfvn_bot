import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import discord

from cogs.booster._role_colors import GRADIENT_FALLBACK_WARNING, RoleColorSpec
from cogs.booster.create_custom_role import BoosterCustomRoleCog
from cogs.booster.create_custom_room import BoosterCustomRoomCog
from cogs.booster.update_custom_role import BoosterCustomRoleUpdateCog
from test_shop import FakeCollection


BOT_ID = 9000


class FakeRole:
    def __init__(
        self,
        guild,
        role_id: int,
        name: str,
        position: int,
        *,
        managed: bool = False,
        default: bool = False,
    ) -> None:
        self.guild = guild
        self.id = role_id
        self.name = name
        self.position = position
        self.managed = managed
        self.mention = f"<@&{role_id}>"
        self.colour = discord.Color(0x010203)
        self.secondary_colour = None
        self._default = default
        self.edit = AsyncMock()
        self.delete = AsyncMock()

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
        roles: list[FakeRole] | None = None,
        booster: bool = True,
        manage_roles: bool = False,
        manage_channels: bool = False,
    ) -> None:
        self.id = member_id
        self.top_role = top_role
        self.roles = [] if roles is None else roles
        self.premium_since = object() if booster else None
        self.mention = f"<@{member_id}>"
        self.guild_permissions = SimpleNamespace(
            manage_roles=manage_roles,
            manage_channels=manage_channels,
        )
        self.add_roles = AsyncMock()

    def __str__(self) -> str:
        return f"member-{self.id}"


class FakeGuild:
    def __init__(self, guild_id: int = 123) -> None:
        self.id = guild_id
        self.default_role = object()
        self._members = {}
        self._roles = {}
        self._channels = {}
        self.create_role = AsyncMock()
        self.create_voice_channel = AsyncMock()
        self.fetch_roles = AsyncMock(return_value=[])
        self.fetch_channel = AsyncMock()

    def get_member(self, member_id: int):
        return self._members.get(member_id)

    def get_role(self, role_id: int):
        return self._roles.get(role_id)

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class FakeCategory:
    def __init__(self, overwrites: dict | None = None) -> None:
        self.overwrites = {} if overwrites is None else overwrites

    def permissions_for(self, _member) -> SimpleNamespace:
        return SimpleNamespace(manage_channels=True, manage_roles=True)


class FakeVoiceChannel:
    def __init__(self, guild: FakeGuild, channel_id: int, name: str) -> None:
        self.guild = guild
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"
        self.delete = AsyncMock()


def make_bot(collection_name: str, collection: MagicMock) -> SimpleNamespace:
    shop_roles = MagicMock()
    shop_roles.find_one.return_value = None
    return SimpleNamespace(
        db={
            collection_name: collection,
            "shop_custom_roles": shop_roles,
            "shop_custom_rooms": FakeCollection(),
            "shop_inventory": FakeCollection(),
            "shop_migrations": FakeCollection(),
        },
        user=SimpleNamespace(id=BOT_ID),
        global_vars={},
    )


def make_role_fixture(collection: MagicMock):
    guild = FakeGuild()
    bot_top_role = FakeRole(guild, 901, "bot-top", 100)
    bot_member = FakeMember(
        BOT_ID,
        bot_top_role,
        manage_roles=True,
        manage_channels=True,
    )
    guild._members[BOT_ID] = bot_member

    existing_role = FakeRole(guild, 10, "existing", 5)
    booster_top_role = FakeRole(guild, 11, "booster-top", 20)
    booster = FakeMember(42, booster_top_role, roles=[existing_role])
    bot = make_bot("booster_custom_roles", collection)
    return bot, guild, bot_member, booster, existing_role


def make_room_fixture(collection: MagicMock):
    guild = FakeGuild()
    bot_top_role = FakeRole(guild, 902, "bot-top", 100)
    bot_member = FakeMember(
        BOT_ID,
        bot_top_role,
        manage_roles=True,
        manage_channels=True,
    )
    guild._members[BOT_ID] = bot_member
    booster = FakeMember(42, FakeRole(guild, 12, "booster-top", 20))
    bot = make_bot("booster_custom_rooms", collection)
    return bot, guild, bot_member, booster


class TestBoosterRoleServices(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_custom_role_arguments_create_add_and_persist(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, existing_role = make_role_fixture(
            collection
        )
        created_role = FakeRole(guild, 77, "Legacy Violet", 10)
        guild.create_role.return_value = created_role
        cog = BoosterCustomRoleCog(bot)
        ctx = SimpleNamespace(
            guild=guild,
            author=booster,
            message=SimpleNamespace(attachments=[]),
            clean_prefix="!tf ",
            send=AsyncMock(),
        )

        await cog.custom_role.callback(
            cog,
            ctx,
            "#7A4DFF",
            role_name="Legacy Violet",
        )

        create_kwargs = guild.create_role.await_args.kwargs
        self.assertEqual(create_kwargs["name"], "Legacy Violet")
        self.assertEqual(create_kwargs["colour"].value, 0x7A4DFF)
        self.assertFalse(create_kwargs["mentionable"])
        booster.add_roles.assert_awaited_once_with(
            created_role,
            reason="Assign booster custom role",
        )
        self.assertEqual(booster.roles, [existing_role])

        record_filter, update = collection.update_one.call_args.args
        self.assertEqual(record_filter, {"guild_id": 123, "user_id": 42})
        self.assertEqual(update["$set"]["role_id"], 77)
        self.assertEqual(update["$set"]["role_name"], "Legacy Violet")
        self.assertEqual(update["$set"]["primary_color"], 0x7A4DFF)
        self.assertIsNone(update["$set"]["secondary_color"])
        self.assertIn("created_at", update["$setOnInsert"])
        self.assertTrue(collection.update_one.call_args.kwargs["upsert"])
        ctx.send.assert_awaited_once()

    async def test_new_booster_role_takes_the_anchor_position(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        anchor = FakeRole(guild, 55, "anchor", 4)
        fresh_anchor = FakeRole(guild, 55, "anchor", 8)
        guild._roles[anchor.id] = anchor
        guild.fetch_roles.return_value = [fresh_anchor]
        guild.edit_role_positions = AsyncMock()
        bot.global_vars["BOOSTER_CUSTOM_ROLE_ANCHOR_ID"] = str(anchor.id)
        created_role = FakeRole(guild, 77, "Under Anchor", 1)
        guild.create_role.return_value = created_role
        cog = BoosterCustomRoleCog(bot)

        result = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(discord.Color(0x7A4DFF)),
            role_name="Under Anchor",
            icon_attachment=None,
        )

        self.assertTrue(result.completed)
        self.assertNotIn("anchor", result.message)
        self.assertNotIn("thứ bậc", result.message)
        created_role.edit.assert_not_awaited()
        guild.edit_role_positions.assert_awaited_once_with(
            {created_role: fresh_anchor.position},
            reason="Place booster custom role under anchor",
        )
        self.assertEqual(created_role.position, fresh_anchor.position)
        booster.add_roles.assert_awaited_once_with(
            created_role,
            reason="Assign booster custom role",
        )

    async def test_uncached_anchor_is_fetched_before_placement(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        anchor = FakeRole(guild, 55, "anchor", 8)
        guild.fetch_roles.return_value = [anchor]
        guild.edit_role_positions = AsyncMock()
        bot.global_vars["BOOSTER_CUSTOM_ROLE_ANCHOR_ID"] = " 55 "
        created_role = FakeRole(guild, 77, "Fetched Anchor", 1)
        guild.create_role.return_value = created_role
        cog = BoosterCustomRoleCog(bot)

        result = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(discord.Color(0x7A4DFF)),
            role_name="Fetched Anchor",
            icon_attachment=None,
        )

        self.assertTrue(result.completed)
        self.assertNotIn("anchor", result.message)
        self.assertEqual(guild.fetch_roles.await_count, 2)
        guild.edit_role_positions.assert_awaited_once_with(
            {created_role: anchor.position},
            reason="Place booster custom role under anchor",
        )

    async def test_anchor_above_the_bot_does_not_move_the_role(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        anchor = FakeRole(guild, 55, "anchor", 150)
        guild._roles[anchor.id] = anchor
        guild.edit_role_positions = AsyncMock()
        bot.global_vars["BOOSTER_CUSTOM_ROLE_ANCHOR_ID"] = anchor.id
        created_role = FakeRole(guild, 77, "Too High", 1)
        guild.create_role.return_value = created_role
        cog = BoosterCustomRoleCog(bot)

        result = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(discord.Color(0x7A4DFF)),
            role_name="Too High",
            icon_attachment=None,
        )

        self.assertTrue(result.completed)
        self.assertIn("Không thể đặt role dưới anchor", result.message)
        guild.fetch_roles.assert_not_awaited()
        guild.edit_role_positions.assert_not_awaited()
        created_role.edit.assert_not_awaited()

    async def test_recorded_role_is_verified_before_a_second_create(self) -> None:
        collection = MagicMock()
        collection.find_one.side_effect = [
            None,
            {"guild_id": 123, "user_id": 42, "role_id": 77},
        ]
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        created_role = FakeRole(guild, 77, "Fresh role", 10)
        guild.create_role.return_value = created_role
        guild.fetch_roles.return_value = [created_role]
        cog = BoosterCustomRoleCog(bot)
        color_spec = RoleColorSpec(discord.Color(0x7A4DFF))

        created = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=color_spec,
            role_name="Fresh role",
            icon_attachment=None,
        )
        duplicate = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=color_spec,
            role_name="Fresh role",
            icon_attachment=None,
        )

        self.assertTrue(created.completed)
        self.assertFalse(duplicate.completed)
        self.assertIn("đã có custom role", duplicate.message)
        guild.fetch_roles.assert_awaited_once_with()
        self.assertEqual(guild.create_role.await_count, 1)

    async def test_shop_custom_role_blocks_booster_create(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        shop_role = FakeRole(guild, 80, "Shop Pink", 6)
        guild._roles[shop_role.id] = shop_role
        bot.db["shop_custom_roles"].find_one.return_value = {
            "guild_id": guild.id,
            "user_id": booster.id,
            "role_id": shop_role.id,
        }
        cog = BoosterCustomRoleCog(bot)
        result = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(discord.Color(0xFF66B3)),
            role_name="Booster Pink",
            icon_attachment=None,
        )
        self.assertFalse(result.completed)
        self.assertIn("cửa hàng", result.message)
        guild.create_role.assert_not_awaited()

    async def test_update_custom_role_edits_existing_role_and_record(self) -> None:
        collection = MagicMock()
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        owned_role = FakeRole(guild, 88, "Old name", 10)
        guild._roles[owned_role.id] = owned_role
        booster.roles.append(owned_role)
        collection.find_one.return_value = {
            "guild_id": guild.id,
            "user_id": booster.id,
            "role_id": owned_role.id,
        }
        cog = BoosterCustomRoleUpdateCog(bot)
        color_spec = RoleColorSpec(
            primary=discord.Color(0x123456),
            secondary=discord.Color(0xABCDEF),
        )

        result = await cog._update_custom_role(
            guild=guild,
            member=booster,
            color_spec=color_spec,
            role_name="  New gradient  ",
            icon_attachment=None,
        )

        self.assertTrue(result.completed)
        edit_kwargs = owned_role.edit.await_args.kwargs
        self.assertEqual(edit_kwargs["name"], "New gradient")
        self.assertEqual(edit_kwargs["colour"].value, 0x123456)
        self.assertEqual(edit_kwargs["secondary_color"].value, 0xABCDEF)
        self.assertIsNone(edit_kwargs["tertiary_color"])
        self.assertNotIn("display_icon", edit_kwargs)
        booster.add_roles.assert_not_awaited()

        record_filter, update = collection.update_one.call_args.args
        self.assertEqual(record_filter, {"guild_id": 123, "user_id": 42})
        self.assertEqual(update["$set"]["role_name"], "New gradient")
        self.assertEqual(update["$set"]["primary_color"], 0x123456)
        self.assertEqual(update["$set"]["secondary_color"], 0xABCDEF)

    async def test_gradient_without_perk_creates_the_primary_color(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        guild.features = []
        created_role = FakeRole(guild, 77, "Solid", 1)
        guild.create_role.return_value = created_role
        cog = BoosterCustomRoleCog(bot)

        result = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(
                primary=discord.Color(0x112233),
                secondary=discord.Color(0xAABBCC),
            ),
            role_name="Solid",
            icon_attachment=None,
        )

        self.assertTrue(result.completed)
        self.assertIn(GRADIENT_FALLBACK_WARNING, result.message)
        create_kwargs = guild.create_role.await_args.kwargs
        self.assertEqual(create_kwargs["colour"].value, 0x112233)
        self.assertNotIn("secondary_color", create_kwargs)
        self.assertNotIn("display_icon", create_kwargs)
        self.assertEqual(guild.create_role.await_count, 1)
        _record_filter, update = collection.update_one.call_args.args
        self.assertEqual(update["$set"]["secondary_color"], None)

    async def test_rejected_gradient_is_retried_as_the_primary_color(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        guild.features = ["ENHANCED_ROLE_COLORS"]
        created_role = FakeRole(guild, 78, "Retried", 1)

        async def create_role(**kwargs):
            if "secondary_color" in kwargs:
                response = SimpleNamespace(status=403, reason="Forbidden")
                raise discord.Forbidden(response, {"code": 50013})
            return created_role

        guild.create_role.side_effect = create_role
        cog = BoosterCustomRoleCog(bot)

        result = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(
                primary=discord.Color(0x010203),
                secondary=discord.Color(0x040506),
            ),
            role_name="Retried",
            icon_attachment=None,
        )

        self.assertTrue(result.completed)
        self.assertIn(GRADIENT_FALLBACK_WARNING, result.message)
        self.assertEqual(guild.create_role.await_count, 2)
        self.assertNotIn("secondary_color", guild.create_role.await_args.kwargs)
        _record_filter, update = collection.update_one.call_args.args
        self.assertEqual(update["$set"]["primary_color"], 0x010203)
        self.assertIsNone(update["$set"]["secondary_color"])

    async def test_solid_color_forbidden_is_not_retried(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        guild.features = ["ENHANCED_ROLE_COLORS"]
        response = SimpleNamespace(status=403, reason="Forbidden")
        guild.create_role.side_effect = discord.Forbidden(response, {"code": 50013})
        cog = BoosterCustomRoleCog(bot)

        result = await cog._create_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(discord.Color(0x112233)),
            role_name="Blocked",
            icon_attachment=None,
        )

        self.assertFalse(result.completed)
        self.assertIn("quyền tạo role", result.message)
        self.assertEqual(guild.create_role.await_count, 1)

    async def test_update_without_gradient_perk_keeps_the_primary_color(self) -> None:
        collection = MagicMock()
        bot, guild, _bot_member, booster, _existing_role = make_role_fixture(
            collection
        )
        guild.features = []
        owned_role = FakeRole(guild, 88, "Old name", 10)
        guild._roles[owned_role.id] = owned_role
        booster.roles.append(owned_role)
        collection.find_one.return_value = {
            "guild_id": guild.id,
            "user_id": booster.id,
            "role_id": owned_role.id,
        }
        cog = BoosterCustomRoleUpdateCog(bot)

        result = await cog._update_custom_role(
            guild=guild,
            member=booster,
            color_spec=RoleColorSpec(
                primary=discord.Color(0x123456),
                secondary=discord.Color(0xABCDEF),
            ),
            role_name="Flat",
            icon_attachment=None,
        )

        self.assertTrue(result.completed)
        self.assertIn(GRADIENT_FALLBACK_WARNING, result.message)
        edit_kwargs = owned_role.edit.await_args.kwargs
        self.assertEqual(edit_kwargs["colour"].value, 0x123456)
        self.assertNotIn("secondary_color", edit_kwargs)
        self.assertEqual(owned_role.edit.await_count, 1)
        _record_filter, update = collection.update_one.call_args.args
        self.assertIsNone(update["$set"]["secondary_color"])


class TestBoosterRoomServices(unittest.IsolatedAsyncioTestCase):
    async def test_create_room_is_private_excludes_inherited_role_allows_and_persists(
        self,
    ) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        bot, guild, bot_member, booster = make_room_fixture(collection)
        staff_role = object()
        everyone_source = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
        )
        staff_source = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
        )
        category = FakeCategory(
            {
                guild.default_role: everyone_source,
                staff_role: staff_source,
            }
        )
        channel = FakeVoiceChannel(guild, 501, "Quiet corner")
        guild.create_voice_channel.return_value = channel
        cog = BoosterCustomRoomCog(bot)
        cog._get_category = Mock(return_value=category)

        result = await cog._create_custom_room(
            guild=guild,
            member=booster,
            room_name="  Quiet corner  ",
            user_limit=7,
        )

        self.assertTrue(result.completed)
        create_kwargs = guild.create_voice_channel.await_args.kwargs
        self.assertEqual(create_kwargs["name"], "Quiet corner")
        self.assertIs(create_kwargs["category"], category)
        self.assertEqual(create_kwargs["user_limit"], 7)
        overwrites = create_kwargs["overwrites"]
        self.assertFalse(overwrites[guild.default_role].view_channel)
        self.assertFalse(overwrites[guild.default_role].connect)
        self.assertTrue(overwrites[booster].view_channel)
        self.assertTrue(overwrites[booster].connect)
        self.assertTrue(overwrites[booster].manage_channels)
        self.assertTrue(overwrites[booster].manage_permissions)
        self.assertTrue(overwrites[booster].move_members)
        self.assertTrue(overwrites[bot_member].view_channel)
        self.assertTrue(overwrites[bot_member].connect)
        self.assertTrue(overwrites[bot_member].manage_channels)
        self.assertTrue(overwrites[bot_member].manage_permissions)
        self.assertNotIn(staff_role, overwrites)
        self.assertTrue(everyone_source.view_channel)
        self.assertTrue(everyone_source.connect)
        self.assertTrue(staff_source.view_channel)
        self.assertTrue(staff_source.connect)

        record_filter, update = collection.update_one.call_args.args
        self.assertEqual(record_filter, {"guild_id": 123, "user_id": 42})
        self.assertEqual(update["$set"]["channel_id"], 501)
        self.assertEqual(update["$set"]["channel_name"], "Quiet corner")
        self.assertEqual(update["$set"]["user_limit"], 7)
        self.assertIn("created_at", update["$setOnInsert"])
        self.assertTrue(collection.update_one.call_args.kwargs["upsert"])

    async def test_recorded_room_cache_miss_is_verified_before_create(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = {
            "guild_id": 123,
            "user_id": 42,
            "channel_id": 701,
        }
        bot, guild, _bot_member, booster = make_room_fixture(collection)
        category = FakeCategory()
        existing = FakeVoiceChannel(guild, 701, "Existing room")
        guild.fetch_channel.return_value = existing
        cog = BoosterCustomRoomCog(bot)
        cog._get_category = Mock(return_value=category)

        result = await cog._create_custom_room(
            guild=guild,
            member=booster,
            room_name="Duplicate room",
            user_limit=3,
        )

        self.assertFalse(result.completed)
        self.assertIn("đã có custom room", result.message)
        guild.fetch_channel.assert_awaited_once_with(701)
        guild.create_voice_channel.assert_not_awaited()

    async def test_database_failure_rolls_back_room_and_allows_retry(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        collection.update_one.side_effect = [RuntimeError("database down"), None]
        bot, guild, _bot_member, booster = make_room_fixture(collection)
        category = FakeCategory()
        first_channel = FakeVoiceChannel(guild, 601, "Retry room")
        second_channel = FakeVoiceChannel(guild, 602, "Retry room")
        guild.create_voice_channel.side_effect = [first_channel, second_channel]
        cog = BoosterCustomRoomCog(bot)
        cog._get_category = Mock(return_value=category)

        with self.assertLogs(
            "cogs.booster.create_custom_room",
            level="ERROR",
        ):
            failed = await cog._create_custom_room(
                guild=guild,
                member=booster,
                room_name="Retry room",
                user_limit=3,
            )
        retried = await cog._create_custom_room(
            guild=guild,
            member=booster,
            room_name="Retry room",
            user_limit=3,
        )

        self.assertFalse(failed.completed)
        self.assertIn("thử lại", failed.message)
        first_channel.delete.assert_awaited_once_with(
            reason="Rollback untracked booster custom room"
        )
        self.assertTrue(retried.completed)
        second_channel.delete.assert_not_awaited()
        self.assertEqual(guild.create_voice_channel.await_count, 2)
        self.assertEqual(collection.update_one.call_count, 2)

    async def test_failed_database_and_rollback_warns_not_to_retry(self) -> None:
        collection = MagicMock()
        collection.find_one.return_value = None
        collection.update_one.side_effect = RuntimeError("database down")
        bot, guild, _bot_member, booster = make_room_fixture(collection)
        category = FakeCategory()
        channel = FakeVoiceChannel(guild, 603, "Orphan room")
        response = SimpleNamespace(status=403, reason="Forbidden")
        channel.delete.side_effect = discord.Forbidden(
            response,
            {"code": 50013, "message": "Missing Permissions"},
        )
        guild.create_voice_channel.return_value = channel
        cog = BoosterCustomRoomCog(bot)
        cog._get_category = Mock(return_value=category)

        with self.assertLogs(
            "cogs.booster.create_custom_room",
            level="ERROR",
        ):
            result = await cog._create_custom_room(
                guild=guild,
                member=booster,
                room_name="Orphan room",
                user_limit=3,
            )

        self.assertTrue(result.completed)
        self.assertIn("không thử lại", result.message)
        self.assertIn("`603`", result.message)
        channel.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
