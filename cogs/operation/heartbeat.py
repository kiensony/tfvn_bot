from discord.ext import commands

from cogs._beta_function import BetaFunction


class HeartbeatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ping", help="Checks if the bot is responsive.")
    async def ping(self, ctx: commands.Context) -> None:
        """Responds with a heartbeat message to confirm the bot is active."""
        await ctx.send("💓 TFVN Bot đang hoạt động và phản hồi!")

    @commands.command(
        name="beta_preview",
        help="Kiểm tra quyền truy cập một Beta function.",
    )
    @BetaFunction
    async def beta_preview(self, ctx: commands.Context) -> None:
        await ctx.send("🧪 Beta function hoạt động; bạn có Beta role.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HeartbeatCog(bot))
