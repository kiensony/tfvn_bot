import random
from collections import deque

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]

from assets.gifs import (
    BITE_GIFS,
    BONK_GIFS,
    BOOP_GIFS,
    CUDDLE_GIFS,
    HANDHOLD_GIFS,
    HUG_GIFS,
    KISS_GIFS,
    PAT_GIFS,
    POKE_GIFS,
    PUNCH_GIFS,
    SLAP_GIFS,
    SNUGGLE_GIFS,
    STARE_GIFS,
)

HIT_GIFS = SLAP_GIFS + PUNCH_GIFS

SFW_INTERACTIONS = [
    "kiss",
    "hug",
    "pat",
    "slap",
    "punch",
    "hit",
    "poke",
    "cuddle",
    "snuggle",
    "boop",
    "handhold",
    "bonk",
    "bite",
    "stare",
]

ACTION_TEXT_GIVEN = {
    "kiss": "hôn người khác",
    "hug": "ôm người khác",
    "pat": "xoa đầu người khác",
    "slap": "tát người khác",
    "punch": "đấm người khác",
    "hit": "đánh người khác",
    "poke": "chọc người khác",
    "cuddle": "cuddle người khác",
    "snuggle": "snuggle người khác",
    "boop": "boop mũi người khác",
    "handhold": "nắm tay người khác",
    "bonk": "bonk người khác",
    "bite": "cắn người khác",
    "stare": "nhìn chằm chằm người khác",
}

ACTION_TEXT_RECEIVED = {
    "kiss": "được hôn",
    "hug": "được ôm",
    "pat": "được xoa đầu",
    "slap": "bị tát",
    "punch": "bị đấm",
    "hit": "bị đánh",
    "poke": "bị chọc",
    "cuddle": "được cuddle",
    "snuggle": "được snuggle",
    "boop": "bị boop mũi",
    "handhold": "được nắm tay",
    "bonk": "bị bonk",
    "bite": "bị cắn",
    "stare": "bị nhìn chằm chằm",
}


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
        self.cuddle_picker = GifPicker(CUDDLE_GIFS, history_size=5)
        self.snuggle_picker = GifPicker(SNUGGLE_GIFS, history_size=5)
        self.boop_picker = GifPicker(BOOP_GIFS, history_size=5)
        self.handhold_picker = GifPicker(HANDHOLD_GIFS, history_size=5)
        self.bonk_picker = GifPicker(BONK_GIFS, history_size=5)
        self.bite_picker = GifPicker(BITE_GIFS, history_size=5)
        self.stare_picker = GifPicker(STARE_GIFS, history_size=5)
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

    async def _do_interaction(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        action: str,
        title: str,
        description: str,
        picker: GifPicker,
    ) -> None:
        self.record_action(action, ctx, member)
        await self._send_embed(
            ctx,
            title=title,
            description=description,
            gif_url=picker.pick(),
        )

    @commands.command(name="kiss")
    async def kiss(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="kiss",
            title="💋 Moah moahhh~",
            description=f"{ctx.author.mention} hôn {member.mention} 💖",
            picker=self.kiss_picker,
        )

    @commands.command(name="hug")
    async def hug(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="hug",
            title="🤗 Ỏoooo, ôm cái nào!",
            description=f"{ctx.author.mention} ôm {member.mention} 🫂",
            picker=self.hug_picker,
        )

    @commands.command(name="pat")
    async def pat(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="pat",
            title="😉 Xoa đầu cái nha~",
            description=f"{ctx.author.mention} xoa đầu {member.mention} 🌸",
            picker=self.pat_picker,
        )

    @commands.command(name="slap")
    async def slap(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="slap",
            title="🤬 Ăn tát đi!",
            description=f"{ctx.author.mention} tát {member.mention} 🤚🏻",
            picker=self.slap_picker,
        )

    @commands.command(name="punch")
    async def punch(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="punch",
            title="👊 Một đấm là nằm!",
            description=f"{ctx.author.mention} đấm {member.mention} 👊🏻",
            picker=self.punch_picker,
        )

    @commands.command(name="hit")
    async def hit(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="hit",
            title="💥 Bốp bốp!",
            description=f"{ctx.author.mention} đánh {member.mention} 🔨",
            picker=self.hit_picker,
        )

    @commands.command(name="poke")
    async def poke(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="poke",
            title="👉 Chọc chọc!",
            description=f"{ctx.author.mention} chọc {member.mention} 👉🏻",
            picker=self.poke_picker,
        )

    @commands.command(name="cuddle")
    async def cuddle(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="cuddle",
            title="🥰 Cuddle nè~",
            description=f"{ctx.author.mention} cuddle {member.mention} 💕",
            picker=self.cuddle_picker,
        )

    @commands.command(name="snuggle")
    async def snuggle(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="snuggle",
            title="🐻 Snuggle chút nha~",
            description=f"{ctx.author.mention} snuggle {member.mention} 💗",
            picker=self.snuggle_picker,
        )

    @commands.command(name="boop")
    async def boop(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="boop",
            title="👆 Boop!",
            description=f"{ctx.author.mention} boop mũi {member.mention} 🐽",
            picker=self.boop_picker,
        )

    @commands.command(name="handhold", aliases=["holdhand"])
    async def handhold(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="handhold",
            title="🤝 Nắm tay nào~",
            description=f"{ctx.author.mention} nắm tay {member.mention} 💞",
            picker=self.handhold_picker,
        )

    @commands.command(name="bonk")
    async def bonk(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="bonk",
            title="🔨 Bonk!",
            description=f"{ctx.author.mention} bonk {member.mention} 💢",
            picker=self.bonk_picker,
        )

    @commands.command(name="bite", aliases=["nom"])
    async def bite(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="bite",
            title="🦷 Nham nham~",
            description=f"{ctx.author.mention} cắn {member.mention} 🤭",
            picker=self.bite_picker,
        )

    @commands.command(name="stare")
    async def stare(self, ctx: commands.Context, member: discord.Member):
        await self._do_interaction(
            ctx,
            member,
            action="stare",
            title="👀 ...",
            description=f"{ctx.author.mention} nhìn chằm chằm {member.mention} 😳",
            picker=self.stare_picker,
        )

    @commands.command(name="avatar", aliases=["av"])
    async def avatar(self, ctx: commands.Context, member: discord.Member | None = None):
        member = member or ctx.author
        await self._send_embed(
            ctx,
            title=f"📸 Avatar của {member.name}:",
            description="",
            gif_url=member.display_avatar.url,
        )

    @commands.command(name="rank", aliases=["ranking"])
    async def rank(
        self,
        ctx: commands.Context,
        mode_or_action: str | None = None,
        interaction_type: str | None = None,
    ):
        # mặc định: người CHỦ ĐỘNG
        mode = "given"

        if mode_or_action == "r":
            mode = "received"
            action = interaction_type
        else:
            action = mode_or_action

        if action not in (SFW_INTERACTIONS + [None]):
            actions_hint = ", ".join(f"`{name}`" for name in SFW_INTERACTIONS)
            await ctx.send(f"Loại tương tác không hợp lệ.\nDùng: {actions_hint}.")
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
            pipeline.insert(0, {"$match": {"action": {"$in": SFW_INTERACTIONS}}})

        top_users = list(self.db["interactions"].aggregate(pipeline))

        lines = []
        for rank, record in enumerate(top_users, start=1):
            user_id = record["_id"]
            count = record["count"]

            user = self.bot.get_user(user_id)
            name = user.mention if user else f"ID {user_id}"

            if mode == "given":
                if action:
                    text = f"{count} lần {ACTION_TEXT_GIVEN[action]}."
                else:
                    text = f"{count} lần tương tác."
            else:
                if action:
                    text = f"{count} lần {ACTION_TEXT_RECEIVED[action]}."
                else:
                    text = f"{count} lần bị tương tác."

            lines.append(f"**{rank}. {name}** – {text}")

        description = "\n".join(lines) if lines else "Chưa có dữ liệu."

        if mode == "given":
            title = "🏆 Top 10 người tương tác nhiều nhất"
            if action:
                title = f"🏆 Top 10 người {ACTION_TEXT_GIVEN[action]} nhiều nhất"
        else:
            title = "🏆 Top 10 người bị tương tác nhiều nhất"
            if action:
                title = f"🏆 Top 10 người {ACTION_TEXT_RECEIVED[action]} nhiều nhất"

        embed = discord.Embed(title=title, description=description)
        embed.set_author(name="BXH tương tác", icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_image(
            url="https://cdn.discordapp.com/attachments/1382770560743903246/1456661155236806832/Untitled_design_37.png"
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInteractionCog(bot))
