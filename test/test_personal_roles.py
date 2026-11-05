import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.roles._personal_roles import (
    configured_anchor_role,
    place_role_under_anchor,
)


def http_error(status: int) -> discord.HTTPException:
    response = SimpleNamespace(status=status, reason="error")
    if status == 403:
        return discord.Forbidden(response, {"message": "Missing Permissions"})
    return discord.HTTPException(response, {"message": "failed"})


class FakeRole:
    def __init__(
        self,
        role_id: int,
        position: int,
        *,
        managed: bool = False,
        default: bool = False,
    ) -> None:
        self.id = role_id
        self.position = position
        self.managed = managed
        self._default = default

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


class ConfiguredAnchorRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_cache_reads_the_role_from_discord(self) -> None:
        anchor = FakeRole(55, 8)
        guild = SimpleNamespace(
            id=123,
            get_role=lambda role_id: None,
            fetch_roles=AsyncMock(return_value=[anchor]),
        )
        bot = SimpleNamespace(
            global_vars={"BOOSTER_CUSTOM_ROLE_ANCHOR_ID": " 55 "}
        )

        role = await configured_anchor_role(bot, guild)

        self.assertIs(role, anchor)
        guild.fetch_roles.assert_awaited_once_with()

    async def test_invalid_or_missing_anchor_is_ignored(self) -> None:
        guild = SimpleNamespace(
            id=123,
            get_role=lambda role_id: None,
            fetch_roles=AsyncMock(return_value=[]),
        )
        unset = SimpleNamespace(global_vars={})
        self.assertIsNone(await configured_anchor_role(unset, guild))
        guild.fetch_roles.assert_not_awaited()

        invalid = SimpleNamespace(global_vars={"BOOSTER_CUSTOM_ROLE_ANCHOR_ID": "nope"})
        with self.assertLogs("cogs.roles._personal_roles", level="WARNING"):
            self.assertIsNone(await configured_anchor_role(invalid, guild))

        missing = SimpleNamespace(global_vars={"BOOSTER_CUSTOM_ROLE_ANCHOR_ID": 55})
        with self.assertLogs("cogs.roles._personal_roles", level="WARNING"):
            self.assertIsNone(await configured_anchor_role(missing, guild))
        guild.fetch_roles.assert_awaited_once_with()


class PlaceRoleUnderAnchorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.guild = SimpleNamespace(
            id=123,
            fetch_roles=AsyncMock(),
            edit_role_positions=AsyncMock(),
        )
        self.role = FakeRole(77, 1)
        self.anchor = FakeRole(55, 8)
        self.bot_top = FakeRole(90, 20)

    async def test_uses_fresh_anchor_position_instead_of_role_edit(self) -> None:
        fresh_anchor = FakeRole(55, 11)
        self.guild.fetch_roles.return_value = [fresh_anchor, FakeRole(10, 1)]

        warning = await place_role_under_anchor(
            self.guild,
            self.role,
            self.anchor,
            bot_top_role=self.bot_top,
            reason="Place booster custom role under anchor",
        )

        self.assertIsNone(warning)
        self.guild.edit_role_positions.assert_awaited_once_with(
            {self.role: 11},
            reason="Place booster custom role under anchor",
        )
        self.assertEqual(self.role.position, 11)

    async def test_cached_position_is_used_when_role_lookup_fails(self) -> None:
        self.guild.fetch_roles.side_effect = http_error(500)

        with self.assertLogs("cogs.roles._personal_roles", level="ERROR"):
            warning = await place_role_under_anchor(
                self.guild,
                self.role,
                self.anchor,
                bot_top_role=self.bot_top,
                reason="place",
            )

        self.assertIsNone(warning)
        self.guild.edit_role_positions.assert_awaited_once_with(
            {self.role: 8},
            reason="place",
        )

    async def test_missing_fresh_anchor_does_not_move_the_role(self) -> None:
        self.guild.fetch_roles.return_value = [FakeRole(10, 1)]

        warning = await place_role_under_anchor(
            self.guild,
            self.role,
            self.anchor,
            bot_top_role=self.bot_top,
            reason="place",
        )

        self.assertEqual(warning, "Không thể đặt role dưới anchor đã cấu hình.")
        self.guild.edit_role_positions.assert_not_awaited()
        self.assertEqual(self.role.position, 1)

    async def test_managed_default_and_higher_anchors_are_rejected(self) -> None:
        cases = (
            FakeRole(55, 8, managed=True),
            FakeRole(55, 0, default=True),
            FakeRole(55, 20),
            FakeRole(55, 25),
        )
        for anchor in cases:
            self.guild.fetch_roles.reset_mock()
            self.guild.edit_role_positions.reset_mock()
            warning = await place_role_under_anchor(
                self.guild,
                self.role,
                anchor,
                bot_top_role=self.bot_top,
                reason="place",
            )
            self.assertEqual(
                warning,
                "Không thể đặt role dưới anchor đã cấu hình.",
            )
            self.guild.fetch_roles.assert_not_awaited()
            self.guild.edit_role_positions.assert_not_awaited()

    async def test_forbidden_and_http_errors_warn(self) -> None:
        self.guild.fetch_roles.return_value = [self.anchor]
        self.guild.edit_role_positions.side_effect = http_error(403)
        warning = await place_role_under_anchor(
            self.guild,
            self.role,
            self.anchor,
            bot_top_role=None,
            reason="place",
        )
        self.assertEqual(warning, "Bot không có quyền đặt thứ bậc role.")

        self.guild.edit_role_positions.side_effect = http_error(400)
        with self.assertLogs("cogs.roles._personal_roles", level="ERROR"):
            warning = await place_role_under_anchor(
                self.guild,
                self.role,
                self.anchor,
                bot_top_role=None,
                reason="place",
            )
        self.assertEqual(warning, "Không thể cập nhật thứ bậc role.")
