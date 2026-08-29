import json
import logging
from pathlib import Path

import discord
from discord.ext import commands


VERIFY_ROLE_ID_SETTING = "FALLEN_FEMBOY_ROLE_ID"
NSFW_CHANNEL_FILE = Path(__file__).resolve().parents[2] / "data" / "nsfw_channel.json"


class VerifiedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    def _verify_role_id_value(self) -> object | None:
        if not hasattr(self.bot, "global_vars"):
            return None

        return self.bot.global_vars.get(VERIFY_ROLE_ID_SETTING)

    def _parse_verify_role_id(self, role_id_value: object) -> int | None:
        try:
            return int(str(role_id_value).strip())
        except (TypeError, ValueError):
            self.logger.warning(
                "%s phải là Discord role ID hợp lệ.",
                VERIFY_ROLE_ID_SETTING,
            )
            return None

    def _bot_can_assign(self, guild: discord.Guild, role: discord.Role) -> bool:
        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)

        if bot_member is None:
            return False

        return (
            bot_member.guild_permissions.manage_roles
            and not role.managed
            and bot_member.top_role > role
        )

    def _author_can_assign(self, member: discord.Member, role: discord.Role) -> bool:
        if member.guild.owner_id == member.id:
            return True

        if member.guild_permissions.administrator:
            return True

        return member.guild_permissions.manage_roles and member.top_role > role

    def _load_nsfw_channels(self) -> list[dict[str, str]]:
        try:
            with NSFW_CHANNEL_FILE.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            self.logger.exception("Không thể đọc danh sách kênh NSFW.")
            return []

        raw_channels = payload.get("data", [])
        if not isinstance(raw_channels, list):
            self.logger.warning("Danh sách kênh NSFW không đúng định dạng.")
            return []

        channels: list[dict[str, str]] = []
        for item in raw_channels:
            if not isinstance(item, dict):
                continue

            channel_id = str(item.get("id", "")).strip()
            if not channel_id:
                continue

            channels.append(
                {
                    "id": channel_id,
                    "description": str(item.get("description", "")).strip(),
                }
            )

        return channels

    def _onboarding_embed(
        self,
        member: discord.Member,
        role: discord.Role,
        role_added: bool,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Sa ngã thành cong",
            description=(
                f"Chào mừng cưng {member.mention} đến với động bàn tơ.... "
                "dưới đây là hướng dẫn nhanh, Các bé tự tìm hiểu thêm có khi còn nhanh hơn ^^"
            ),
            color=discord.Color.from_rgb(255, 105, 180),
        )

        channels = self._load_nsfw_channels()
        if not channels:
            embed.add_field(
                name="Danh sách kênh NSFW",
                value="Chưa có kênh nào trong `data/nsfw_channel.json`.",
                inline=False,
            )
            return embed

        for index, channel in enumerate(channels, start=1):
            description = channel["description"] or "Không có mô tả."
            embed.add_field(
                name=f"{index}. <#{channel['id']}>",
                value=description,
                inline=False,
            )

        embed.set_footer(text="Chúc bạn chơi vui và nhớ đọc luật từng kênh nhé.")
        return embed

    def _offboarding_embed(
        self,
        member: discord.Member,
        role: discord.Role,
        role_removed: bool,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Hoàn lương thành cong",
            description=(
                f"Tạm biệt cưng {member.mention}, vé vào động bàn tơ đã bị thu hồi. "
                "Các kênh NSFW tạm đóng cửa với bé rồi, khi nào muốn sa ngã tiếp thì gọi min mót nha ^^"
            ),
            color=discord.Color.dark_grey(),
        )

        if not role_removed:
            embed.add_field(
                name="Trạng thái",
                value=f"{member.mention} chưa có role `{role.name}`.",
                inline=False,
            )

        return embed

    @commands.command(
        name="verified",
        help="Support gán verified role Fallen Femboy cho member.",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def verified(self, ctx: commands.Context, member: discord.Member):
        if ctx.guild is None:
            return

        role_id_value = self._verify_role_id_value()
        if not role_id_value:
            await ctx.reply(
                (
                    f"Mình chưa được cấu hình `{VERIFY_ROLE_ID_SETTING}` "
                    "trong settings."
                ),
                mention_author=False,
            )
            return

        role_id = self._parse_verify_role_id(role_id_value)
        if role_id is None:
            await ctx.reply(
                f"`{VERIFY_ROLE_ID_SETTING}` phải là Discord role ID hợp lệ.",
                mention_author=False,
            )
            return

        role = ctx.guild.get_role(role_id)
        if role is None:
            await ctx.reply(
                (
                    "Mình không tìm thấy role verify từ "
                    f"`{VERIFY_ROLE_ID_SETTING}` trong server."
                ),
                mention_author=False,
            )
            return

        if not isinstance(ctx.author, discord.Member):
            await ctx.reply(
                "Mình không tìm thấy thông tin mod/admin trong server.",
                mention_author=False,
            )
            return

        if not self._author_can_assign(ctx.author, role):
            await ctx.reply(
                f"Bạn không thể gán role `{role.name}` vì role này cao hơn hoặc ngang role của bạn.",
                mention_author=False,
            )
            return

        role_added = False
        if role not in member.roles:
            if not self._bot_can_assign(ctx.guild, role):
                await ctx.reply(
                    (
                        f"Mình chưa thể gán role `{role.name}`. "
                        "Hãy kiểm tra quyền Manage Roles và thứ bậc role của bot."
                    ),
                    mention_author=False,
                )
                return

            try:
                await member.add_roles(
                    role,
                    reason=f"Self verify via {ctx.command} by {member} ({member.id})",
                )
                role_added = True
            except discord.Forbidden:
                await ctx.reply(
                    (
                        f"Mình bị Discord từ chối khi gán role `{role.name}`. "
                        "Hãy kiểm tra quyền và thứ bậc role của bot."
                    ),
                    mention_author=False,
                )
                return
            except discord.HTTPException:
                self.logger.exception(
                    "Gán role verify thất bại cho %s (%s).",
                    member,
                    member.id,
                )
                await ctx.reply(
                    "Gán role verify thất bại, thử lại giúp mình sau nhé.",
                    mention_author=False,
                )
                return

        await ctx.reply(
            embed=self._onboarding_embed(member, role, role_added),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @verified.error
    async def verified_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn cần quyền Manage Roles để dùng lệnh support này.",
                mention_author=False,
            )
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"Cú pháp: `{self.bot.command_prefix}verified @user`.",
                mention_author=False,
            )
            return

        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Mình không tìm thấy member đó trong server.",
                mention_author=False,
            )
            return

        raise error

    @commands.command(
        name="unverified",
        help="Support gỡ verified role Fallen Femboy khỏi member.",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def unverified(self, ctx: commands.Context, member: discord.Member):
        if ctx.guild is None:
            return

        role_id_value = self._verify_role_id_value()
        if not role_id_value:
            await ctx.reply(
                (
                    f"Mình chưa được cấu hình `{VERIFY_ROLE_ID_SETTING}` "
                    "trong settings."
                ),
                mention_author=False,
            )
            return

        role_id = self._parse_verify_role_id(role_id_value)
        if role_id is None:
            await ctx.reply(
                f"`{VERIFY_ROLE_ID_SETTING}` phải là Discord role ID hợp lệ.",
                mention_author=False,
            )
            return

        role = ctx.guild.get_role(role_id)
        if role is None:
            await ctx.reply(
                (
                    "Mình không tìm thấy role verify từ "
                    f"`{VERIFY_ROLE_ID_SETTING}` trong server."
                ),
                mention_author=False,
            )
            return

        if not isinstance(ctx.author, discord.Member):
            await ctx.reply(
                "Mình không tìm thấy thông tin mod/admin trong server.",
                mention_author=False,
            )
            return

        if not self._author_can_assign(ctx.author, role):
            await ctx.reply(
                f"Bạn không thể gỡ role `{role.name}` vì role này cao hơn hoặc ngang role của bạn.",
                mention_author=False,
            )
            return

        role_removed = False
        if role in member.roles:
            if not self._bot_can_assign(ctx.guild, role):
                await ctx.reply(
                    (
                        f"Mình chưa thể gỡ role `{role.name}`. "
                        "Hãy kiểm tra quyền Manage Roles và thứ bậc role của bot."
                    ),
                    mention_author=False,
                )
                return

            try:
                await member.remove_roles(
                    role,
                    reason=f"Support unverified via {ctx.command} by {ctx.author} ({ctx.author.id})",
                )
                role_removed = True
            except discord.Forbidden:
                await ctx.reply(
                    (
                        f"Mình bị Discord từ chối khi gỡ role `{role.name}`. "
                        "Hãy kiểm tra quyền và thứ bậc role của bot."
                    ),
                    mention_author=False,
                )
                return
            except discord.HTTPException:
                self.logger.exception(
                    "Gỡ role verify thất bại cho %s (%s).",
                    member,
                    member.id,
                )
                await ctx.reply(
                    "Gỡ role verify thất bại, thử lại giúp mình sau nhé.",
                    mention_author=False,
                )
                return

        await ctx.reply(
            embed=self._offboarding_embed(member, role, role_removed),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @unverified.error
    async def unverified_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn cần quyền Manage Roles để dùng lệnh support này.",
                mention_author=False,
            )
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"Cú pháp: `{self.bot.command_prefix}unverified @user`.",
                mention_author=False,
            )
            return

        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Mình không tìm thấy member đó trong server.",
                mention_author=False,
            )
            return

        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifiedCog(bot))
