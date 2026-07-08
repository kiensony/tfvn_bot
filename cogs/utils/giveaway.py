import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from pymongo import ReturnDocument


GIVEAWAY_COLLECTION = "giveaways"
GIVEAWAY_BUTTON_CUSTOM_ID = "tfvn:giveaway:join"
MAX_WINNERS = 20


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


class GiveawayJoinView(discord.ui.View):
    def __init__(
        self,
        cog: "GiveawayCog",
        message_id: int | None = None,
        ended: bool = False,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.message_id = message_id

        if ended:
            self.disable_buttons()

    def disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(
        label="Join Giveaway",
        style=discord.ButtonStyle.primary,
        custom_id=GIVEAWAY_BUTTON_CUSTOM_ID,
    )
    async def join_giveaway(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        message_id = self.message_id
        if message_id is None and interaction.message is not None:
            message_id = interaction.message.id

        if message_id is None:
            await interaction.response.send_message(
                "I cannot find this giveaway message.",
                ephemeral=True,
            )
            return

        await self.cog.join_giveaway(interaction, message_id, self)


class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.logger = logging.getLogger(__name__)
        self.pending_giveaways: dict[int, asyncio.Task] = {}
        self._synced_startup = False

    def cog_unload(self) -> None:
        for task in self.pending_giveaways.values():
            task.cancel()

    @property
    def collection(self):
        return self.db[GIVEAWAY_COLLECTION]

    def parse_duration(self, duration: str) -> int:
        matches = re.findall(r"(\d+)([dhms])", duration.lower().replace(" ", ""))
        if not matches:
            raise ValueError("Invalid duration")

        total_seconds = 0
        for value, unit in matches:
            amount = int(value)
            if unit == "d":
                total_seconds += amount * 86400
            elif unit == "h":
                total_seconds += amount * 3600
            elif unit == "m":
                total_seconds += amount * 60
            elif unit == "s":
                total_seconds += amount

        if total_seconds <= 0:
            raise ValueError("Duration must be positive")

        return total_seconds

    def format_duration(self, seconds: int) -> str:
        units = [
            ("day", 86400),
            ("hour", 3600),
            ("minute", 60),
            ("second", 1),
        ]
        parts = []

        for name, unit_seconds in units:
            value, seconds = divmod(seconds, unit_seconds)
            if value:
                label = name if value == 1 else f"{name}s"
                parts.append(f"{value} {label}")

        return " ".join(parts)

    def _usage_text(self) -> str:
        return (
            f"Usage: `{self.bot.command_prefix}giveaway <time> [winners] <prize>`\n"
            f"Example: `{self.bot.command_prefix}giveaway 10m Nitro Classic`\n"
            f"Example: `{self.bot.command_prefix}giveaway 1h 3 Discord Nitro`"
        )

    def _split_giveaway_args(
        self,
        winner_or_prize: str | None,
        prize: str | None,
    ) -> tuple[int, str | None]:
        if winner_or_prize is None:
            return 1, prize

        if winner_or_prize.isdigit():
            winner_count = int(winner_or_prize)
            return winner_count, prize

        full_prize = winner_or_prize
        if prize:
            full_prize = f"{winner_or_prize} {prize}"

        return 1, full_prize

    def _giveaway_embed(self, giveaway: dict, ended: bool = False) -> discord.Embed:
        end_at = _as_utc(giveaway["end_at"])
        entries = giveaway.get("entries", [])
        winner_count = giveaway.get("winner_count", 1)
        title = "Giveaway Ended" if ended else "Giveaway"
        color = discord.Color.dark_grey() if ended else discord.Color.gold()

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="Prize", value=giveaway["prize"], inline=False)
        embed.add_field(
            name="Host",
            value=f"<@{giveaway['host_id']}>",
            inline=True,
        )
        embed.add_field(
            name="Ends",
            value=discord.utils.format_dt(end_at, style="R"),
            inline=True,
        )
        embed.add_field(
            name="Entries",
            value=str(len(entries)),
            inline=True,
        )
        embed.add_field(
            name="Winners",
            value=str(winner_count),
            inline=True,
        )

        winner_ids = giveaway.get("winner_ids") or []
        if ended:
            winner_text = (
                ", ".join(f"<@{winner_id}>" for winner_id in winner_ids)
                if winner_ids
                else "No valid entries."
            )
            embed.add_field(name="Result", value=winner_text, inline=False)
        else:
            embed.set_footer(text="Click the button below to join.")

        return embed

    async def _fetch_channel(
        self,
        channel_id: int,
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel

        try:
            channel = await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            self.logger.exception("Cannot fetch giveaway channel %s.", channel_id)
            return None

        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _edit_giveaway_message(
        self,
        giveaway: dict,
        ended: bool = False,
    ) -> None:
        channel = await self._fetch_channel(giveaway["channel_id"])
        if channel is None or not hasattr(channel, "fetch_message"):
            return

        try:
            message = await channel.fetch_message(giveaway["message_id"])
        except (discord.Forbidden, discord.NotFound):
            return
        except discord.HTTPException:
            self.logger.exception(
                "Cannot fetch giveaway message %s.",
                giveaway["message_id"],
            )
            return

        view = GiveawayJoinView(self, giveaway["message_id"], ended=ended)

        try:
            await message.edit(
                embed=self._giveaway_embed(giveaway, ended=ended),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound):
            return
        except discord.HTTPException:
            self.logger.exception(
                "Cannot edit giveaway message %s.",
                giveaway["message_id"],
            )

    def _track_giveaway(self, giveaway: dict) -> None:
        message_id = giveaway["message_id"]
        if message_id in self.pending_giveaways:
            return

        task = asyncio.create_task(self.schedule_giveaway_end(giveaway))
        self.pending_giveaways[message_id] = task

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._synced_startup:
            return

        self._synced_startup = True
        now = discord.utils.utcnow()
        active_giveaways = list(
            self.collection.find(
                {
                    "ended": False,
                    "end_at": {"$gt": now},
                }
            )
        )
        expired_giveaways = list(
            self.collection.find(
                {
                    "ended": False,
                    "end_at": {"$lte": now},
                }
            )
        )

        for giveaway in active_giveaways:
            self.bot.add_view(
                GiveawayJoinView(self, giveaway["message_id"]),
                message_id=giveaway["message_id"],
            )
            self._track_giveaway(giveaway)

        for giveaway in expired_giveaways:
            self._track_giveaway(giveaway)

    async def schedule_giveaway_end(self, giveaway: dict) -> None:
        message_id = giveaway["message_id"]
        try:
            now = discord.utils.utcnow()
            end_at = _as_utc(giveaway["end_at"])
            delay = max(0, (_as_utc(end_at) - _as_utc(now)).total_seconds())

            if delay:
                await asyncio.sleep(delay)

            await self.end_giveaway(message_id)
        except asyncio.CancelledError:
            raise
        finally:
            self.pending_giveaways.pop(message_id, None)

    async def join_giveaway(
        self,
        interaction: discord.Interaction,
        message_id: int,
        view: GiveawayJoinView,
    ) -> None:
        if interaction.user.bot:
            await interaction.response.send_message(
                "Bot accounts cannot join giveaways.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = self.collection.update_one(
            {"message_id": message_id, "ended": False},
            {"$addToSet": {"entries": interaction.user.id}},
        )

        giveaway = self.collection.find_one({"message_id": message_id})
        if giveaway is None or giveaway.get("ended"):
            view.disable_buttons()
            await interaction.followup.send(
                "This giveaway has already ended.",
                ephemeral=True,
            )
            return

        if result.modified_count == 0:
            await interaction.followup.send(
                "You already joined this giveaway.",
                ephemeral=True,
            )
            return

        if interaction.message is not None:
            try:
                await interaction.message.edit(
                    embed=self._giveaway_embed(giveaway),
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound):
                pass
            except discord.HTTPException:
                self.logger.exception(
                    "Cannot update giveaway entry count for %s.",
                    message_id,
                )

        await interaction.followup.send(
            "You joined the giveaway. Good luck!",
            ephemeral=True,
        )

    async def end_giveaway(self, message_id: int) -> None:
        ended_at = discord.utils.utcnow()
        giveaway = self.collection.find_one_and_update(
            {"message_id": message_id, "ended": False},
            {"$set": {"ended": True, "ended_at": ended_at}},
            return_document=ReturnDocument.AFTER,
        )
        if giveaway is None:
            return

        entries = list(dict.fromkeys(giveaway.get("entries", [])))
        winner_count = min(giveaway.get("winner_count", 1), len(entries))
        winner_ids = random.sample(entries, winner_count) if winner_count else []

        giveaway = self.collection.find_one_and_update(
            {"message_id": message_id},
            {"$set": {"winner_ids": winner_ids}},
            return_document=ReturnDocument.AFTER,
        )
        if giveaway is None:
            return

        await self._edit_giveaway_message(giveaway, ended=True)

        channel = await self._fetch_channel(giveaway["channel_id"])
        if channel is None or not hasattr(channel, "send"):
            return

        if winner_ids:
            winners = ", ".join(f"<@{winner_id}>" for winner_id in winner_ids)
            winner_label = "Winner" if len(winner_ids) == 1 else "Winners"
            content = (
                f"Giveaway ended for **{giveaway['prize']}**.\n"
                f"{winner_label}: {winners}"
            )
        else:
            content = (
                f"Giveaway ended for **{giveaway['prize']}**, "
                "but nobody joined."
            )

        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
        except discord.Forbidden:
            self.logger.exception(
                "Cannot send giveaway result for message %s.",
                message_id,
            )
        except discord.HTTPException:
            self.logger.exception(
                "Failed to send giveaway result for message %s.",
                message_id,
            )

    @commands.command(
        name="giveaway",
        aliases=["ga"],
        help="Start a giveaway with a join button.",
    )
    @commands.guild_only()
    async def giveaway(
        self,
        ctx: commands.Context,
        duration: str = None,
        winner_or_prize: str = None,
        *,
        prize: str = None,
    ) -> None:
        if duration is None:
            await ctx.reply(self._usage_text(), mention_author=False)
            return

        winner_count, prize = self._split_giveaway_args(winner_or_prize, prize)
        if not prize:
            await ctx.reply(self._usage_text(), mention_author=False)
            return

        if winner_count < 1 or winner_count > MAX_WINNERS:
            await ctx.reply(
                f"Winner count must be between 1 and {MAX_WINNERS}.",
                mention_author=False,
            )
            return

        try:
            seconds = self.parse_duration(duration)
        except ValueError:
            await ctx.reply(
                "Invalid time. Use formats like `10m`, `1h30m`, or `2d`.",
                mention_author=False,
            )
            return

        end_at = discord.utils.utcnow() + timedelta(seconds=seconds)
        giveaway_doc = {
            "guild_id": ctx.guild.id if ctx.guild else None,
            "channel_id": ctx.channel.id,
            "host_id": ctx.author.id,
            "message_id": 0,
            "prize": prize.strip(),
            "winner_count": winner_count,
            "entries": [],
            "winner_ids": [],
            "created_at": discord.utils.utcnow(),
            "end_at": end_at,
            "ended": False,
        }

        view = GiveawayJoinView(self)
        message = await ctx.send(
            embed=self._giveaway_embed(giveaway_doc),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        view.message_id = message.id
        giveaway_doc["message_id"] = message.id

        self.collection.insert_one(giveaway_doc)
        self._track_giveaway(giveaway_doc)

        await ctx.reply(
            (
                f"Giveaway started for **{prize.strip()}**. "
                f"It ends in {self.format_duration(seconds)}."
            ),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @giveaway.error
    async def giveaway_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Giveaways can only be started in a server.",
                mention_author=False,
            )
            return

        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
