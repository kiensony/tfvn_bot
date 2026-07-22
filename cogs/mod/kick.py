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


class KickCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="kick", help="Kick một thành viên khỏi server.")
    @commands.guild_only()
    @commands.has_guild_permissions(kick_members=True)
    async def kick_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Không có lý do cụ thể",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send("Bạn không thể kick chính mình, server owner, hoặc role ngang/cao hơn.")
            return
        reason = clean_case_reason(reason)
        try:
            await member.kick(reason=format_audit_reason(reason, ctx.author))
        except discord.Forbidden:
            await ctx.send(
                "Bot không thể kick thành viên này. Hãy kiểm tra quyền và thứ bậc role."
            )
            return
        except discord.HTTPException:
            logger.exception("Discord rejected kick target=%s", member.id)
            await ctx.send("Discord từ chối thao tác kick. Vui lòng thử lại.")
            return

        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="kick",
            reason=reason,
        )
        await ctx.send(
            f"Đã kick {member.mention}. Lý do: {reason}{case_suffix(case_number)}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @kick_member.error
    async def kick_member_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền kick thành viên.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Không tìm thấy thành viên cần kick.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(KickCog(bot))
