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


class MuteCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="mute", help="Gán role Muted cho thành viên.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def mute_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Không có lý do cụ thể",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send("Bạn không thể mute chính mình, server owner, hoặc role ngang/cao hơn.")
            return
        reason = clean_case_reason(reason)
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if mute_role is None:
            await ctx.send("Role Muted không tồn tại.")
            return
        if mute_role >= ctx.guild.me.top_role:
            await ctx.send("Role Muted phải thấp hơn role cao nhất của bot.")
            return

        try:
            await member.add_roles(
                mute_role, reason=format_audit_reason(reason, ctx.author)
            )
        except discord.Forbidden:
            await ctx.send("Bot không có quyền gán role Muted.")
            return
        except discord.HTTPException:
            logger.exception("Discord rejected mute target=%s", member.id)
            await ctx.send("Discord từ chối thao tác mute.")
            return

        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="mute",
            reason=reason,
        )
        await ctx.send(
            f"Đã mute {member.mention}{case_suffix(case_number)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="unmute", help="Gỡ role Muted.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def unmute_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Moderator removed mute",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send("Bạn không thể unmute chính mình, server owner, hoặc role ngang/cao hơn.")
            return
        reason = clean_case_reason(reason)
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if mute_role is None:
            await ctx.send("Role Muted không tồn tại.")
            return
        if mute_role not in member.roles:
            await ctx.send(f"{member.mention} không bị mute.")
            return

        try:
            await member.remove_roles(
                mute_role, reason=format_audit_reason(reason, ctx.author)
            )
        except discord.Forbidden:
            await ctx.send("Bot không có quyền gỡ role Muted.")
            return
        except discord.HTTPException:
            logger.exception("Discord rejected unmute target=%s", member.id)
            await ctx.send("Discord từ chối thao tác unmute.")
            return

        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="unmute",
            reason=reason,
        )
        await ctx.send(
            f"Đã unmute {member.mention}{case_suffix(case_number)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MuteCog(bot))
