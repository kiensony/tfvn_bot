import asyncio
import logging

import discord
from discord.ext import commands

from cogs.booster._custom_resource_ui import (
    BoosterActionResult,
    BoosterRoleEditorView,
    RoleDesignDraft,
)
from cogs.booster._role_colors import RoleColorSpec, parse_role_color_args


logger = logging.getLogger(__name__)


class BoosterCustomRoleUpdateCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.collection = self.db["booster_custom_roles"]
        self._member_locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _is_booster(self, member: discord.Member) -> bool:
        return member.premium_since is not None

    def _get_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if not self.bot.user:
            return None
        return guild.get_member(self.bot.user.id)

    def _get_member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._member_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def _get_png_attachment(
        self,
        message: discord.Message,
    ) -> discord.Attachment | None:
        for attachment in message.attachments:
            if attachment.content_type == "image/png":
                return attachment
            if attachment.filename.lower().endswith(".png"):
                return attachment
        return None

    def _attachment_result(
        self,
        message: discord.Message,
    ) -> tuple[discord.Attachment | None, str | None]:
        if not message.attachments:
            return None, None
        attachment = self._get_png_attachment(message)
        if attachment is None:
            return None, "Vui lòng đính kèm file PNG nếu muốn đặt icon role."
        if attachment.size > 256 * 1024:
            return None, "Icon PNG tối đa 256KB."
        return attachment, None

    def _base_denial(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> str | None:
        if not self._is_booster(member):
            return "Bạn cần là Booster để dùng lệnh này."
        bot_member = self._get_bot_member(guild)
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            return "Bot đang thiếu quyền Manage Roles."
        return None

    async def _owned_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> tuple[dict | None, discord.Role | None, str | None]:
        try:
            record = self.collection.find_one(
                {"guild_id": guild.id, "user_id": member.id}
            )
        except Exception:
            logger.exception(
                "Could not read booster role record for guild %s user %s.",
                guild.id,
                member.id,
            )
            return None, None, "Không thể kiểm tra custom role lúc này."
        if not record:
            return None, None, "Bạn chưa có custom role. Hãy tạo trước."

        role_id = record.get("role_id")
        role = guild.get_role(role_id)
        if role is None and isinstance(role_id, int):
            try:
                fetched_roles = await guild.fetch_roles()
            except discord.HTTPException:
                logger.exception(
                    "Could not fetch booster role %s in guild %s.",
                    role_id,
                    guild.id,
                )
                return record, None, "Không thể kiểm tra role lúc này. Hãy thử lại."
            role = next(
                (
                    fetched_role
                    for fetched_role in fetched_roles
                    if fetched_role.id == role_id
                ),
                None,
            )
        if role is None:
            return record, None, "Không tìm thấy role. Hãy tạo lại custom role."
        if role.is_default() or role.managed:
            return (
                record,
                role,
                "Role đã lưu không thể chỉnh sửa vì do Discord quản lý.",
            )
        bot_member = self._get_bot_member(guild)
        if bot_member is None or role >= bot_member.top_role:
            return (
                record,
                role,
                "Bot không thể chỉnh sửa role này vì thứ bậc cao hơn bot.",
            )
        return record, role, None

    async def _read_icon(
        self,
        attachment: discord.Attachment | None,
    ) -> tuple[bytes | None, str | None]:
        if attachment is None:
            return None, None
        try:
            return await attachment.read(), None
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Could not read booster role update icon attachment.")
            return None, "Không thể đọc icon PNG. Vui lòng đính kèm lại và thử lại."

    def _record_color_spec(
        self,
        record: dict,
        role: discord.Role,
    ) -> RoleColorSpec:
        primary_value = record.get("primary_color", role.colour.value)
        secondary_value = record.get("secondary_color")
        try:
            primary = discord.Color(int(primary_value))
        except (TypeError, ValueError):
            primary = role.colour
        try:
            secondary = (
                discord.Color(int(secondary_value))
                if secondary_value is not None
                else None
            )
        except (TypeError, ValueError):
            secondary = role.secondary_colour
        return RoleColorSpec(primary=primary, secondary=secondary)

    async def _update_custom_role(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        color_spec: RoleColorSpec,
        role_name: str,
        icon_attachment: discord.Attachment | None,
    ) -> BoosterActionResult:
        role_name = role_name.strip()
        if not role_name:
            return BoosterActionResult(False, "Tên role không hợp lệ.")
        if len(role_name) > 100:
            return BoosterActionResult(False, "Tên role tối đa 100 ký tự.")

        lock = self._get_member_lock(guild.id, member.id)
        async with lock:
            denial = self._base_denial(guild, member)
            if denial:
                return BoosterActionResult(False, denial)
            _, role, denial = await self._owned_role(guild, member)
            if denial or role is None:
                return BoosterActionResult(False, denial or "Không tìm thấy role.")

            icon_bytes, icon_error = await self._read_icon(icon_attachment)
            if icon_error:
                return BoosterActionResult(False, icon_error)

            edit_kwargs: dict[str, object] = {
                "name": role_name,
                "reason": (
                    f"Booster custom role update for {member} ({member.id})"
                ),
                **color_spec.edit_kwargs(),
            }
            if icon_attachment is not None:
                edit_kwargs["display_icon"] = icon_bytes

            try:
                await role.edit(**edit_kwargs)
            except discord.Forbidden:
                return BoosterActionResult(
                    False,
                    "Bot không có quyền chỉnh sửa role. Vui lòng kiểm tra quyền và thứ bậc role.",
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not update booster role %s in guild %s for user %s.",
                    role.id,
                    guild.id,
                    member.id,
                )
                return BoosterActionResult(False, "Đã xảy ra lỗi khi cập nhật role.")

            warnings: list[str] = []
            if role not in member.roles:
                try:
                    await member.add_roles(
                        role,
                        reason="Assign booster custom role",
                    )
                except discord.Forbidden:
                    warnings.append(
                        "Role đã cập nhật nhưng bot chưa có quyền gán lại cho bạn."
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not reassign booster role %s in guild %s to user %s.",
                        role.id,
                        guild.id,
                        member.id,
                    )
                    warnings.append(
                        "Role đã cập nhật nhưng chưa thể gán lại do lỗi Discord."
                    )

            now = discord.utils.utcnow()
            try:
                self.collection.update_one(
                    {"guild_id": guild.id, "user_id": member.id},
                    {
                        "$set": {
                            "role_name": role_name,
                            **color_spec.record_fields(),
                            "updated_at": now,
                        }
                    },
                )
            except Exception:
                logger.exception(
                    "Could not persist booster role update %s in guild %s for user %s.",
                    role.id,
                    guild.id,
                    member.id,
                )
                warnings.append(
                    "Role đã cập nhật trên Discord nhưng chưa thể đồng bộ dữ liệu."
                )

            message = f"Đã cập nhật custom role: {role.mention}"
            if warnings:
                message += "\n⚠️ " + " ".join(warnings)
            return BoosterActionResult(True, message)

    async def _open_role_editor(
        self,
        ctx: commands.Context,
        icon_attachment: discord.Attachment | None,
    ) -> None:
        denial = self._base_denial(ctx.guild, ctx.author)
        if denial:
            await ctx.send(denial)
            return
        record, role, denial = await self._owned_role(ctx.guild, ctx.author)
        if denial or record is None or role is None:
            await ctx.send(denial or "Không tìm thấy custom role.")
            return

        async def submitter(
            interaction: discord.Interaction,
            draft: RoleDesignDraft,
        ) -> BoosterActionResult:
            if interaction.guild is None or interaction.guild.id != ctx.guild.id:
                return BoosterActionResult(
                    False,
                    "Server đã thay đổi. Hãy gọi lại lệnh trong server ban đầu.",
                )
            return await self._update_custom_role(
                guild=interaction.guild,
                member=interaction.user,
                color_spec=draft.color_spec,
                role_name=draft.role_name,
                icon_attachment=icon_attachment,
            )

        view = BoosterRoleEditorView(
            author_id=ctx.author.id,
            command_name="update_custom_role",
            submitter=submitter,
            default_role_name=role.name,
            initial_color_spec=self._record_color_spec(record, role),
            icon_attached=icon_attachment is not None,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False,
        )

    @commands.command(
        name="update_custom_role",
        aliases=["customroleupdate", "boosterroleupdate"],
        help=(
            "Cập nhật custom role; chạy không tham số để mở bảng chọn màu."
        ),
    )
    @commands.guild_only()
    async def update_custom_role(
        self,
        ctx: commands.Context,
        color_hex: str | None = None,
        *,
        role_name: str | None = None,
    ) -> None:
        icon_attachment, attachment_error = self._attachment_result(ctx.message)
        if attachment_error:
            await ctx.send(attachment_error)
            return

        if color_hex is None:
            if role_name is not None:
                await ctx.send(
                    f"Cách dùng: `{ctx.clean_prefix}update_custom_role <màu> <tên role>` "
                    "hoặc gọi lệnh không tham số để mở bảng chọn."
                )
                return
            await self._open_role_editor(ctx, icon_attachment)
            return

        if role_name is None:
            await ctx.send(
                f"Cách dùng: `{ctx.clean_prefix}update_custom_role <màu> <tên role>` "
                "hoặc gọi lệnh không tham số để mở bảng chọn."
            )
            return

        color_spec, parsed_role_name = parse_role_color_args(color_hex, role_name)
        if color_spec is None:
            await ctx.send(
                "Màu không hợp lệ. Dùng #RRGGBB hoặc #RRGGBB,#RRGGBB cho gradient."
            )
            return

        result = await self._update_custom_role(
            guild=ctx.guild,
            member=ctx.author,
            color_spec=color_spec,
            role_name=parsed_role_name,
            icon_attachment=icon_attachment,
        )
        await ctx.send(
            result.message,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @update_custom_role.error
    async def update_custom_role_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BoosterCustomRoleUpdateCog(bot))
