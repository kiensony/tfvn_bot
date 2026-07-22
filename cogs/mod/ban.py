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


class BanCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ban", help="Ban một thành viên khỏi server.")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    async def ban_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Không có lý do cụ thể",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send("Bạn không thể ban chính mình, server owner, hoặc role ngang/cao hơn.")
            return
        reason = clean_case_reason(reason)
        try:
            await member.ban(reason=format_audit_reason(reason, ctx.author))
        except discord.Forbidden:
            await ctx.send(
                "Bot không thể ban thành viên này. Hãy kiểm tra quyền và thứ bậc role."
            )
            return
        except discord.HTTPException:
            logger.exception("Discord rejected ban target=%s", member.id)
            await ctx.send("Discord từ chối thao tác ban. Vui lòng thử lại.")
            return

        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="ban",
            reason=reason,
        )
        await ctx.send(
            f"Đã ban {member.mention}. Lý do: {reason}{case_suffix(case_number)}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ban_member.error
    async def ban_member_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền ban thành viên.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Không tìm thấy thành viên cần ban.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BanCog(bot))
