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


class SoftbanCog(commands.Cog):
    """Temporarily jail a member by replacing their roles with Handcuffed."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.old_roles = self.db["old_roles"]

    def save_old_roles(
        self, guild_id: int, member_id: int, role_ids: list[int]
    ) -> None:
        query = {"guild_id": guild_id, "member_id": member_id}
        if self.old_roles.find_one(query) is None:
            legacy = self.old_roles.find_one(
                {"member_id": member_id, "guild_id": {"$exists": False}}
            )
            if legacy is not None:
                query = {"_id": legacy["_id"]}
        self.old_roles.update_one(
            query,
            {
                "$set": {
                    "guild_id": guild_id,
                    "member_id": member_id,
                    "old_roles": role_ids,
                    "updated_at": discord.utils.utcnow(),
                }
            },
            upsert=True,
        )

    def get_old_roles(self, guild_id: int, member_id: int) -> list[int] | None:
        document = self.old_roles.find_one(
            {"guild_id": guild_id, "member_id": member_id}
        )
        if document is None:
            document = self.old_roles.find_one(
                {"member_id": member_id, "guild_id": {"$exists": False}}
            )
            if document is not None:
                self.old_roles.update_one(
                    {"_id": document["_id"]},
                    {
                        "$set": {
                            "guild_id": guild_id,
                            "updated_at": discord.utils.utcnow(),
                        }
                    },
                )
        return document.get("old_roles", []) if document else None

    @staticmethod
    async def _restore_roles_after_failure(
        member: discord.Member,
        roles: list[discord.Role],
        moderator: discord.Member,
    ) -> bool:
        if not roles:
            return True
        try:
            await member.add_roles(
                *roles,
                reason=f"Rollback failed softban requested by {moderator}",
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to roll back softban target=%s", member.id)
            return False
        return True

    @commands.command(name="softban", help="Nhốt member bằng role Handcuffed.")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    async def softban_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Không có lý do cụ thể",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send(
                "Bạn không thể nhốt chính mình, server owner, hoặc role ngang/cao hơn."
            )
            return
        reason = clean_case_reason(reason)

        handcuffed_role = discord.utils.get(ctx.guild.roles, name="Handcuffed")
        if handcuffed_role is None:
            await ctx.send("Role Handcuffed không tồn tại.")
            return
        if handcuffed_role >= ctx.guild.me.top_role:
            await ctx.send("Role Handcuffed phải thấp hơn role cao nhất của bot.")
            return
        if handcuffed_role in member.roles:
            await ctx.send(f"{member.mention} đã bị nhốt.")
            return

        original_roles = [
            role for role in member.roles if not role.is_default() and not role.managed
        ]
        old_role_ids = [role.id for role in original_roles]
        self.save_old_roles(ctx.guild.id, member.id, old_role_ids)
        try:
            await member.edit(
                roles=[], reason=format_audit_reason(reason, ctx.author)
            )
        except discord.Forbidden:
            self.old_roles.delete_one(
                {"guild_id": ctx.guild.id, "member_id": member.id}
            )
            await ctx.send("Bot không thể thay đổi role của thành viên này.")
            return
        except discord.HTTPException:
            self.old_roles.delete_one(
                {"guild_id": ctx.guild.id, "member_id": member.id}
            )
            logger.exception("Discord rejected softban target=%s", member.id)
            await ctx.send("Discord từ chối thao tác softban.")
            return

        try:
            await member.add_roles(
                handcuffed_role,
                reason=format_audit_reason(reason, ctx.author),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to add Handcuffed role target=%s", member.id)
            restorable = [
                role for role in original_roles if role < ctx.guild.me.top_role
            ]
            restored = await self._restore_roles_after_failure(
                member, restorable, ctx.author
            )
            if restored:
                self.old_roles.delete_one(
                    {"guild_id": ctx.guild.id, "member_id": member.id}
                )
                await ctx.send(
                    "Không thể gán role Handcuffed; các role cũ đã được khôi phục."
                )
            else:
                await ctx.send(
                    "Không thể gán role Handcuffed hoặc tự khôi phục role. "
                    "Dữ liệu role cũ vẫn được giữ để moderator chạy unsoftban."
                )
            return

        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="softban",
            reason=reason,
        )
        await ctx.send(
            f"Đã nhốt {member.mention}. Lý do: {reason}{case_suffix(case_number)}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="unsoftban", help="Khôi phục role trước softban.")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    async def unsoftban_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Moderator removed softban",
    ) -> None:
        if not can_moderate(ctx.author, member):
            await ctx.send(
                "Bạn không thể thả server owner hoặc member có role ngang/cao hơn."
            )
            return
        reason = clean_case_reason(reason)
        old_role_ids = self.get_old_roles(ctx.guild.id, member.id)
        if old_role_ids is None:
            await ctx.send(f"Không tìm thấy role cũ cho {member.mention}.")
            return

        handcuffed_role = discord.utils.get(ctx.guild.roles, name="Handcuffed")
        restorable = []
        for role_id in old_role_ids:
            role = ctx.guild.get_role(role_id)
            if role and not role.managed and role < ctx.guild.me.top_role:
                restorable.append(role)

        try:
            if handcuffed_role and handcuffed_role in member.roles:
                await member.remove_roles(
                    handcuffed_role,
                    reason=format_audit_reason(reason, ctx.author),
                )
            if restorable:
                await member.add_roles(
                    *restorable,
                    reason=format_audit_reason(reason, ctx.author),
                )
        except discord.Forbidden:
            await ctx.send("Bot không thể khôi phục role của thành viên này.")
            return
        except discord.HTTPException:
            logger.exception("Discord rejected unsoftban target=%s", member.id)
            await ctx.send("Discord từ chối thao tác unsoftban.")
            return

        self.old_roles.delete_one(
            {"guild_id": ctx.guild.id, "member_id": member.id}
        )
        case_number = await record_case(
            self.bot,
            guild=ctx.guild,
            target=member,
            moderator=ctx.author,
            action="unsoftban",
            reason=reason,
        )
        await ctx.send(
            f"Đã thả {member.mention} và khôi phục role{case_suffix(case_number)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @softban_member.error
    @unsoftban_member.error
    async def softban_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền sử dụng lệnh này.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Không tìm thấy thành viên.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SoftbanCog(bot))
