from datetime import timedelta
import logging

import discord
from discord.ext import commands

from cogs.mod._case_helpers import (
    can_moderate,
    case_suffix,
    clean_case_reason,
    format_audit_reason,
    record_case,
)


logger = logging.getLogger(__name__)
MAX_TIMEOUT_MINUTES = 28 * 24 * 60


class TimeoutCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="timeout", help="Timeout thành viên theo số phút.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration_minutes: int,
        *,
        reason: str = "Không có lý do cụ thể",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send(
                "Bạn không thể timeout chính mình, server owner, hoặc role ngang/cao hơn."
            )
            return
        if duration_minutes <= 0 or duration_minutes > MAX_TIMEOUT_MINUTES:
            await ctx.send(
                f"Thời gian timeout phải từ 1 đến {MAX_TIMEOUT_MINUTES:,} phút."
            )
            return
        reason = clean_case_reason(reason)

        until = discord.utils.utcnow() + timedelta(minutes=duration_minutes)
        try:
            await member.timeout(
                until, reason=format_audit_reason(reason, ctx.author)
            )
        except discord.Forbidden:
            await ctx.send("Bot không có quyền timeout thành viên này.")
            return
        except discord.HTTPException:
            logger.exception("Discord rejected timeout target=%s", member.id)
            await ctx.send("Discord từ chối thao tác timeout. Vui lòng thử lại.")
            return

        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="timeout",
            reason=reason,
            duration_seconds=duration_minutes * 60,
        )
        await ctx.send(
            f"Đã timeout {member.mention} trong {duration_minutes:,} phút. "
            f"Lý do: {reason}{case_suffix(case_number)}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="untimeout", help="Gỡ timeout cho thành viên.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    async def untimeout(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Moderator removed timeout",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send(
                "Bạn không thể gỡ timeout cho server owner hoặc role ngang/cao hơn."
            )
            return
        reason = clean_case_reason(reason)
        try:
            await member.timeout(
                None, reason=format_audit_reason(reason, ctx.author)
            )
        except discord.Forbidden:
            await ctx.send("Bot không có quyền gỡ timeout cho thành viên này.")
            return
        except discord.HTTPException:
            logger.exception("Discord rejected untimeout target=%s", member.id)
            await ctx.send("Discord từ chối thao tác. Vui lòng thử lại.")
            return

        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="untimeout",
            reason=reason,
        )
        await ctx.send(
            f"Đã gỡ timeout cho {member.mention}{case_suffix(case_number)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @timeout.error
    @untimeout.error
    async def timeout_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền timeout thành viên.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Thành viên hoặc thời gian timeout không hợp lệ.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimeoutCog(bot))
