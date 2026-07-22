import logging

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import PyMongoError

from cogs.mod._case_helpers import clean_case_reason, normalize_case_status


logger = logging.getLogger(__name__)

CASES_COLLECTION = "moderation_cases"
CONFIG_COLLECTION = "moderation_config"
COUNTERS_COLLECTION = "feature_counters"
MAX_HISTORY_RESULTS = 10

ACTION_LABELS = {
    "ban": "Ban",
    "kick": "Kick",
    "mute": "Mute",
    "softban": "Softban",
    "timeout": "Timeout",
    "unmute": "Unmute",
    "unsoftban": "Unsoftban",
    "untimeout": "Untimeout",
    "warn": "Warn",
}


class ModerationCasesCog(commands.Cog):
    """Numbered moderation audit trail shared by moderation cogs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.cases = self.db[CASES_COLLECTION]
        self.config = self.db[CONFIG_COLLECTION]
        self.counters = self.db[COUNTERS_COLLECTION]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        try:
            self.cases.create_index(
                [("guild_id", ASCENDING), ("case_number", ASCENDING)],
                unique=True,
                name="guild_case_unique",
            )
            self.cases.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("target_id", ASCENDING),
                    ("created_at", DESCENDING),
                ],
                name="guild_target_history",
            )
            self.config.create_index(
                [("guild_id", ASCENDING)], unique=True, name="guild_config_unique"
            )
        except PyMongoError:
            logger.exception("Failed to create moderation case indexes")

    def _next_case_number(self, guild_id: int) -> int:
        counter = self.counters.find_one_and_update(
            {"_id": f"moderation_case:{guild_id}"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(counter["value"])

    async def create_case(
        self,
        *,
        guild: discord.Guild,
        target: discord.abc.User,
        moderator: discord.abc.User,
        action: str,
        reason: str,
        duration_seconds: int | None = None,
    ) -> int:
        case_number = self._next_case_number(guild.id)
        now = discord.utils.utcnow()
        document = {
            "guild_id": guild.id,
            "case_number": case_number,
            "action": action.lower(),
            "target_id": target.id,
            "target_name": str(target),
            "moderator_id": moderator.id,
            "moderator_name": str(moderator),
            "reason": clean_case_reason(reason),
            "duration_seconds": duration_seconds,
            "status": "open",
            "edit_history": [],
            "created_at": now,
            "updated_at": now,
        }
        self.cases.insert_one(document)
        await self._send_case_log(guild, document)
        return case_number

    async def _send_case_log(self, guild: discord.Guild, case: dict) -> None:
        config = self.config.find_one({"guild_id": guild.id}) or {}
        channel_id = config.get("log_channel_id")
        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(
                embed=self._case_embed(case),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to send moderation log guild=%s case=%s",
                guild.id,
                case["case_number"],
            )

    @staticmethod
    def _case_embed(case: dict) -> discord.Embed:
        action = ACTION_LABELS.get(case["action"], case["action"].title())
        status = case.get("status", "open")
        embed = discord.Embed(
            title=f"Case #{case['case_number']} · {action}",
            color=discord.Color.orange(),
            timestamp=case.get("created_at"),
        )
        embed.add_field(
            name="Thành viên",
            value=f"<@{case['target_id']}> ({case['target_id']})",
            inline=False,
        )
        embed.add_field(
            name="Moderator",
            value=f"<@{case['moderator_id']}> ({case['moderator_id']})",
            inline=False,
        )
        embed.add_field(name="Lý do", value=case["reason"][:1024], inline=False)
        if case.get("duration_seconds"):
            embed.add_field(
                name="Thời hạn",
                value=f"{case['duration_seconds'] // 60:,} phút",
                inline=True,
            )
        embed.add_field(name="Trạng thái", value=status, inline=True)
        return embed

    @commands.group(
        name="case",
        invoke_without_command=True,
        help="Xem và quản lý moderation cases.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case(self, ctx: commands.Context) -> None:
        await ctx.send(
            "Dùng: case view <số>, case history <member>, case edit <số> <lý do>, "
            "case status <số> <open|resolved|appealed|void>."
        )

    @case.command(name="view")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case_view(self, ctx: commands.Context, case_number: int) -> None:
        case = self.cases.find_one(
            {"guild_id": ctx.guild.id, "case_number": case_number}
        )
        if case is None:
            await ctx.send("Không tìm thấy case đó.")
            return
        await ctx.send(
            embed=self._case_embed(case),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @case.command(name="history")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case_history(
        self,
        ctx: commands.Context,
        member: discord.Member,
        limit: int = MAX_HISTORY_RESULTS,
    ) -> None:
        limit = max(1, min(limit, MAX_HISTORY_RESULTS))
        cases = list(
            self.cases.find(
                {"guild_id": ctx.guild.id, "target_id": member.id}
            )
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        if not cases:
            await ctx.send(f"{member.mention} chưa có moderation case nào.")
            return

        lines = []
        for case in cases:
            action = ACTION_LABELS.get(case["action"], case["action"].title())
            reason = case["reason"]
            if len(reason) > 80:
                reason = reason[:77] + "..."
            lines.append(
                f"#{case['case_number']} · {action} · {case.get('status', 'open')} — {reason}"
            )
        embed = discord.Embed(
            title=f"Lịch sử moderation · {member}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @case.command(name="edit")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case_edit(
        self, ctx: commands.Context, case_number: int, *, reason: str
    ) -> None:
        cleaned_reason = clean_case_reason(reason)
        now = discord.utils.utcnow()
        existing = self.cases.find_one(
            {"guild_id": ctx.guild.id, "case_number": case_number}
        )
        if existing is None:
            await ctx.send("Không tìm thấy case đó.")
            return
        case = self.cases.find_one_and_update(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "reason": cleaned_reason,
                    "updated_at": now,
                    "updated_by": ctx.author.id,
                },
                "$push": {
                    "edit_history": {
                        "field": "reason",
                        "old_value": existing.get("reason"),
                        "new_value": cleaned_reason,
                        "editor_id": ctx.author.id,
                        "edited_at": now,
                    }
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        await ctx.send(f"Đã cập nhật lý do cho case #{case_number}.")
        await self._send_case_log(ctx.guild, case)

    @case.command(name="status")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case_status(
        self, ctx: commands.Context, case_number: int, status: str
    ) -> None:
        try:
            normalized_status = normalize_case_status(status)
        except ValueError:
            await ctx.send("Trạng thái phải là open, resolved, appealed hoặc void.")
            return

        existing = self.cases.find_one(
            {"guild_id": ctx.guild.id, "case_number": case_number}
        )
        if existing is None:
            await ctx.send("Không tìm thấy case đó.")
            return
        if existing.get("status", "open") == normalized_status:
            await ctx.send(
                f"Case #{case_number} đã ở trạng thái {normalized_status}."
            )
            return

        now = discord.utils.utcnow()
        case = self.cases.find_one_and_update(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "status": normalized_status,
                    "updated_at": now,
                    "updated_by": ctx.author.id,
                },
                "$push": {
                    "edit_history": {
                        "field": "status",
                        "old_value": existing.get("status", "open"),
                        "new_value": normalized_status,
                        "editor_id": ctx.author.id,
                        "edited_at": now,
                    }
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        await ctx.send(
            f"Đã chuyển case #{case_number} sang trạng thái {normalized_status}."
        )
        await self._send_case_log(ctx.guild, case)

    @case.command(name="log_channel")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def case_log_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or ctx.channel
        self.config.update_one(
            {"guild_id": ctx.guild.id},
            {
                "$set": {
                    "log_channel_id": target.id,
                    "updated_at": discord.utils.utcnow(),
                    "updated_by": ctx.author.id,
                }
            },
            upsert=True,
        )
        await ctx.send(f"Moderation cases sẽ được ghi vào {target.mention}.")

    @case.error
    async def case_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Manage Messages hoặc Manage Server.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Tham số case không hợp lệ.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCasesCog(bot))
