import discord
from discord.ext import commands

from cogs._beta_function import BetaFunctionError
from cogs.operation._graceful_shutdown import ShutdownInProgress


class ServerStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.start_time = discord.utils.utcnow()
        self.command_count = 0
        self.exception_count = 0

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context) -> None:
        """Increment the count when a command starts execution."""
        self.command_count += 1

    @commands.Cog.listener()
    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        """Count unexpected command failures, excluding access denials."""
        if isinstance(error, (BetaFunctionError, ShutdownInProgress)):
            return
        self.exception_count += 1

    @commands.command(
        name="server_stats",
        help="Displays server stats since bot start.",
    )
    @commands.has_permissions(administrator=True)
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def server_stats(self, ctx: commands.Context) -> None:
        """Display in-memory uptime and command/error counts."""
        uptime = discord.utils.utcnow() - self.start_time
        hours = uptime.seconds // 3600
        minutes = (uptime.seconds % 3600) // 60
        await ctx.send(
            "**Thống kê máy chủ:**\n"
            f"- Khởi chạy lúc: {self.start_time:%Y-%m-%d %H:%M:%S UTC}\n"
            f"- Thời gian hoạt động: {uptime.days} ngày, "
            f"{hours} giờ, {minutes} phút\n"
            f"- Lệnh đã thực thi: {self.command_count}\n"
            f"- Lệnh lỗi: {self.exception_count}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerStatsCog(bot))
