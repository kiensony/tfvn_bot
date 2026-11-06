import asyncio
import logging

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.settings._guild_variables import (
    GUILD_SETTING_VARIABLES,
    MRBEAST_SCAM_ALERT_CHANNEL,
    ensure_guild_variable_index,
    get_guild_variables,
    normalize_guild_setting_value,
    valid_guild_id,
)


logger = logging.getLogger(__name__)
UNSUPPORTED_VARIABLE_MESSAGE = (
    "❌ Lệnh này không hỗ trợ biến này. Dùng `setting` để xem danh sách."
)


class VariableSetting(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.collection = bot.db["global_variables"]
        ensure_guild_variable_index(self.collection)
        self.bot.global_vars, self.bot.guild_vars = self.load_variables()

    def save_variable(self, guild_id: int, name: str, value: str) -> None:
        """Persist one supported setting for the specified guild."""
        if name not in GUILD_SETTING_VARIABLES:
            raise ValueError("Only supported guild settings can be saved.")
        if not valid_guild_id(guild_id):
            raise ValueError("A positive guild ID is required.")
        stored = normalize_guild_setting_value(name, value)
        self.collection.update_one(
            {"guild_id": guild_id, "name": name},
            {"$set": {
                "guild_id": guild_id, "name": name, "type": "STRING", "value": stored,
            }},
            upsert=True,
        )
        self.bot.guild_vars.setdefault(guild_id, {})[name] = stored

    def load_variables(self) -> tuple[dict[str, object], dict[int, dict[str, str]]]:
        """Load guild settings and retain read compatibility for existing cogs."""
        legacy = {}
        guilds = {}
        for doc in self.collection.find():
            name = doc.get("name")
            if not isinstance(name, str) or "value" not in doc:
                continue
            if "guild_id" not in doc:
                # Existing features still read the original flat configuration.
                legacy[name] = doc["value"]
            elif name in GUILD_SETTING_VARIABLES and valid_guild_id(doc["guild_id"]):
                value = doc["value"]
                if isinstance(value, str) and value.strip():
                    guilds.setdefault(doc["guild_id"], {})[name] = value
        return legacy, guilds

    @commands.group(name="setting", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setting(self, ctx: commands.Context) -> None:
        """Quản lý thông báo chào mừng, rời/kick/ban và kênh cảnh báo MrBeast."""
        variables = ", ".join(f"`{name}`" for name in sorted(GUILD_SETTING_VARIABLES))
        await ctx.send(
            "Dùng `setting set_variable <NAME>` để đặt biến hoặc "
            "`setting get_variable <NAME>` để xem biến của server này.\n"
            f"Biến được hỗ trợ: {variables}"
        )

    @setting.command(name="set_variable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.max_concurrency(1, per=commands.BucketType.member, wait=False)
    async def set_variable(self, ctx: commands.Context, name: str) -> None:
        """Đặt biến thông báo hoặc kênh cảnh báo MrBeast cho server hiện tại."""
        if name not in GUILD_SETTING_VARIABLES:
            await ctx.send(UNSUPPORTED_VARIABLE_MESSAGE)
            return

        def check(message: discord.Message) -> bool:
            return (
                message.author == ctx.author
                and message.channel == ctx.channel
                and message.guild is not None
                and message.guild.id == ctx.guild.id
            )

        try:
            while True:
                if name == MRBEAST_SCAM_ALERT_CHANNEL:
                    prompt = (
                        f"Nhập ID kênh hoặc mention kênh cho `{name}` của server này. "
                        "Gõ `cancel` để hủy."
                    )
                else:
                    prompt = (
                        f"Nhập giá trị cho `{name}` của server này. Gõ `cancel` để hủy."
                    )
                await ctx.send(prompt)
                message = await self.bot.wait_for("message", check=check, timeout=120)
                value = message.content.strip()
                if value.lower() == "cancel":
                    await ctx.send("✅ Đã hủy thiết lập biến.")
                    return
                if not value:
                    await ctx.send("❌ Giá trị không được rỗng.")
                    continue
                try:
                    value = normalize_guild_setting_value(name, value)
                except ValueError:
                    await ctx.send(
                        "❌ Nhập ID kênh hoặc mention kênh, ví dụ `123456789012345678`."
                    )
                    continue
                break
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        current_member = ctx.guild.get_member(ctx.author.id)
        if current_member is None or not current_member.guild_permissions.administrator:
            await ctx.send("❌ Bạn cần quyền Administrator để thiết lập biến.")
            return
        try:
            self.save_variable(ctx.guild.id, name, value)
        except PyMongoError:
            logger.exception("Could not save a guild setting for guild %s", ctx.guild.id)
            await ctx.send("❌ Không lưu được biến. Vui lòng thử lại sau.")
            return
        await ctx.send(
            f"✅ Đã thiết lập biến '{name}' cho server này (ID: {ctx.guild.id}).",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @setting.command(name="get_variable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def get_variable(self, ctx: commands.Context, requested_name: str) -> None:
        """Xem biến thông báo hoặc kênh cảnh báo MrBeast của server hiện tại."""
        if requested_name not in GUILD_SETTING_VARIABLES:
            await ctx.send(UNSUPPORTED_VARIABLE_MESSAGE)
            return
        variables = get_guild_variables(self.bot, ctx.guild.id)
        if requested_name not in variables:
            await ctx.send("❌ Không tìm thấy biến trong server này.")
            return
        text = f"Biến '{requested_name}' của server này:\n{variables[requested_name]}"
        for start in range(0, len(text), 1900):
            await ctx.send(
                text[start:start + 1900], allowed_mentions=discord.AllowedMentions.none()
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VariableSetting(bot))
