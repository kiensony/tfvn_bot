"""Paid big-speaker utility: spend TC to re-post large markdown text."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from cogs.utils._big_speaker_helpers import (
    clean_message,
    format_amount_guide,
    format_big_speaker,
    resolve_speaker_tier,
)


logger = logging.getLogger(__name__)

ACCOUNTS_COLLECTION = "user_accounts"
TRANSACTIONS_COLLECTION = "transaction_logs"

SPEAKER_ALLOWED_MENTIONS = discord.AllowedMentions(
    everyone=False,
    roles=False,
    users=True,
    replied_user=False,
)


class BigSpeakerCog(commands.Cog):
    """Spend Trap Coins to have the bot re-speak a message in large text."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.accounts = self.db[ACCOUNTS_COLLECTION]
        self.transactions = self.db[TRANSACTIONS_COLLECTION]

    def _bot_can_send(self, channel: discord.abc.Messageable) -> bool:
        if not isinstance(channel, discord.abc.GuildChannel):
            return False
        guild = channel.guild
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return bool(perms.send_messages)

    def _log_transaction(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        channel_id: int | None,
        amount: int,
        text_size: int,
        transaction_type: str,
        tx_type: str,
    ) -> None:
        try:
            self.transactions.insert_one(
                {
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "type": tx_type,
                    "transaction_type": transaction_type,
                    "amount": amount,
                    "text_size": text_size,
                    "channel_id": channel_id,
                    "timestamp": discord.utils.utcnow(),
                }
            )
        except PyMongoError:
            logger.exception("Failed to write big_speaker transaction log")

    def _refund(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        channel_id: int | None,
        amount: int,
        text_size: int,
    ) -> None:
        try:
            self.accounts.update_one(
                {"user_id": user_id},
                {"$inc": {"balance": amount}},
            )
        except PyMongoError:
            logger.exception("Failed to refund big_speaker charge")
            return
        self._log_transaction(
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            amount=amount,
            text_size=text_size,
            transaction_type="credit",
            tx_type="big_speaker_refund",
        )

    @commands.command(
        name="big_speaker",
        aliases=["loa", "speaker"],
        help=(
            "Chi TC để bot nói to trong kênh. "
            "Giá: 1/2/5/10/20/50 TC → cỡ 1–6."
        ),
    )
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def big_speaker(
        self,
        ctx: commands.Context,
        amount: int,
        *,
        message: str,
    ) -> None:
        try:
            tier = resolve_speaker_tier(amount)
        except ValueError as exc:
            await ctx.send(
                f"{exc}\nBảng giá: {format_amount_guide()}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        try:
            cleaned = clean_message(message)
        except ValueError as exc:
            await ctx.send(
                str(exc),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if not self._bot_can_send(ctx.channel):
            await ctx.send(
                "Bot không có quyền gửi tin nhắn trong kênh này.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        self.accounts.update_one(
            {"user_id": ctx.author.id},
            {"$setOnInsert": {"balance": 0}},
            upsert=True,
        )
        account = self.accounts.find_one_and_update(
            {"user_id": ctx.author.id, "balance": {"$gte": tier.amount}},
            {"$inc": {"balance": -tier.amount}},
            return_document=ReturnDocument.AFTER,
        )
        if account is None:
            await ctx.send(
                f"Bạn không có đủ Trap Coin. Cần **{tier.amount:,} TC** "
                f"cho cỡ chữ {tier.text_size}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        body = format_big_speaker(cleaned, tier.text_size)
        content = (
            f"{body}\n\n"
            f"— {ctx.author.display_name} · {tier.amount:,} TC · cỡ {tier.text_size}"
        )

        guild_id = ctx.guild.id if ctx.guild else None
        channel_id = ctx.channel.id if ctx.channel else None

        self._log_transaction(
            user_id=ctx.author.id,
            guild_id=guild_id,
            channel_id=channel_id,
            amount=tier.amount,
            text_size=tier.text_size,
            transaction_type="debit",
            tx_type="big_speaker",
        )

        try:
            await ctx.send(content, allowed_mentions=SPEAKER_ALLOWED_MENTIONS)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to post big_speaker message; refunding")
            self._refund(
                user_id=ctx.author.id,
                guild_id=guild_id,
                channel_id=channel_id,
                amount=tier.amount,
                text_size=tier.text_size,
            )
            await ctx.send(
                "Không gửi được loa. Trap Coin đã được hoàn lại.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        remaining = int(account.get("balance", 0))
        await ctx.send(
            f"Đã chi **{tier.amount:,} TC** (cỡ {tier.text_size}). "
            f"Số dư còn lại: **{remaining:,} TC**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BigSpeakerCog(bot))
