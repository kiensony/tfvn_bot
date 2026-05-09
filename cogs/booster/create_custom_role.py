import discord
from discord.ext import commands


class BoosterCustomRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.collection = self.db["booster_custom_roles"]

    def _is_booster(self, member: discord.Member) -> bool:
        return member.premium_since is not None

    def _get_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if not self.bot.user:
            return None
        return guild.get_member(self.bot.user.id)

    async def _ensure_manage_roles(self, ctx: commands.Context) -> bool:
        bot_member = self._get_bot_member(ctx.guild)
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            await ctx.send("Bot đang thiếu quyền Manage Roles.")
            return False
        return True

    def _parse_hex_color(self, color_text: str) -> discord.Color | None:
        value = color_text.strip().lstrip("#")
        if len(value) != 6:
            return None
        try:
            return discord.Color(int(value, 16))
        except ValueError:
            return None

    def _get_png_attachment(
        self, message: discord.Message
    ) -> discord.Attachment | None:
        for attachment in message.attachments:
            if attachment.content_type == "image/png":
                return attachment
            if attachment.filename.lower().endswith(".png"):
                return attachment
        return None

    def _get_anchor_role(self, guild: discord.Guild) -> discord.Role | None:
        if not hasattr(self.bot, "global_vars"):
            return None

        anchor_value = self.bot.global_vars.get("BOOSTER_CUSTOM_ROLE_ANCHOR_ID")
        if not anchor_value:
            return None

        try:
            role_id = int(anchor_value)
        except (TypeError, ValueError):
            return None

        return guild.get_role(role_id)

    @commands.command(
        name="custom_role",
        aliases=["booster_role"],
        help="Tạo custom role cho booster.",
    )
    async def custom_role(
        self, ctx: commands.Context, color_hex: str, *, role_name: str
    ):
        if not ctx.guild:
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return

        if not self._is_booster(ctx.author):
            await ctx.send("Bạn cần là Booster để dùng lệnh này.")
            return

        role_name = role_name.strip()
        if not role_name:
            await ctx.send("Tên role không hợp lệ.")
            return

        if len(role_name) > 100:
            await ctx.send("Tên role tối đa 100 ký tự.")
            return

        color = self._parse_hex_color(color_hex)
        if not color:
            await ctx.send("Màu không hợp lệ. Dùng định dạng #RRGGBB.")
            return

        icon_attachment = None
        if ctx.message.attachments:
            icon_attachment = self._get_png_attachment(ctx.message)
            if not icon_attachment:
                await ctx.send("Vui lòng đính kèm file PNG nếu muốn đặt icon role.")
                return
            if icon_attachment.size > 256 * 1024:
                await ctx.send("Icon PNG tối đa 256KB.")
                return

        if not await self._ensure_manage_roles(ctx):
            return

        record = self.collection.find_one(
            {"guild_id": ctx.guild.id, "user_id": ctx.author.id}
        )
        role = None
        if record:
            role = ctx.guild.get_role(record.get("role_id"))

        if role:
            await ctx.send(
                "Bạn đã có custom role. Hãy dùng lệnh update_custom_role để cập nhật."
            )
            return

        bot_member = self._get_bot_member(ctx.guild)

        try:
            icon_bytes = None
            if icon_attachment:
                icon_bytes = await icon_attachment.read()

            role = await ctx.guild.create_role(
                name=role_name,
                colour=color,
                mentionable=False,
                display_icon=icon_bytes,
                reason=f"Booster custom role for {ctx.author} ({ctx.author.id})",
            )

            anchor_role = self._get_anchor_role(ctx.guild)
            if anchor_role:
                if bot_member and anchor_role >= bot_member.top_role:
                    await ctx.send(
                        "Không thể đặt role bên dưới anchor vì anchor cao hơn bot."
                    )
                else:
                    target_position = max(anchor_role.position - 1, 1)
                    try:
                        await role.edit(
                            position=target_position,
                            reason="Place booster custom role under anchor",
                        )
                    except discord.Forbidden:
                        await ctx.send(
                            "Bot không có quyền đặt thứ bậc role."
                        )
                    except discord.HTTPException:
                        await ctx.send("Không thể cập nhật thứ bậc role.")

            if bot_member and role >= bot_member.top_role:
                await ctx.send(
                    "Đã tạo role nhưng bot không thể gán vì thứ bậc role quá cao."
                )
            else:
                await ctx.author.add_roles(
                    role, reason="Assign booster custom role"
                )

            now = discord.utils.utcnow()
            self.collection.update_one(
                {"guild_id": ctx.guild.id, "user_id": ctx.author.id},
                {
                    "$set": {
                        "role_id": role.id,
                        "role_name": role.name,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )

            await ctx.send(
                f"Đã tạo custom role {role.mention} cho {ctx.author.mention}."
            )
        except discord.Forbidden:
            await ctx.send(
                "Bot không có quyền tạo hoặc gán role. Vui lòng kiểm tra quyền và thứ bậc role."
            )
        except discord.HTTPException:
            await ctx.send("Đã xảy ra lỗi khi tạo role.")


async def setup(bot: commands.Bot):
    await bot.add_cog(BoosterCustomRoleCog(bot))
