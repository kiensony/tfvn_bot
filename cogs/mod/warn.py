from datetime import timezone

import discord
from discord.ext import commands
from pymongo import DESCENDING

from cogs.mod._case_helpers import (
    can_moderate,
    case_suffix,
    clean_case_reason,
    record_case,
)


class WarnCommandCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    @commands.command(name="warn", help="Cảnh cáo một thành viên.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def warn_user(
        self,
        ctx: commands.Context,
        user: discord.Member,
        *,
        reason: str = "Không có lý do cụ thể",
    ) -> None:
        if not can_moderate(ctx.author, user):
            await ctx.send(
                "Bạn không thể cảnh cáo chính mình, server owner, hoặc role ngang/cao hơn.",
                delete_after=10,
            )
            return
        reason = clean_case_reason(reason)

        now = discord.utils.utcnow()
        self.db["warnings"].insert_one(
            {
                "guild_id": ctx.guild.id,
                "user_id": user.id,
                "user_name": str(user),
                "moderator_id": ctx.author.id,
                "moderator_name": str(ctx.author),
                "reason": reason,
                "timestamp": now,
            }
        )
        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=user,
            moderator=ctx.author,
            action="warn",
            reason=reason,
        )

        embed = discord.Embed(
            title=f"Cảnh cáo người dùng{case_suffix(case_number)}",
            color=discord.Color.orange(),
            timestamp=now,
        )
        embed.add_field(
            name="Thành viên", value=f"{user.mention} ({user.id})", inline=False
        )
        embed.add_field(
            name="Moderator",
            value=f"{ctx.author.mention} ({ctx.author.id})",
            inline=False,
        )
        embed.add_field(name="Lý do", value=reason[:1024], inline=False)
        await ctx.send(embed=embed, delete_after=60)

    @warn_user.error
    async def warn_user_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền cảnh cáo thành viên.", delete_after=10)
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Vui lòng cung cấp thành viên cần cảnh cáo.", delete_after=10)
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Không tìm thấy thành viên cần cảnh cáo.", delete_after=10)
            return
        raise error

    @commands.command(name="check_warn", help="Xem cảnh cáo gần đây.")
    @commands.guild_only()
    async def check_warnings(
        self,
        ctx: commands.Context,
        user: discord.Member | None = None,
    ) -> None:
        target = user or ctx.author
        warnings = list(
            self.db["warnings"]
            .find(
                {
                    "user_id": target.id,
                    "$or": [
                        {"guild_id": ctx.guild.id},
                        {"guild_id": {"$exists": False}},
                    ],
                }
            )
            .sort("timestamp", DESCENDING)
            .limit(10)
        )

        embed = discord.Embed(
            title="Lịch sử cảnh cáo",
            color=discord.Color.blue(),
        )
        embed.set_author(name=str(target), icon_url=target.display_avatar.url)
        if not warnings:
            embed.description = "Người dùng này chưa bị cảnh cáo."
        else:
            for warning in warnings:
                timestamp = warning.get("timestamp")
                if timestamp and timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                when = (
                    discord.utils.format_dt(timestamp, style="f")
                    if timestamp
                    else "Không xác định"
                )
                embed.add_field(
                    name=when,
                    value=(
                        f"Lý do: {warning.get('reason', 'Không có lý do')}\n"
                        f"Mod: {warning.get('moderator_name', 'Không xác định')}"
                    )[:1024],
                    inline=False,
                )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarnCommandCog(bot))
