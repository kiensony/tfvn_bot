import logging

import discord
from discord.ext import commands
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.interaction._trigger_reply_helpers import (
    parse_rule_spec,
    select_matching_rule,
)


logger = logging.getLogger(__name__)

RULES_COLLECTION = "triggered_replies"
COUNTERS_COLLECTION = "feature_counters"
MAX_RULES_PER_GUILD = 100
RULES_PER_PAGE = 10


class TriggeredReplyCog(commands.Cog):
    """Persistent exact and substring replies configured per guild."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.rules = bot.db[RULES_COLLECTION]
        self.counters = bot.db[COUNTERS_COLLECTION]
        self.rules_by_guild: dict[int, list[dict]] = {}
        self._ensure_indexes()
        self._load_rules()

    def _ensure_indexes(self) -> None:
        try:
            self.rules.create_index(
                [("guild_id", ASCENDING), ("rule_id", ASCENDING)],
                unique=True,
                name="guild_triggered_reply_id_unique",
            )
            self.rules.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("mode", ASCENDING),
                    ("normalized_trigger", ASCENDING),
                ],
                unique=True,
                name="guild_triggered_reply_phrase_unique",
            )
        except PyMongoError:
            logger.exception("Failed to create triggered-reply indexes")

    def _load_rules(self) -> None:
        try:
            documents = self.rules.find({})
            for document in documents:
                guild_id = document.get("guild_id")
                if (
                    not isinstance(guild_id, int)
                    or not isinstance(document.get("rule_id"), int)
                    or document.get("mode") not in {"contains", "exact"}
                    or not isinstance(document.get("trigger"), str)
                    or not isinstance(document.get("normalized_trigger"), str)
                    or not document["normalized_trigger"]
                    or not isinstance(document.get("reply"), str)
                    or not document["reply"]
                ):
                    logger.warning(
                        "Skipping malformed triggered-reply document id=%s",
                        document.get("_id"),
                    )
                    continue
                self.rules_by_guild.setdefault(guild_id, []).append(document)
        except PyMongoError:
            logger.exception("Failed to load triggered replies")

        for rules in self.rules_by_guild.values():
            rules.sort(key=lambda rule: int(rule.get("rule_id", 0)))

    def _next_rule_id(self, guild_id: int) -> int:
        counter = self.counters.find_one_and_update(
            {"_id": f"triggered_reply:{guild_id}"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(counter["value"])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.author.bot
            or message.webhook_id is not None
            or message.guild is None
            or not message.content
        ):
            return

        context = await self.bot.get_context(message)
        if context.prefix is not None:
            return

        rule = select_matching_rule(
            message.content,
            self.rules_by_guild.get(message.guild.id, ()),
        )
        if rule is None:
            return

        try:
            await message.reply(
                str(rule["reply"]),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.NotFound:
            logger.info(
                "Triggered-reply source disappeared guild=%s rule=%s",
                message.guild.id,
                rule.get("rule_id"),
            )
        except discord.Forbidden:
            logger.warning(
                "Missing permission for triggered reply guild=%s rule=%s",
                message.guild.id,
                rule.get("rule_id"),
            )
        except discord.HTTPException:
            logger.exception(
                "Failed triggered reply guild=%s rule=%s",
                message.guild.id,
                rule.get("rule_id"),
            )

    @commands.group(
        name="triggerreply",
        aliases=["autoreply"],
        invoke_without_command=True,
        help="Quản lý câu trả lời tự động theo cụm từ.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def triggerreply(self, ctx: commands.Context) -> None:
        await ctx.send(
            "Dùng `triggerreply add <contains|exact> <cụm từ> | <phản hồi>`, "
            "`triggerreply update <ID> <contains|exact> <cụm từ> | <phản hồi>`, "
            "`triggerreply list`, hoặc `triggerreply remove <ID>`."
        )

    @triggerreply.command(name="add")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def triggerreply_add(
        self,
        ctx: commands.Context,
        mode: str,
        *,
        spec: str,
    ) -> None:
        guild_rules = self.rules_by_guild.setdefault(ctx.guild.id, [])
        if len(guild_rules) >= MAX_RULES_PER_GUILD:
            await ctx.send(f"Server chỉ được tạo tối đa {MAX_RULES_PER_GUILD} rule.")
            return

        try:
            parsed = parse_rule_spec(mode, spec)
        except ValueError as exc:
            await ctx.send(
                f"Rule không hợp lệ: {exc}. Dùng `<cụm từ> | <phản hồi>`."
            )
            return

        duplicate = any(
            rule.get("mode") == parsed["mode"]
            and rule.get("normalized_trigger") == parsed["normalized_trigger"]
            for rule in guild_rules
        )
        if duplicate:
            await ctx.send("Rule này đã tồn tại với cùng chế độ khớp.")
            return

        try:
            rule_id = self._next_rule_id(ctx.guild.id)
            document = {
                "guild_id": ctx.guild.id,
                "rule_id": rule_id,
                **parsed,
                "created_by": ctx.author.id,
                "created_at": discord.utils.utcnow(),
            }
            self.rules.insert_one(document)
        except DuplicateKeyError:
            await ctx.send("Rule này đã tồn tại với cùng chế độ khớp.")
            return
        except PyMongoError:
            logger.exception("Failed to create triggered reply guild=%s", ctx.guild.id)
            await ctx.send("Không thể lưu rule vào database lúc này.")
            return

        guild_rules.append(document)
        guild_rules.sort(key=lambda rule: int(rule["rule_id"]))
        displayed_trigger = parsed["trigger"]
        if len(displayed_trigger) > 100:
            displayed_trigger = displayed_trigger[:97] + "..."
        await ctx.send(
            f"Đã thêm rule #{rule_id} (`{parsed['mode']}`): "
            f"`{displayed_trigger}`.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @triggerreply.command(name="update", aliases=["edit"])
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def triggerreply_update(
        self,
        ctx: commands.Context,
        rule_id: int,
        mode: str,
        *,
        spec: str,
    ) -> None:
        try:
            parsed = parse_rule_spec(mode, spec)
        except ValueError as exc:
            await ctx.send(
                f"Rule không hợp lệ: {exc}. Dùng `<cụm từ> | <phản hồi>`."
            )
            return

        guild_rules = self.rules_by_guild.setdefault(ctx.guild.id, [])
        duplicate = any(
            rule.get("rule_id") != rule_id
            and rule.get("mode") == parsed["mode"]
            and rule.get("normalized_trigger") == parsed["normalized_trigger"]
            for rule in guild_rules
        )
        if duplicate:
            await ctx.send("Rule này đã tồn tại với cùng chế độ khớp.")
            return

        try:
            updated = self.rules.find_one_and_update(
                {"guild_id": ctx.guild.id, "rule_id": rule_id},
                {
                    "$set": {
                        **parsed,
                        "updated_by": ctx.author.id,
                        "updated_at": discord.utils.utcnow(),
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            await ctx.send("Rule này đã tồn tại với cùng chế độ khớp.")
            return
        except PyMongoError:
            logger.exception("Failed to update triggered reply guild=%s", ctx.guild.id)
            await ctx.send("Không thể cập nhật rule trong database lúc này.")
            return

        if updated is None:
            await ctx.send("Không tìm thấy rule đó.")
            return

        replaced = False
        for index, rule in enumerate(guild_rules):
            if rule.get("rule_id") == rule_id:
                guild_rules[index] = updated
                replaced = True
                break
        if not replaced:
            guild_rules.append(updated)
        guild_rules.sort(key=lambda rule: int(rule["rule_id"]))

        displayed_trigger = parsed["trigger"]
        if len(displayed_trigger) > 100:
            displayed_trigger = displayed_trigger[:97] + "..."
        await ctx.send(
            f"Đã cập nhật rule #{rule_id} (`{parsed['mode']}`): "
            f"`{displayed_trigger}`.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @triggerreply.command(name="remove", aliases=["delete"])
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def triggerreply_remove(
        self,
        ctx: commands.Context,
        rule_id: int,
    ) -> None:
        try:
            deleted = self.rules.find_one_and_delete(
                {"guild_id": ctx.guild.id, "rule_id": rule_id}
            )
        except PyMongoError:
            logger.exception("Failed to delete triggered reply guild=%s", ctx.guild.id)
            await ctx.send("Không thể xóa rule khỏi database lúc này.")
            return

        if deleted is None:
            await ctx.send("Không tìm thấy rule đó.")
            return

        self.rules_by_guild[ctx.guild.id] = [
            rule
            for rule in self.rules_by_guild.get(ctx.guild.id, [])
            if rule.get("rule_id") != rule_id
        ]
        await ctx.send(f"Đã xóa rule #{rule_id}.")

    @triggerreply.command(name="list")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def triggerreply_list(self, ctx: commands.Context) -> None:
        rules = self.rules_by_guild.get(ctx.guild.id, [])
        if not rules:
            await ctx.send("Server chưa có triggered-reply rule nào.")
            return

        for page_start in range(0, len(rules), RULES_PER_PAGE):
            page = rules[page_start : page_start + RULES_PER_PAGE]
            lines = []
            for rule in page:
                trigger = discord.utils.escape_markdown(str(rule["trigger"]))
                reply = discord.utils.escape_markdown(str(rule["reply"]))
                if len(trigger) > 80:
                    trigger = trigger[:77] + "..."
                if len(reply) > 100:
                    reply = reply[:97] + "..."
                lines.append(
                    f"**#{rule['rule_id']} · {rule['mode']}**\n"
                    f"`{trigger}` → `{reply}`"
                )

            page_number = page_start // RULES_PER_PAGE + 1
            page_count = (len(rules) + RULES_PER_PAGE - 1) // RULES_PER_PAGE
            embed = discord.Embed(
                title=f"Triggered replies · {page_number}/{page_count}",
                description="\n\n".join(lines),
                color=discord.Color.blurple(),
            )
            await ctx.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @triggerreply.error
    async def triggerreply_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Administrator để quản lý reply rules.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "Thiếu tham số. Xem cú pháp bằng lệnh `triggerreply`."
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("ID rule phải là một số nguyên.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TriggeredReplyCog(bot))
