import datetime
import random
from collections import deque
import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]

from assets.gifs import (
    KISS_GIFS,
    HUG_GIFS,
    PAT_GIFS,
    POKE_GIFS,
    PUNCH_GIFS,
    SLAP_GIFS,
)

HIT_GIFS = SLAP_GIFS + PUNCH_GIFS


# tránh lặp lại gif gần đây
class GifPicker:
    def __init__(self, gifs: list[str], history_size: int = 5):
        self.gifs = gifs
        self.recent = deque(maxlen=history_size)

    def pick(self) -> str:
        candidates = [g for g in self.gifs if g not in self.recent]
        gif = random.choice(candidates or self.gifs)
        self.recent.append(gif)
        return gif


class UserInteractionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.kiss_picker = GifPicker(KISS_GIFS, history_size=5)
        self.hug_picker = GifPicker(HUG_GIFS, history_size=5)
        self.pat_picker = GifPicker(PAT_GIFS, history_size=5)
        self.slap_picker = GifPicker(SLAP_GIFS, history_size=5)
        self.punch_picker = GifPicker(PUNCH_GIFS, history_size=5)
        self.hit_picker = GifPicker(HIT_GIFS, history_size=5)
        self.poke_picker = GifPicker(POKE_GIFS, history_size=5)
        self.db = bot.db

    def record_action(self, action: str, ctx: commands.Context, member: discord.Member):
        document = {
            "message_id": ctx.message.id,
            "initMember": ctx.author.id,
            "targetMember": member.id,
            "action": action,
            "created_at": discord.utils.utcnow(),
        }
        self.db["interactions"].insert_one(document)

    # gọn gọn send embed
    async def _send_embed(
        self,
        ctx: commands.Context,
        *,
        title: str,
        description: str,
        gif_url: str | None = None,
    ):
        embed = discord.Embed(title=title, description=description)
        if gif_url:
            embed.set_image(url=gif_url)
        await ctx.send(embed=embed)

    # KISS
    @commands.command(name="kiss")
    async def kiss(self, ctx: commands.Context, member: discord.Member):

        self.record_action("kiss", ctx, member)

        await self._send_embed(
            ctx,
            title="💋 Moah moahhh~",
            description=f"{ctx.author.mention} hôn {member.mention} 💖",
            gif_url=self.kiss_picker.pick(),
        )

    # HUG
    @commands.command(name="hug")
    async def hug(self, ctx: commands.Context, member: discord.Member):

        self.record_action("hug", ctx, member)

        await self._send_embed(
            ctx,
            title="🤗 Ỏoooo, ôm cái nào!",
            description=f"{ctx.author.mention} ôm {member.mention} 🫂",
            gif_url=self.hug_picker.pick(),
        )

    # PAT
    @commands.command(name="pat")
    async def pat(self, ctx: commands.Context, member: discord.Member):

        self.record_action("pat", ctx, member)

        await self._send_embed(
            ctx,
            title="😉 Xoa đầu cái nha~",
            description=f"{ctx.author.mention} xoa đầu {member.mention} 🌸",
            gif_url=self.pat_picker.pick(),
        )

    # SLAP
    @commands.command(name="slap")
    async def slap(self, ctx: commands.Context, member: discord.Member):

        self.record_action("slap", ctx, member)

        await self._send_embed(
            ctx,
            title="🤬 Ăn tát đi!",
            description=f"{ctx.author.mention} tát {member.mention} 🤚🏻",
            gif_url=self.slap_picker.pick(),
        )

    # PUNCH
    @commands.command(name="punch")
    async def punch(self, ctx: commands.Context, member: discord.Member):

        self.record_action("punch", ctx, member)

        await self._send_embed(
            ctx,
            title="👊 Một đấm là nằm!",
            description=f"{ctx.author.mention} đấm {member.mention} 👊🏻",
            gif_url=self.punch_picker.pick(),
        )

    # HIT
    @commands.command(name="hit")
    async def hit(self, ctx: commands.Context, member: discord.Member):

        self.record_action("hit", ctx, member)

        await self._send_embed(
            ctx,
            title="💥 Bốp bốp!",
            description=f"{ctx.author.mention} đánh {member.mention} 🔨",
            gif_url=self.hit_picker.pick(),
        )

    # POKE
    @commands.command(name="poke")
    async def poke(self, ctx: commands.Context, member: discord.Member):

        self.record_action("poke", ctx, member)

        await self._send_embed(
            ctx,
            title="👉 Chọc chọc!",
            description=f"{ctx.author.mention} chọc {member.mention} 👉🏻",
            gif_url=self.poke_picker.pick(),
        )

    # AVATAR
    @commands.command(name="avatar", aliases=["av"])
    async def avatar(self, ctx: commands.Context, member: discord.Member | None = None):
        member = member or ctx.author
        await self._send_embed(
            ctx,
            title=f"📸 Avatar của {member.name}:",
            description="",
            gif_url=member.avatar.url,
        )

    @commands.command(name="rank", aliases=["ranking"])
    async def rank(
        self,
        ctx: commands.Context,
        mode_or_action: str | None = None,
        interaction_type: str | None = None,
    ):
        sfw_interactions = ["kiss", "hug", "pat", "slap", "punch", "hit", "poke"]

        # text cho NGƯỜI CHỦ ĐỘNG
        action_text_given = {
            "kiss": "hôn người khác",
            "hug": "ôm người khác",
            "pat": "xoa đầu người khác",
            "slap": "tát người khác",
            "punch": "đấm người khác",
            "hit": "đánh người khác",
            "poke": "chọc người khác",
        }

        # text cho NGƯỜI BỊ
        action_text_received = {
            "kiss": "được hôn",
            "hug": "được ôm",
            "pat": "được xoa đầu",
            "slap": "bị tát",
            "punch": "bị đấm",
            "hit": "bị đánh",
            "poke": "bị chọc",
        }

        # mặc định: người CHỦ ĐỘNG
        mode = "given"

        if mode_or_action == "r":
            mode = "received"
            action = interaction_type
        else:
            action = mode_or_action

        if action not in (sfw_interactions + [None]):
            await ctx.send(
                "Loại tương tác không hợp lệ.\nDùng: `kiss`, `hug`, `pat`, `slap`, `punch`, `hit`, `poke`."
            )
            return

        user_field = "$initMember" if mode == "given" else "$targetMember"

        pipeline = [
            {"$group": {"_id": user_field, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]

        if action:
            pipeline.insert(0, {"$match": {"action": action}})
        else:
            pipeline.insert(0, {"$match": {"action": {"$in": sfw_interactions}}})

        top_users = list(self.db["interactions"].aggregate(pipeline))

        lines = []
        for rank, record in enumerate(top_users, start=1):
            user_id = record["_id"]
            count = record["count"]

            user = self.bot.get_user(user_id)
            name = user.mention if user else f"ID {user_id}"

            if mode == "given":
                if action:
                    text = f"{count} lần {action_text_given[action]}."
                else:
                    text = f"{count} lần tương tác."
            else:
                if action:
                    text = f"{count} lần {action_text_received[action]}."
                else:
                    text = f"{count} lần bị tương tác."

            lines.append(f"**{rank}. {name}** – {text}")

        description = "\n".join(lines) if lines else "Chưa có dữ liệu."

        if mode == "given":
            title = "🏆 Top 10 người tương tác nhiều nhất"
            if action:
                title = f"🏆 Top 10 người {action_text_given[action]} nhiều nhất"
        else:
            title = "🏆 Top 10 người bị tương tác nhiều nhất"
            if action:
                title = f"🏆 Top 10 người {action_text_received[action]} nhiều nhất"

        embed = discord.Embed(title=title, description=description)
        embed.set_author(name="BXH tương tác", icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_image(
            url="https://cdn.discordapp.com/attachments/1382770560743903246/1456661155236806832/Untitled_design_37.png"
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInteractionCog(bot))
