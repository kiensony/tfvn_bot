from datetime import timezone

import discord
from discord.ext import commands
from pymongo import DESCENDING


TRANSACTION_LABELS = {
    "shop_purchase": "Mua vật phẩm",
    "slot_machine_play": "Chơi slot",
    "slot_machine_win": "Thắng slot",
    "big_speaker": "Big speaker / Loa",
    "big_speaker_refund": "Hoàn big speaker",
}


class UserAccountCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    @commands.command(name="user_balance", aliases=["balance"])
    async def user_balance(self, ctx: commands.Context) -> None:
        user_id = ctx.author.id
        self.db["user_accounts"].update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"balance": 0}},
            upsert=True,
        )
        account = self.db["user_accounts"].find_one({"user_id": user_id}) or {}
        balance = int(account.get("balance", 0))
        badge = account.get("active_badge") or {}
        badge_text = ""
        if not ctx.guild or badge.get("guild_id") == ctx.guild.id:
            if badge.get("name"):
                badge_text = f" · Badge: **{badge['name']}**"
        await ctx.send(
            f"{ctx.author.mention}, số dư của bạn: **{balance:,} TC**{badge_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="user_transactions", aliases=["transactions"])
    async def user_transactions(self, ctx: commands.Context) -> None:
        transactions = list(
            self.db["transaction_logs"]
            .find({"user_id": ctx.author.id})
            .sort("timestamp", DESCENDING)
            .limit(10)
        )
        if not transactions:
            await ctx.send("Bạn chưa có giao dịch nào.")
            return

        lines = []
        for transaction in transactions:
            transaction_type = transaction.get("transaction_type", "debit")
            sign = "+" if transaction_type == "credit" else "−"
            amount = int(transaction.get("amount", 0))
            label = TRANSACTION_LABELS.get(
                transaction.get("type"), transaction.get("type", "Giao dịch")
            )
            if transaction.get("item_id"):
                label += f" ({transaction['item_id']})"
            timestamp = transaction.get("timestamp")
            if timestamp:
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                when = discord.utils.format_dt(timestamp, style="R")
            else:
                when = "không rõ thời gian"
            lines.append(f"{sign}{amount:,} TC · {label} · {when}")

        embed = discord.Embed(
            title=f"💳 Giao dịch của {ctx.author.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserAccountCog(bot))
