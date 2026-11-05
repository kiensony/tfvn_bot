"""Shared creation locks, saved-role lookup, and anchor placement."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from weakref import WeakValueDictionary

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)


def personal_role_lock(
    bot: commands.Bot, guild_id: int, user_id: int
) -> asyncio.Lock:
    """Serialize booster and shop creation for one member on this bot."""
    return personal_resource_lock(bot, guild_id, user_id, "custom_role")


def personal_resource_lock(
    bot: commands.Bot, guild_id: int, user_id: int, item_type: str
) -> asyncio.Lock:
    """Share a resource lock across purchases, editors, boosters, and cleanup."""
    locks = getattr(bot, "_personal_resource_locks", None)
    if locks is None:
        locks = WeakValueDictionary()
        bot._personal_resource_locks = locks
    key = (guild_id, user_id, item_type)
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


async def resolve_personal_role(
    guild: discord.Guild, record: dict[str, Any] | None
) -> discord.Role | None:
    """Verify a saved role through HTTP on cache misses; propagate API errors."""
    if not record:
        return None
    try:
        role_id = int(record.get("role_id"))
    except (TypeError, ValueError):
        return None
    role = guild.get_role(role_id)
    if role is not None:
        return role
    roles = await guild.fetch_roles()
    return next((role for role in roles if role.id == role_id), None)


ANCHOR_ROLE_VARIABLE = "BOOSTER_CUSTOM_ROLE_ANCHOR_ID"


def anchor_role_id(bot: commands.Bot) -> int | None:
    """Return the configured anchor role id, or None when it is unset."""
    variables = getattr(bot, "global_vars", None)
    if not isinstance(variables, dict):
        return None
    value = variables.get(ANCHOR_ROLE_VARIABLE)
    if value is None or value == "":
        return None
    try:
        role_id = int(str(value).strip())
    except (TypeError, ValueError):
        logger.warning("%s is not a role id.", ANCHOR_ROLE_VARIABLE)
        return None
    if role_id <= 0:
        logger.warning("%s is not a role id.", ANCHOR_ROLE_VARIABLE)
        return None
    return role_id


async def configured_anchor_role(
    bot: commands.Bot,
    guild: discord.Guild,
) -> discord.Role | None:
    """Resolve the anchor from cache, then from Discord when the cache misses."""
    role_id = anchor_role_id(bot)
    if role_id is None:
        return None
    role = guild.get_role(role_id)
    if role is not None:
        return role
    try:
        roles = await guild.fetch_roles()
    except discord.HTTPException:
        logger.exception(
            "Could not read anchor role %s in guild %s.",
            role_id,
            guild.id,
        )
        return None
    role = next((item for item in roles if item.id == role_id), None)
    if role is None:
        logger.warning(
            "%s %s was not found in guild %s.",
            ANCHOR_ROLE_VARIABLE,
            role_id,
            guild.id,
        )
    return role


def _anchor_blocks_placement(
    anchor: discord.Role,
    bot_top_role: discord.Role | None,
) -> bool:
    return (
        anchor.is_default()
        or anchor.managed
        or anchor.position <= 0
        or (bot_top_role is not None and anchor >= bot_top_role)
    )


async def place_role_under_anchor(
    guild: discord.Guild,
    role: discord.Role,
    anchor: discord.Role,
    *,
    bot_top_role: discord.Role | None,
    reason: str,
) -> str | None:
    """Place a personal role directly under the configured anchor.

    ``Role.edit(position=anchor.position - 1)`` rebuilds the move from the
    guild cache. The role returned by ``create_role`` is not in that cache,
    and it shares position 1, so discord.py's range zip omits it and the role
    stays at the bottom. Equal positions sort by id, with the higher id lower
    in the list, so a new role given the anchor's position sits directly under
    it without shifting roles the bot cannot move.
    """
    if _anchor_blocks_placement(anchor, bot_top_role):
        return "Không thể đặt role dưới anchor đã cấu hình."

    anchor_position = anchor.position
    try:
        fresh_roles = await guild.fetch_roles()
    except discord.HTTPException:
        logger.exception(
            "Could not read roles before placing role %s in guild %s.",
            role.id,
            guild.id,
        )
        fresh_roles = []

    if fresh_roles:
        fresh_anchor = next(
            (item for item in fresh_roles if item.id == anchor.id),
            None,
        )
        if fresh_anchor is None or _anchor_blocks_placement(
            fresh_anchor,
            bot_top_role,
        ):
            return "Không thể đặt role dưới anchor đã cấu hình."
        anchor_position = fresh_anchor.position

    try:
        await guild.edit_role_positions({role: anchor_position}, reason=reason)
    except discord.Forbidden:
        return "Bot không có quyền đặt thứ bậc role."
    except discord.HTTPException:
        logger.exception(
            "Could not position role %s under anchor %s in guild %s.",
            role.id,
            anchor.id,
            guild.id,
        )
        return "Không thể cập nhật thứ bậc role."

    role.position = anchor_position
    return None


async def resolve_personal_room(
    guild: discord.Guild, record: dict[str, Any] | None
) -> discord.VoiceChannel | None:
    """Resolve a tracked voice room, preserving uncertain or invalid records."""
    if not record:
        return None
    channel_id = int(record["channel_id"])
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.NotFound:
            return None
    if not isinstance(channel, discord.VoiceChannel):
        raise ValueError("Saved custom room is not a voice channel")
    return channel
