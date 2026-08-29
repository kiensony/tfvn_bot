import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import discord
from discord.ext import commands

from cogs.mod._member_state import ACTIVE_ROLE_MUTATION_TARGETS


logger = logging.getLogger(__name__)

VERIFY_ROLE_ID_SETTING = "FALLEN_FEMBOY_ROLE_ID"
NSFW_CHANNEL_FILE = Path(__file__).resolve().parents[2] / "data" / "nsfw_channel.json"
SELF_UNVERIFIED_TIMEOUT_SECONDS = 60
SELF_UNVERIFIED_COOLDOWN_SECONDS = 30
SELF_UNVERIFIED_CONFIRM_CUSTOM_ID = "self-unverified:confirm"
SELF_UNVERIFIED_CANCEL_CUSTOM_ID = "self-unverified:cancel"

NO_MENTIONS = discord.AllowedMentions.none()

SelfUnverifiedSubmitter = Callable[
    [discord.Interaction],
    Awaitable["SelfUnverifiedResult"],
]


@dataclass(frozen=True)
class SelfUnverifiedResult:
    completed: bool
    message: str | None = None
    embed: discord.Embed | None = None


def build_self_unverified_confirm_embed(
    member: discord.Member,
    role: discord.Role,
    *,
    command_display: str,
) -> discord.Embed:
    return discord.Embed(
        title="⚠️ Xác nhận hoàn lương?",
        description=(
            f"{member.mention} muốn tự gỡ role `{role.name}`.\n"
            "Sau khi xác nhận, các kênh NSFW sẽ đóng với bạn.\n"
            "**Muốn sa ngã lại thì phải hỏi min mót**, không tự lấy role này được.\n"
            f"Bấm xác nhận trong vòng {SELF_UNVERIFIED_TIMEOUT_SECONDS} giây. "
            f"Hết hạn hoặc hủy thì gọi lại `{command_display}`."
        ),
        color=discord.Color.orange(),
    )


def build_self_unverified_cancel_embed(*, command_display: str) -> discord.Embed:
    return discord.Embed(
        title="❎ Đã hủy hoàn lương",
        description=(
            "Role xác minh vẫn còn. "
            f"Gọi lại `{command_display}` nếu bạn đổi ý."
        ),
        color=discord.Color.green(),
    )


def build_self_unverified_timeout_embed(*, command_display: str) -> discord.Embed:
    return discord.Embed(
        title="⌛ Hết thời gian xác nhận",
        description=(
            "Bảng xác nhận đã hết hạn. "
            f"Gọi lại `{command_display}` nếu bạn vẫn muốn hoàn lương."
        ),
        color=discord.Color.dark_grey(),
    )


class SelfUnverifiedConfirmView(discord.ui.View):
    """Timeout-bound confirm view; cancel or expiry requires a new command."""

    def __init__(
        self,
        *,
        author_id: int,
        command_display: str,
        submitter: SelfUnverifiedSubmitter,
    ) -> None:
        super().__init__(timeout=SELF_UNVERIFIED_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.command_display = command_display
        self.submitter = submitter
        self.message: discord.Message | None = None
        self.completed = False
        self.submitting = False
        self._lock = asyncio.Lock()

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Chỉ người đã gọi lệnh này mới bấm được nút.",
                ephemeral=True,
            )
            return False
        if self.completed:
            await interaction.response.send_message(
                (
                    "Bảng này đã hoàn tất. "
                    f"Gọi lại `{self.command_display}` nếu bạn muốn thao tác tiếp."
                ),
                ephemeral=True,
            )
            return False
        if self.submitting:
            await interaction.response.send_message(
                "Yêu cầu đang được xử lý, vui lòng chờ một chút.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Xác nhận hoàn lương",
        style=discord.ButtonStyle.danger,
        emoji="🚪",
        custom_id=SELF_UNVERIFIED_CONFIRM_CUSTOM_ID,
    )
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.confirm(interaction)

    @discord.ui.button(
        label="Hủy",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
        custom_id=SELF_UNVERIFIED_CANCEL_CUSTOM_ID,
    )
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cancel(interaction)

    async def confirm(self, interaction: discord.Interaction) -> None:
        if self._lock.locked():
            await interaction.response.send_message(
                "Bảng đang xử lý một tương tác khác. Hãy thử lại.",
                ephemeral=True,
            )
            return
        async with self._lock:
            await self._confirm_unlocked(interaction)

    async def _confirm_unlocked(self, interaction: discord.Interaction) -> None:
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                (
                    "Bảng này đã hoàn tất hoặc hết hạn. "
                    f"Gọi lại `{self.command_display}` nếu bạn vẫn muốn hoàn lương."
                ),
                ephemeral=True,
            )
            return
        if self.submitting:
            await interaction.response.send_message(
                "Yêu cầu đang được xử lý, vui lòng chờ một chút.",
                ephemeral=True,
            )
            return

        self.submitting = True
        try:
            await interaction.response.defer()
        except Exception:
            self.submitting = False
            raise

        try:
            result = await self.submitter(interaction)
        except Exception:
            self.submitting = False
            logger.exception(
                "Unexpected self_unverified submit failure user=%s",
                self.author_id,
            )
            try:
                await interaction.followup.send(
                    "Đã xảy ra lỗi ngoài dự kiến. Vui lòng thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not report self_unverified submit failure user=%s",
                    self.author_id,
                )
            return

        if not result.completed:
            self.submitting = False
            try:
                await interaction.followup.send(
                    result.message or "Không thể hoàn lương lúc này. Hãy thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not report retryable self_unverified result user=%s",
                    self.author_id,
                )
            return

        self.completed = True
        self.disable_all()
        try:
            updated = False
            edit_kwargs: dict[str, object] = {
                "view": self,
                "allowed_mentions": NO_MENTIONS,
            }
            if result.embed is not None:
                edit_kwargs["embed"] = result.embed
                if result.message is not None:
                    edit_kwargs["content"] = result.message
            else:
                edit_kwargs["content"] = (
                    result.message or "Đã gỡ role xác minh."
                )
                edit_kwargs["embed"] = None
            try:
                await interaction.edit_original_response(**edit_kwargs)
                updated = True
            except discord.HTTPException:
                logger.exception(
                    "Could not update completed self_unverified UI user=%s",
                    self.author_id,
                )
            if not updated and self.message is not None:
                try:
                    await self.message.edit(**edit_kwargs)
                    updated = True
                except discord.HTTPException:
                    logger.exception(
                        "Could not update stored self_unverified message user=%s",
                        self.author_id,
                    )
            if not updated:
                try:
                    await interaction.followup.send(
                        result.message or "Đã gỡ role xác minh.",
                        embed=result.embed,
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not deliver self_unverified result user=%s",
                        self.author_id,
                    )
        finally:
            self.stop()

    async def cancel(self, interaction: discord.Interaction) -> None:
        if self._lock.locked():
            await interaction.response.send_message(
                "Bảng đang xử lý một tương tác khác. Hãy thử lại.",
                ephemeral=True,
            )
            return
        async with self._lock:
            if self.completed or self.is_finished():
                await interaction.response.send_message(
                    (
                        "Bảng này đã hoàn tất hoặc hết hạn. "
                        f"Gọi lại `{self.command_display}` nếu bạn vẫn muốn hoàn lương."
                    ),
                    ephemeral=True,
                )
                return
            self.completed = True
            self.disable_all()
            self.stop()
            await interaction.response.edit_message(
                embed=build_self_unverified_cancel_embed(
                    command_display=self.command_display,
                ),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )

    async def on_timeout(self) -> None:
        if self.completed:
            return
        self.disable_all()
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=build_self_unverified_timeout_embed(
                    command_display=self.command_display,
                ),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not disable expired self_unverified UI user=%s",
                self.author_id,
                exc_info=True,
            )


class VerifiedCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.logger = logger

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

    def _resolve_verify_role(
        self,
        guild: discord.Guild,
    ) -> tuple[discord.Role | None, str | None]:
        role_id_value = self._verify_role_id_value()
        if not role_id_value:
            return (
                None,
                (
                    f"Mình chưa được cấu hình `{VERIFY_ROLE_ID_SETTING}` "
                    "trong settings."
                ),
            )

        role_id = self._parse_verify_role_id(role_id_value)
        if role_id is None:
            return (
                None,
                f"`{VERIFY_ROLE_ID_SETTING}` phải là Discord role ID hợp lệ.",
            )

        role = guild.get_role(role_id)
        if role is None:
            return (
                None,
                (
                    "Mình không tìm thấy role verify từ "
                    f"`{VERIFY_ROLE_ID_SETTING}` trong server."
                ),
            )

        return role, None

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

    def _self_offboarding_embed(
        self,
        member: discord.Member,
        role: discord.Role,
        role_removed: bool,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Hoàn lương thành công",
            description=(
                f"Tạm biệt cưng {member.mention}, vé vào động bàn tơ đã bị thu hồi. "
                "Các kênh NSFW tạm đóng cửa với bé rồi.\n"
                "**Muốn sa ngã tiếp thì phải hỏi min mót gán lại**, "
                "không tự lấy role này được."
            ),
            color=discord.Color.dark_grey(),
        )

        if not role_removed:
            embed.add_field(
                name="Trạng thái",
                value=(
                    f"{member.mention} chưa có role `{role.name}`. "
                    "Hãy hỏi min mót nếu muốn sa ngã lại."
                ),
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

        role, error = self._resolve_verify_role(ctx.guild)
        if error is not None or role is None:
            await ctx.reply(error or "Không tìm thấy role xác minh.", mention_author=False)
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

        role, error = self._resolve_verify_role(ctx.guild)
        if error is not None or role is None:
            await ctx.reply(error or "Không tìm thấy role xác minh.", mention_author=False)
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

    async def _refresh_member(
        self,
        guild: discord.Guild,
        member_id: int,
    ) -> tuple[discord.Member | None, SelfUnverifiedResult | None]:
        member = guild.get_member(member_id)
        if member is not None:
            return member, None

        try:
            return await guild.fetch_member(member_id), None
        except discord.NotFound:
            return None, SelfUnverifiedResult(
                True,
                message="Bạn không còn trong server này.",
            )
        except discord.Forbidden:
            return None, SelfUnverifiedResult(
                False,
                message="Bot không thể tải lại thông tin thành viên.",
            )
        except discord.HTTPException:
            self.logger.exception(
                "Could not refresh self_unverified member=%s",
                member_id,
            )
            return None, SelfUnverifiedResult(
                False,
                message="Không thể kiểm tra thành viên lúc này.",
            )

    async def _submit_self_unverified(
        self,
        interaction: discord.Interaction,
    ) -> SelfUnverifiedResult:
        guild = interaction.guild
        if guild is None:
            return SelfUnverifiedResult(
                False,
                message="Lệnh này chỉ dùng được trong server.",
            )

        role, error = self._resolve_verify_role(guild)
        if error is not None or role is None:
            return SelfUnverifiedResult(
                False,
                message=error or "Không tìm thấy role xác minh.",
            )

        member, refresh_error = await self._refresh_member(
            guild,
            interaction.user.id,
        )
        if refresh_error is not None:
            return refresh_error
        if member is None:
            return SelfUnverifiedResult(
                False,
                message="Không thể kiểm tra thành viên lúc này.",
            )

        if role not in member.roles:
            return SelfUnverifiedResult(
                True,
                embed=self._self_offboarding_embed(member, role, role_removed=False),
            )

        if not self._bot_can_assign(guild, role):
            return SelfUnverifiedResult(
                False,
                message=(
                    f"Mình chưa thể gỡ role `{role.name}`. "
                    "Hãy kiểm tra quyền Manage Roles và thứ bậc role của bot, "
                    "hoặc nhờ min mót gỡ giúp."
                ),
            )

        key = (guild.id, member.id)
        if key in ACTIVE_ROLE_MUTATION_TARGETS:
            return SelfUnverifiedResult(
                False,
                message="Một thao tác role khác cho bạn đang chạy. Hãy thử lại.",
            )
        ACTIVE_ROLE_MUTATION_TARGETS.add(key)
        try:
            await member.remove_roles(
                role,
                reason=(
                    f"Self unverified via self_unverified by {member} ({member.id})"
                ),
            )
        except discord.NotFound:
            return SelfUnverifiedResult(
                True,
                message="Thành viên hoặc role xác minh không còn tồn tại.",
            )
        except discord.Forbidden:
            return SelfUnverifiedResult(
                False,
                message=(
                    f"Mình bị Discord từ chối khi gỡ role `{role.name}`. "
                    "Hãy kiểm tra quyền và thứ bậc role của bot, "
                    "hoặc nhờ min mót gỡ giúp."
                ),
            )
        except discord.HTTPException:
            self.logger.exception(
                "Gỡ role verify thất bại cho %s (%s).",
                member,
                member.id,
            )
            return SelfUnverifiedResult(
                False,
                message="Gỡ role verify thất bại, thử lại giúp mình sau nhé.",
            )
        finally:
            ACTIVE_ROLE_MUTATION_TARGETS.discard(key)

        return SelfUnverifiedResult(
            True,
            embed=self._self_offboarding_embed(member, role, role_removed=True),
        )

    async def _open_self_unverified(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ) -> None:
        if ctx.guild is None:
            return

        role, error = self._resolve_verify_role(ctx.guild)
        if error is not None or role is None:
            await ctx.reply(
                error or "Không tìm thấy role xác minh.",
                mention_author=False,
            )
            return

        if role not in member.roles:
            await ctx.reply(
                (
                    f"Bạn chưa có role `{role.name}`. "
                    "Hãy hỏi min mót nếu muốn sa ngã lại."
                ),
                mention_author=False,
            )
            return

        if not self._bot_can_assign(ctx.guild, role):
            await ctx.reply(
                (
                    f"Mình chưa thể gỡ role `{role.name}`. "
                    "Hãy kiểm tra quyền Manage Roles và thứ bậc role của bot, "
                    "hoặc nhờ min mót gỡ giúp."
                ),
                mention_author=False,
            )
            return

        command_display = f"{ctx.clean_prefix}self_unverified"
        view = SelfUnverifiedConfirmView(
            author_id=member.id,
            command_display=command_display,
            submitter=self._submit_self_unverified,
        )
        view.message = await ctx.reply(
            embed=build_self_unverified_confirm_embed(
                member,
                role,
                command_display=command_display,
            ),
            view=view,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )

    @commands.command(
        name="self_unverified",
        help=(
            "Tự gỡ role xác minh sau khi xác nhận. "
            "Muốn vào lại phải hỏi min mót."
        ),
    )
    @commands.guild_only()
    @commands.cooldown(
        1,
        SELF_UNVERIFIED_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def self_unverified(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return
        if not isinstance(ctx.author, discord.Member):
            await ctx.reply(
                "Lệnh này chỉ dùng trong server.",
                mention_author=False,
            )
            return
        await self._open_self_unverified(ctx, ctx.author)

    @self_unverified.error
    async def self_unverified_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                (
                    "Hãy thử mở bảng hoàn lương lại sau "
                    f"{error.retry_after:.1f} giây."
                ),
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh này chỉ dùng trong server.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VerifiedCog(bot))
