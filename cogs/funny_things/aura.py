import discord
from discord.ext import commands

from cogs.funny_things._meter_helper import (
    fake_loading,
    get_daily_number,
    pick_tease,
)

# Positive aura sparkles / negative aura (dark)
AURA_ICON_POS = "✨"
AURA_ICON_NEG = "🌑"
AURA_ICON_ZERO = "⚪"
AURA_BAR_LENGTH = 10


def create_aura_icon_bar(aura_points, bar_length=AURA_BAR_LENGTH):
    """
    Icon bar by magnitude (not a percent progress bar).
    +points -> ✨✨✨...
    -points -> 🌑🌑🌑... (dark version)
    0       -> ⚪⚪...
    """
    if aura_points == 0:
        return AURA_ICON_ZERO * bar_length

    # Scale |points| 1..999 -> 1..bar_length icons
    filled = max(1, min(bar_length, round(abs(aura_points) / 999 * bar_length)))
    icon = AURA_ICON_POS if aura_points > 0 else AURA_ICON_NEG
    empty = "・" * (bar_length - filled)
    return icon * filled + empty


class AuraCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="aura", help="Báo cáo aura (+/- điểm) của một người dùng.")
    async def aura_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang quét aura... ⏳✨",
            done_message="Hoàn thành báo cáo aura! 🎉",
            emoji="✨",
        )

        # Aura is a point score from -999 to +999 (not percent)
        aura_points = get_daily_number(member.id, "aura", min_value=-999, max_value=999)

        if aura_points > 0:
            points_str = f"+{aura_points}"
        else:
            points_str = str(aura_points)

        icon_bar = create_aura_icon_bar(aura_points)

        tease = pick_tease(
            aura_points,
            [
                (-800, "Aura âm sâu. WiFi cũng né mày."),
                (-400, "Hơi mờ. Main character đang mute."),
                (1, "Trung tính. Không nấu cũng không bị nấu."),
                (400, "Phát sáng dương. Side character nhìn nể."),
                (800, "Aura chói. Chim bay vòng lấy mày làm GPS."),
                (None, "AURA THẦN THÁNH. Thực tại là fanfic về mày."),
            ],
        )

        # Darker embed color when aura is negative
        if aura_points < 0:
            color = discord.Color.from_rgb(40, 40, 60)
        elif aura_points == 0:
            color = discord.Color.from_rgb(150, 150, 160)
        else:
            color = discord.Color.from_rgb(255, 215, 80)

        embed = discord.Embed(
            title="✨ Aura Report",
            description=f"Báo cáo aura của {member.mention}",
            color=color,
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Kết quả:",
            value=f"{icon_bar}\n**{points_str} điểm**\n```{tease}```",
            inline=False,
        )
        embed.set_footer(text="Aura là tạm thời. Screenshot là mãi mãi. ✨")
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(AuraCog(bot))
