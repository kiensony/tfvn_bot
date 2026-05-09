import discord
from discord.ext import commands


class BoosterCustomRoomCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.collection = self.db["booster_custom_rooms"]

    def _is_booster(self, member: discord.Member) -> bool:
        return member.premium_since is not None

    def _get_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if not self.bot.user:
            return None
        return guild.get_member(self.bot.user.id)

    async def _ensure_manage_channels(self, ctx: commands.Context) -> bool:
        bot_member = self._get_bot_member(ctx.guild)
        if not bot_member or not bot_member.guild_permissions.manage_channels:
            await ctx.send("Bot đang thiếu quyền Manage Channels.")
            return False
        return True

    def _get_category(self, guild: discord.Guild) -> discord.CategoryChannel | None:
        if not hasattr(self.bot, "global_vars"):
            return None

        category_value = self.bot.global_vars.get(
            "BOOSTER_CUSTOM_VOICE_CATEGORY_ID"
        )
        if not category_value:
            return None

        try:
            category_id = int(category_value)
        except (TypeError, ValueError):
            return None

        channel = guild.get_channel(category_id)
        if isinstance(channel, discord.CategoryChannel):
            return channel
        return None

    @commands.command(
        name="custom_room",
        aliases=["booster_room"],
        help="Tạo custom voice room cho booster.",
    )
    async def custom_room(self, ctx: commands.Context, *, room_name: str):
        if not ctx.guild:
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return

        if not self._is_booster(ctx.author):
            await ctx.send("Bạn cần là Booster để dùng lệnh này.")
            return

        room_name = room_name.strip()
        if not room_name:
            await ctx.send("Tên phòng không hợp lệ.")
            return

        if len(room_name) > 100:
            await ctx.send("Tên phòng tối đa 100 ký tự.")
            return

        if not await self._ensure_manage_channels(ctx):
            return

        category = self._get_category(ctx.guild)
        if not category:
            await ctx.send("Chưa cài đặt category cho custom room.")
            return

        record = self.collection.find_one(
            {"guild_id": ctx.guild.id, "user_id": ctx.author.id}
        )
        if record:
            existing = ctx.guild.get_channel(record.get("channel_id"))
            if isinstance(existing, discord.VoiceChannel):
                await ctx.send("Bạn đã có custom room. Hãy dùng lệnh update.")
                return

        try:
            channel = await ctx.guild.create_voice_channel(
                name=room_name,
                category=category,
                reason=f"Booster custom room for {ctx.author} ({ctx.author.id})",
            )

            await channel.set_permissions(
                ctx.author,
                manage_channels=True,
                manage_permissions=True,
                move_members=True,
                connect=True,
                view_channel=True,
            )

            now = discord.utils.utcnow()
            self.collection.update_one(
                {"guild_id": ctx.guild.id, "user_id": ctx.author.id},
                {
                    "$set": {
                        "channel_id": channel.id,
                        "channel_name": channel.name,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )

            await ctx.send(
                f"Đã tạo custom room {channel.mention} cho {ctx.author.mention}."
            )
        except discord.Forbidden:
            await ctx.send(
                "Bot không có quyền tạo phòng. Vui lòng kiểm tra quyền."
            )
        except discord.HTTPException:
            await ctx.send("Đã xảy ra lỗi khi tạo custom room.")


async def setup(bot: commands.Bot):
    await bot.add_cog(BoosterCustomRoomCog(bot))
