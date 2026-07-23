from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
from pymongo import ASCENDING, ReturnDocument

from cogs.interaction._marriage_helpers import (
    XP_PER_INTERACTION,
    days_together,
    level_from_xp,
    level_progress_bar,
    next_rank,
    normalize_pair,
    rank_from_level,
    rank_from_xp,
    rank_level_progress_bar,
    xp_progress_in_level,
)

logger = logging.getLogger(__name__)

MARRIAGES_COLLECTION = "marriages"
PROPOSALS_COLLECTION = "marriage_proposals"

PROPOSE_YES_CUSTOM_ID = "tfvn:marriage:propose:yes"
PROPOSE_NO_CUSTOM_ID = "tfvn:marriage:propose:no"

PROPOSAL_TIMEOUT_SECONDS = 5 * 60
DIVORCE_TIMEOUT_SECONDS = 60


def _utcnow() -> datetime:
    return discord.utils.utcnow()


def _as_naive_utc(dt: datetime) -> datetime:
    """Store naive UTC for consistent Mongo comparisons (match giveaway style)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _as_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ProposeView(discord.ui.View):
    def __init__(self, cog: MarriageCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Đồng ý",
        style=discord.ButtonStyle.success,
        emoji="💍",
        custom_id=PROPOSE_YES_CUSTOM_ID,
    )
    async def yes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_propose_response(interaction, accepted=True)

    @discord.ui.button(
        label="Từ chối",
        style=discord.ButtonStyle.danger,
        emoji="💔",
        custom_id=PROPOSE_NO_CUSTOM_ID,
    )
    async def no_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_propose_response(interaction, accepted=False)


class DivorceConfirmView(discord.ui.View):
    """Timeout-bound confirm view (not persistent; no fixed custom_id)."""

    def __init__(
        self,
        cog: MarriageCog,
        *,
        guild_id: int,
        actor_id: int,
        marriage_id: Any,
    ) -> None:
        super().__init__(timeout=DIVORCE_TIMEOUT_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.actor_id = actor_id
        self.marriage_id = marriage_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message(
                "Chỉ người yêu cầu ly hôn mới bấm được nút này.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Xác nhận ly hôn",
        style=discord.ButtonStyle.danger,
        emoji="💔",
    )
    async def yes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.finalize_divorce(interaction, self)

    @discord.ui.button(
        label="Huỷ",
        style=discord.ButtonStyle.secondary,
        emoji="❎",
    )
    async def no_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="❎ Đã huỷ ly hôn",
            description="Cuộc hôn nhân vẫn còn đó. Yêu thương nhau nhé 💕",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self) -> None:
        self.stop()


class MarriageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self._ensure_indexes()
        # Persistent propose buttons survive restarts.
        bot.add_view(ProposeView(self))

    @property
    def marriages(self):
        return self.db[MARRIAGES_COLLECTION]

    @property
    def proposals(self):
        return self.db[PROPOSALS_COLLECTION]

    def _ensure_indexes(self) -> None:
        try:
            self.marriages.create_index(
                [("guild_id", ASCENDING), ("partner_ids", ASCENDING), ("status", ASCENDING)]
            )
            self.marriages.create_index(
                [("guild_id", ASCENDING), ("user_a", ASCENDING), ("status", ASCENDING)]
            )
            self.marriages.create_index(
                [("guild_id", ASCENDING), ("user_b", ASCENDING), ("status", ASCENDING)]
            )
            self.proposals.create_index([("message_id", ASCENDING)], unique=True)
            self.proposals.create_index(
                [("guild_id", ASCENDING), ("proposer_id", ASCENDING), ("status", ASCENDING)]
            )
            self.proposals.create_index(
                [("guild_id", ASCENDING), ("target_id", ASCENDING), ("status", ASCENDING)]
            )
            self.proposals.create_index([("expires_at", ASCENDING)])
        except Exception:
            logger.exception("Failed to create marriage indexes")

    def find_active_marriage(self, guild_id: int, user_id: int) -> dict | None:
        return self.marriages.find_one(
            {
                "guild_id": guild_id,
                "status": "active",
                "partner_ids": user_id,
            }
        )

    def find_active_couple(
        self, guild_id: int, user_id: int, other_id: int
    ) -> dict | None:
        try:
            user_a, user_b = normalize_pair(user_id, other_id)
        except ValueError:
            return None
        return self.marriages.find_one(
            {
                "guild_id": guild_id,
                "status": "active",
                "user_a": user_a,
                "user_b": user_b,
            }
        )

    def find_pending_involving(self, guild_id: int, user_id: int) -> dict | None:
        now = _as_naive_utc(_utcnow())
        return self.proposals.find_one(
            {
                "guild_id": guild_id,
                "status": "pending",
                "expires_at": {"$gt": now},
                "$or": [{"proposer_id": user_id}, {"target_id": user_id}],
            }
        )

    def _expire_stale_proposals(self, guild_id: int | None = None) -> None:
        now = _as_naive_utc(_utcnow())
        query: dict[str, Any] = {
            "status": "pending",
            "expires_at": {"$lte": now},
        }
        if guild_id is not None:
            query["guild_id"] = guild_id
        self.proposals.update_many(query, {"$set": {"status": "expired"}})

    def _eligibility_error(
        self,
        guild_id: int,
        proposer: discord.Member,
        target: discord.Member,
    ) -> str | None:
        if target.bot:
            return "Không thể cầu hôn bot được đâu 🤖"
        if target.id == proposer.id:
            return "Tự cầu hôn chính mình… hơi cô đơn đó 😳 Hãy tag người khác nhé."
        if self.find_active_marriage(guild_id, proposer.id):
            return "Bạn đã kết hôn rồi. Hãy `divorce` trước nếu muốn cầu hôn người khác."
        if self.find_active_marriage(guild_id, target.id):
            return f"{target.mention} đã có hôn nhân rồi."
        if self.find_pending_involving(guild_id, proposer.id):
            return "Bạn đang có lời cầu hôn chờ phản hồi. Hãy đợi hết hạn hoặc được trả lời."
        if self.find_pending_involving(guild_id, target.id):
            return f"{target.mention} đang có lời cầu hôn chờ phản hồi."
        return None

    def build_status_embed(
        self,
        *,
        requester: discord.Member,
        partner_a: discord.abc.User,
        partner_b: discord.abc.User,
        marriage: dict,
    ) -> discord.Embed:
        xp = int(marriage.get("xp", 0))
        level = int(marriage.get("level") or level_from_xp(xp))
        rank = rank_from_level(level)
        into, need = xp_progress_in_level(xp)
        following = next_rank(level)

        embed = discord.Embed(
            title="💍 Tình trạng hôn nhân",
            description=(
                f"{partner_a.mention}  ❤️  {partner_b.mention}\n"
                f"{rank.emoji} **{rank.display}** · Level **{level}**"
            ),
            color=rank.color,
        )
        embed.set_author(
            name=requester.display_name,
            icon_url=requester.display_avatar.url,
        )
        # Prefer the "other" partner as thumbnail when requester is one of them.
        other = partner_b if partner_a.id == requester.id else partner_a
        if requester.id not in (partner_a.id, partner_b.id):
            other = partner_a
        embed.set_thumbnail(url=other.display_avatar.url)

        embed.add_field(
            name="📊 Tiến độ level",
            value=(
                f"`{level_progress_bar(xp)}`  {into}/{need} XP\n"
                f"Tổng XP: **{xp}**"
            ),
            inline=False,
        )

        if following is None:
            next_rank_value = f"{rank.emoji} Đã đạt hạng cao nhất (**{rank.display}**)"
        else:
            next_rank_value = (
                f"{following.emoji} **{following.display}** "
                f"(level {following.min_level})\n"
                f"`{rank_level_progress_bar(level)}`  "
                f"level {level}/{following.min_level}"
            )
        embed.add_field(name="🏅 Hạng tiếp theo", value=next_rank_value, inline=False)

        married_at = marriage.get("married_at")
        if married_at is not None:
            married_aware = _as_aware_utc(married_at)
            days = days_together(married_aware, _utcnow())
            embed.add_field(
                name="📅 Ngày cưới",
                value=(
                    f"{discord.utils.format_dt(married_aware, style='D')} · "
                    f"{discord.utils.format_dt(married_aware, style='R')}\n"
                    f"Đã bên nhau: **{days} ngày**"
                ),
                inline=False,
            )

        last_xp = marriage.get("last_xp_at")
        if last_xp is not None:
            last_xp_text = discord.utils.format_dt(
                _as_aware_utc(last_xp), style="R"
            )
        else:
            last_xp_text = "Chưa có"
        embed.add_field(
            name="🤝 Tương tác gần nhất",
            value=last_xp_text,
            inline=False,
        )

        if requester.guild is not None:
            embed.set_footer(text=requester.guild.name)
        return embed

    @commands.command(
        name="propose",
        help="Cầu hôn một member. Đối phương bấm Đồng ý / Từ chối.",
    )
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def propose(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        assert ctx.guild is not None
        self._expire_stale_proposals(ctx.guild.id)

        error = self._eligibility_error(ctx.guild.id, ctx.author, member)
        if error:
            await ctx.reply(error, mention_author=False)
            return

        now = _utcnow()
        expires_at = now + timedelta(seconds=PROPOSAL_TIMEOUT_SECONDS)
        rank = rank_from_level(1)

        embed = discord.Embed(
            title="💌 Lời cầu hôn",
            description=(
                f"{ctx.author.mention} cầu hôn {member.mention}!\n\n"
                f"Chỉ {member.mention} có thể bấm nút bên dưới.\n"
                f"Hết hạn {discord.utils.format_dt(expires_at, style='R')}."
            ),
            color=rank.color,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )

        view = ProposeView(self)
        message = await ctx.send(
            content=member.mention,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        try:
            self.proposals.insert_one(
                {
                    "guild_id": ctx.guild.id,
                    "proposer_id": ctx.author.id,
                    "target_id": member.id,
                    "channel_id": ctx.channel.id,
                    "message_id": message.id,
                    "status": "pending",
                    "created_at": _as_naive_utc(now),
                    "expires_at": _as_naive_utc(expires_at),
                }
            )
        except Exception:
            logger.exception("Failed to persist marriage proposal")
            await message.edit(
                content=None,
                embed=discord.Embed(
                    title="❌ Không thể tạo lời cầu hôn",
                    description="Lỗi lưu dữ liệu. Thử lại sau nhé.",
                    color=discord.Color.red(),
                ),
                view=None,
            )

    async def handle_propose_response(
        self, interaction: discord.Interaction, *, accepted: bool
    ) -> None:
        if interaction.message is None or interaction.guild is None:
            await interaction.response.send_message(
                "Không tìm thấy lời cầu hôn này.",
                ephemeral=True,
            )
            return

        self._expire_stale_proposals(interaction.guild.id)
        proposal = self.proposals.find_one({"message_id": interaction.message.id})
        if proposal is None or proposal.get("status") != "pending":
            await interaction.response.send_message(
                "Lời cầu hôn này không còn hiệu lực.",
                ephemeral=True,
            )
            return

        now = _as_naive_utc(_utcnow())
        expires_at = proposal.get("expires_at")
        if expires_at is not None and expires_at <= now:
            self.proposals.update_one(
                {"_id": proposal["_id"]},
                {"$set": {"status": "expired"}},
            )
            await interaction.response.send_message(
                "Lời cầu hôn đã hết hạn.",
                ephemeral=True,
            )
            return

        if interaction.user.id != proposal["target_id"]:
            await interaction.response.send_message(
                "Chỉ người được cầu hôn mới bấm được nút này 😳",
                ephemeral=True,
            )
            return

        if not accepted:
            self.proposals.update_one(
                {"_id": proposal["_id"], "status": "pending"},
                {"$set": {"status": "declined"}},
            )
            embed = discord.Embed(
                title="💔 Từ chối cầu hôn",
                description=(
                    f"<@{proposal['target_id']}> đã từ chối lời cầu hôn của "
                    f"<@{proposal['proposer_id']}>."
                ),
                color=discord.Color.dark_grey(),
            )
            view = ProposeView(self)
            for child in view.children:
                child.disabled = True
            await interaction.response.edit_message(embed=embed, view=view)
            return

        # Accept path: re-check monogamy, then create marriage.
        guild_id = proposal["guild_id"]
        proposer_id = proposal["proposer_id"]
        target_id = proposal["target_id"]

        if self.find_active_marriage(guild_id, proposer_id) or self.find_active_marriage(
            guild_id, target_id
        ):
            self.proposals.update_one(
                {"_id": proposal["_id"]},
                {"$set": {"status": "cancelled"}},
            )
            await interaction.response.send_message(
                "Không thể kết hôn vì một trong hai đã có hôn nhân.",
                ephemeral=True,
            )
            return

        user_a, user_b = normalize_pair(proposer_id, target_id)
        married_at = _as_naive_utc(_utcnow())
        marriage_doc = {
            "guild_id": guild_id,
            "user_a": user_a,
            "user_b": user_b,
            "partner_ids": [user_a, user_b],
            "status": "active",
            "level": 1,
            "xp": 0,
            "rank_key": "bronze",
            "married_at": married_at,
            "divorced_at": None,
            "last_xp_at": None,
            "created_at": married_at,
            "updated_at": married_at,
        }

        # Mark proposal accepted first to reduce double-accept races.
        updated = self.proposals.find_one_and_update(
            {"_id": proposal["_id"], "status": "pending"},
            {"$set": {"status": "accepted"}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            await interaction.response.send_message(
                "Lời cầu hôn này đã được xử lý.",
                ephemeral=True,
            )
            return

        try:
            self.marriages.insert_one(marriage_doc)
        except Exception:
            logger.exception("Failed to insert marriage")
            self.proposals.update_one(
                {"_id": proposal["_id"]},
                {"$set": {"status": "pending"}},
            )
            await interaction.response.send_message(
                "Không lưu được hôn nhân. Thử lại sau nhé.",
                ephemeral=True,
            )
            return

        rank = rank_from_level(1)
        embed = discord.Embed(
            title="💍 Kết hôn thành công!",
            description=(
                f"<@{proposer_id}>  ❤️  <@{target_id}>\n"
                f"{rank.emoji} **{rank.display}** · Level **1** · 0 XP\n\n"
                f"Chúc hai bạn hạnh phúc! Dùng tương tác SFW với nhau để lên level nhé."
            ),
            color=rank.color,
        )
        view = ProposeView(self)
        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=view)

    @commands.command(
        name="divorce",
        help="Ly hôn với người bạn đời hiện tại (cần xác nhận).",
    )
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def divorce(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        marriage = self.find_active_marriage(ctx.guild.id, ctx.author.id)
        if marriage is None:
            await ctx.reply("Bạn chưa kết hôn trong server này.", mention_author=False)
            return

        partner_id = (
            marriage["user_b"]
            if marriage["user_a"] == ctx.author.id
            else marriage["user_a"]
        )
        embed = discord.Embed(
            title="⚠️ Xác nhận ly hôn?",
            description=(
                f"{ctx.author.mention} muốn ly hôn với <@{partner_id}>.\n"
                "Hành động này **không thể hoàn tác** (phải cầu hôn lại).\n"
                "Bấm xác nhận trong vòng 60 giây."
            ),
            color=discord.Color.orange(),
        )
        view = DivorceConfirmView(
            self,
            guild_id=ctx.guild.id,
            actor_id=ctx.author.id,
            marriage_id=marriage["_id"],
        )
        await ctx.send(embed=embed, view=view)

    async def finalize_divorce(
        self, interaction: discord.Interaction, view: DivorceConfirmView
    ) -> None:
        marriage = self.marriages.find_one_and_update(
            {
                "_id": view.marriage_id,
                "guild_id": view.guild_id,
                "status": "active",
                "partner_ids": view.actor_id,
            },
            {
                "$set": {
                    "status": "divorced",
                    "divorced_at": _as_naive_utc(_utcnow()),
                    "updated_at": _as_naive_utc(_utcnow()),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        for child in view.children:
            child.disabled = True

        if marriage is None:
            embed = discord.Embed(
                title="❌ Không thể ly hôn",
                description="Hôn nhân không còn hiệu lực.",
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=embed, view=view)
            view.stop()
            return

        partner_id = (
            marriage["user_b"]
            if marriage["user_a"] == view.actor_id
            else marriage["user_a"]
        )
        # Cancel any pending proposals involving either spouse.
        self.proposals.update_many(
            {
                "guild_id": view.guild_id,
                "status": "pending",
                "$or": [
                    {"proposer_id": {"$in": marriage["partner_ids"]}},
                    {"target_id": {"$in": marriage["partner_ids"]}},
                ],
            },
            {"$set": {"status": "cancelled"}},
        )

        embed = discord.Embed(
            title="💔 Đã ly hôn",
            description=(
                f"<@{view.actor_id}> và <@{partner_id}> đã chấm dứt hôn nhân.\n"
                "Hai bạn có thể `propose` với người mới khi sẵn sàng."
            ),
            color=discord.Color.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=view)
        view.stop()

    def _resolve_partner(
        self, guild: discord.Guild, user_id: int
    ) -> discord.abc.User:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        user = self.bot.get_user(user_id)
        if user is not None:
            return user

        class _LeftMember:
            """Minimal stand-in when the partner is no longer cached."""

            def __init__(self, uid: int, fallback: discord.Member) -> None:
                self.id = uid
                self.mention = f"<@{uid}>"
                self.display_name = str(uid)
                self.display_avatar = fallback.display_avatar

        # requester fallback set by caller via guild.me or first available member
        fallback = guild.me or next(iter(guild.members), None)
        if fallback is None:
            raise RuntimeError("Cannot resolve partner without a fallback avatar")
        return _LeftMember(user_id, fallback)  # type: ignore[return-value]

    @commands.command(
        name="marriage",
        aliases=["marry", "marriage_status"],
        help="Xem tình trạng hôn nhân của bạn hoặc member khác.",
    )
    @commands.guild_only()
    async def marriage_status(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        assert ctx.guild is not None
        target = member or ctx.author
        marriage = self.find_active_marriage(ctx.guild.id, target.id)
        if marriage is None:
            embed = discord.Embed(
                title="💍 Tình trạng hôn nhân",
                description=(
                    f"{target.mention} chưa kết hôn trong server này.\n"
                    f"Dùng `{ctx.prefix}propose @user` để cầu hôn."
                ),
                color=discord.Color.light_grey(),
            )
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            return

        partner_a = self._resolve_partner(ctx.guild, marriage["user_a"])
        partner_b = self._resolve_partner(ctx.guild, marriage["user_b"])
        embed = self.build_status_embed(
            requester=ctx.author,
            partner_a=partner_a,
            partner_b=partner_b,
            marriage=marriage,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def try_grant_couple_xp(
        self,
        guild_id: int,
        author_id: int,
        target_id: int,
        *,
        action: str,
        channel: discord.abc.Messageable | None = None,
    ) -> None:
        """Grant XP when two active spouses interact. Announce level/rank ups."""
        if author_id == target_id:
            return
        marriage = self.find_active_couple(guild_id, author_id, target_id)
        if marriage is None:
            return

        old_xp = int(marriage.get("xp", 0))
        old_level = int(marriage.get("level") or level_from_xp(old_xp))
        old_rank_key = marriage.get("rank_key") or rank_from_level(old_level).key

        new_xp = old_xp + XP_PER_INTERACTION
        new_level = level_from_xp(new_xp)
        new_rank = rank_from_xp(new_xp)

        updated = self.marriages.find_one_and_update(
            {"_id": marriage["_id"], "status": "active"},
            {
                "$set": {
                    "xp": new_xp,
                    "level": new_level,
                    "rank_key": new_rank.key,
                    "last_xp_at": _as_naive_utc(_utcnow()),
                    "updated_at": _as_naive_utc(_utcnow()),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None or channel is None:
            return

        messages: list[str] = []
        if new_level > old_level:
            messages.append(
                f"💍 Level up! Cặp đôi đạt **level {new_level}** "
                f"({new_rank.emoji} {new_rank.display})"
            )
        if new_rank.key != old_rank_key:
            messages.append(
                f"✨ Hạng mới: {new_rank.emoji} **{new_rank.display}**!"
            )
        if not messages:
            return
        try:
            await channel.send(
                "\n".join(messages),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to send marriage level-up message")

    async def cog_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Chậm thôi~ đợi **{error.retry_after:.1f}s** rồi thử lại ⏳",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            if ctx.command and ctx.command.name == "propose":
                await ctx.reply(
                    f"Cú pháp: `{ctx.prefix}propose @user`",
                    mention_author=False,
                )
                return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Không tìm thấy member đó.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh hôn nhân chỉ dùng trong server.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarriageCog(bot))
