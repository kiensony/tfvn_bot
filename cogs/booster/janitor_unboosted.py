import logging
from datetime import time, timezone

import discord
from discord.ext import commands, tasks


class BoosterJanitorUnboostedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.role_collection = self.db["booster_custom_roles"]
        self.room_collection = self.db["booster_custom_rooms"]
        self.log = logging.getLogger(__name__)
        self.janitor.start()

    def cog_unload(self):
        self.janitor.cancel()

    async def _get_member(
        self, guild: discord.Guild, user_id: int
    ) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    def _is_booster(self, member: discord.Member | None) -> bool:
        return bool(member and member.premium_since)

    def _delete_role_record(self, record: dict) -> None:
        if "_id" in record:
            self.role_collection.delete_one({"_id": record["_id"]})
            return
        self.role_collection.delete_one(
            {
                "guild_id": record.get("guild_id"),
                "user_id": record.get("user_id"),
            }
        )

    def _delete_room_record(self, record: dict) -> None:
        if "_id" in record:
            self.room_collection.delete_one({"_id": record["_id"]})
            return
        self.room_collection.delete_one(
            {
                "guild_id": record.get("guild_id"),
                "user_id": record.get("user_id"),
            }
        )

    async def _cleanup_role_record(
        self, guild: discord.Guild, record: dict
    ) -> None:
        role_id = record.get("role_id")
        role = guild.get_role(role_id) if role_id else None
        if role:
            try:
                await role.delete(reason="Booster custom role cleanup")
            except discord.Forbidden:
                self.log.warning(
                    "Missing permission to delete role %s in guild %s",
                    role.id,
                    guild.id,
                )
                return
            except discord.HTTPException:
                self.log.warning(
                    "Failed to delete role %s in guild %s",
                    role.id,
                    guild.id,
                )
                return

        self._delete_role_record(record)

    async def _cleanup_room_record(
        self, guild: discord.Guild, record: dict
    ) -> None:
        channel_id = record.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel and isinstance(channel, discord.VoiceChannel):
            try:
                await channel.delete(reason="Booster custom room cleanup")
            except discord.Forbidden:
                self.log.warning(
                    "Missing permission to delete channel %s in guild %s",
                    channel.id,
                    guild.id,
                )
                return
            except discord.HTTPException:
                self.log.warning(
                    "Failed to delete channel %s in guild %s",
                    channel.id,
                    guild.id,
                )
                return

        self._delete_room_record(record)

    async def _clean_roles(self) -> None:
        for record in self.role_collection.find():
            guild_id = record.get("guild_id")
            user_id = record.get("user_id")
            if not guild_id or not user_id:
                self._delete_role_record(record)
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                self._delete_role_record(record)
                continue

            member = await self._get_member(guild, user_id)
            if self._is_booster(member):
                continue

            await self._cleanup_role_record(guild, record)

    async def _clean_rooms(self) -> None:
        for record in self.room_collection.find():
            guild_id = record.get("guild_id")
            user_id = record.get("user_id")
            if not guild_id or not user_id:
                self._delete_room_record(record)
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                self._delete_room_record(record)
                continue

            member = await self._get_member(guild, user_id)
            if self._is_booster(member):
                continue

            await self._cleanup_room_record(guild, record)

    @tasks.loop(time=time(hour=0, minute=0, tzinfo=timezone.utc))
    async def janitor(self) -> None:
        now = discord.utils.utcnow()
        if now.weekday() != 6:
            return
        await self._clean_roles()
        await self._clean_rooms()

    @janitor.before_loop
    async def before_janitor(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(BoosterJanitorUnboostedCog(bot))
