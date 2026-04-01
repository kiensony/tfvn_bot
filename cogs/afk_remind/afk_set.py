import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import re


class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    def parse_time_string(self, time_str: str) -> int:
        pattern = r"(\d+)([dhms])"
        matches = re.findall(pattern, time_str)

        if not matches:
            raise ValueError("Invalid time format")

        total_seconds = 0
        for value, unit in matches:
            value = int(value)
            if unit == "d":
                total_seconds += value * 86400
            elif unit == "h":
                total_seconds += value * 3600
            elif unit == "m":
                total_seconds += value * 60
            elif unit == "s":
                total_seconds += value

        return total_seconds

    def format_duration(self, seconds: int) -> str:
        units = [
            ("ngày", 86400),
            ("giờ", 3600),
            ("phút", 60),
            ("giây", 1),
        ]

        parts = []
        for name, unit_seconds in units:
            value, seconds = divmod(seconds, unit_seconds)
            if value > 0:
                parts.append(f"{value} {name}")

        return " ".join(parts)

    @commands.group(name="afk", invoke_without_command=True)
    async def afk(self, ctx: commands.Context):
        """Set AFK reminder or clear existing one."""
        embed = discord.Embed(
            title="Cài đặt nhắc AFK ⌛",
            description=(
                "Sử dụng các lệnh con để cài đặt hoặc xóa lời nhắc AFK:\n"
                "`!tf afk time` - Cài đặt lời nhắc AFK theo thời gian.\n"
                "`!tf afk dynamic` - Cài đặt lời nhắc AFK (sẽ tự động xóa khi bạn gửi tin nhắn).\n"
                "`!tf afk clear` - Xóa lời nhắc AFK hiện tại của bạn.\n"
                "`!tf afk check` - Kiểm tra các ping AFK chưa đọc của bạn."
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)
    
    @afk.command(name="dynamic")
    async def afk_dynamic(self, ctx: commands.Context, *, reason: str = "Không có lý do"):
        # check if user already has an active AFK reminder
        existing = self.db["afk_reminders"].find_one(
            {
                "user_id": ctx.author.id,
                "$or": [
                    {"end_at": None},
                    {"end_at": {"$gt": discord.utils.utcnow()}}
                ]
            })
        if existing:
            embed = discord.Embed(
                description="❌ Bạn đã có lời nhắc AFK đang hoạt động.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            title="✅ Đã set nhắc AFK động!",
            description=(
                f"**Lý do:** {reason}\n\n"
                "Lời nhắc AFK này sẽ tự động bị xóa khi bạn gửi tin nhắn."
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

        self.db["afk_reminders"].update_one(
            {"user_id": ctx.author.id},
            {"$set": {"message": reason, "start_at": discord.utils.utcnow(), "end_at": None}},
            upsert=True,
        )

        monitor_cog = self.bot.get_cog('MonitorAfkMessageCog')
        if monitor_cog:
            monitor_cog._load_dynamic_afk_users()
    
    @afk.command(name="time")
    async def afk_by_time(self, ctx: commands.Context):
        existing = self.db["afk_reminders"].find_one(
            {
                "user_id": ctx.author.id,
                "$or": [
                    {"end_at": None},
                    {"end_at": {"$gt": discord.utils.utcnow()}}
                ]
            })
        if existing:
            embed = discord.Embed(
                description="❌ Bạn đã có lời nhắc AFK đang hoạt động.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        # if action and action.lower() == "clear":
        #     await self.clear_afk(ctx)
        #     return
        
        try:
            embed = discord.Embed(
                title="Set thời gian AFK ⌛",
                description="Vui lòng nhập thời gian muốn AFK:\nVí dụ: `1h30m`, `2d 3h 5s`",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="!tf afk")
            await ctx.send(embed=embed)

            msg_time = await self.bot.wait_for("message", check=check, timeout=120)
            time_str = msg_time.content.lower().replace(" ", "")
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        try:
            embed = discord.Embed(
                title="Set tin nhắn nhắc AFK 📝",
                description="Vui lòng nhập lý do AFK:",
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

            msg_afk_message = await self.bot.wait_for(
                "message", check=check, timeout=300
            )
            remind_message = msg_afk_message.content
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        try:
            seconds = self.parse_time_string(time_str)
        except Exception:
            await ctx.send("❌ Định dạng thời gian không hợp lệ. Vui lòng thử lại.")
            return

        formatted_time = self.format_duration(seconds)
        end_at = discord.utils.utcnow() + timedelta(seconds=seconds)

        self.db["afk_reminders"].update_one(
            {"user_id": ctx.author.id},
            {"$set": {"message": remind_message, "start_at": discord.utils.utcnow(), "end_at": end_at}},
            upsert=True,
        )

        embed = discord.Embed(
            title="✅ Đã set nhắc AFK!",
            description=(
                f"**Thời gian**: {formatted_time}.\n**Lý do:** {remind_message}\n\nNhập `!tf afk clear` để hủy nhắc AFK."
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @afk.command(name="clear")
    async def clear_afk(self, ctx: commands.Context):
        result = self.db["afk_reminders"].update_one(
            {
                "user_id": ctx.author.id,
                "end_at": {"$gt": discord.utils.utcnow()},
            },
            {
                "$set": {"end_at": discord.utils.utcnow()},
            },
        )

        if result.matched_count == 0:
            embed = discord.Embed(
                description="❌ Bạn chưa cài lời nhắc AFK nào.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            description="✅ Đã xóa lời nhắc AFK của bạn.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)


    @clear_afk.error
    async def clear_afk_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandInvokeError):
            embed = discord.Embed(
                description="⚠️ Đã xảy ra lỗi khi xóa lời nhắc AFK.",
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)

    @afk.command(name="check")
    async def ping_check(self, ctx: commands.Context):
        pings = self.db["afk_pings"].find({"user_id": ctx.author.id, "is_read": False})
        ping_list = list(pings)

        if not ping_list:
            embed = discord.Embed(
                description="✅ Bạn không có ping AFK nào.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
            return

        description = ""
        for ping in ping_list:
            pinged_by = ctx.guild.get_member(ping["pinged_by"])
            channel = self.bot.get_channel(ping["channel_id"])
            timestamp = ping["timestamp"].strftime("%Y-%m-%d %H:%M:%S")

            description += (
                f"- Bị ping bởi {pinged_by.mention if pinged_by else 'Unknown User'} "
                f"vào {timestamp} trong kênh {channel.mention if channel else 'Unknown Channel'} (nhảy đến tin nhắn: {ping.get('jump_url') or 'N/A'})\n"
            )

        embed = discord.Embed(
            title="📋 Danh sách ping AFK của bạn",
            description=description,
            color=discord.Color.blurple(),
        )

        # update all pings to read
        self.db["afk_pings"].update_many(
            {"user_id": ctx.author.id, "is_read": False},
            {"$set": {"is_read": True}},
        )

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(AFK(bot))
