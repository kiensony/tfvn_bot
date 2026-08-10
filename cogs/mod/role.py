import logging

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)

ROLE_ROLL_SELECT_CUSTOM_ID = "roleroll:role"
ROLE_UNROLL_SELECT_CUSTOM_ID = "roleunroll:role"
ROLE_MENU_TIMEOUT_SECONDS = 60
ROLE_ROLL_TIMEOUT_SECONDS = ROLE_MENU_TIMEOUT_SECONDS


def _role_change_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
    role: discord.Role,
    *,
    remove: bool,
) -> str | None:
    action = "gỡ" if remove else "gán"
    if role.guild.id != guild.id:
        return "Role đã chọn không thuộc server này."
    if role.is_default():
        return f"Không thể {action} role mặc định `@everyone`."
    if role.managed:
        return (
            "Role này do Discord hoặc integration quản lý nên "
            f"không thể {action} thủ công."
        )

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return f"Bot không có quyền Manage Roles để {action} role."
    if bot_member.id != guild.owner_id and role >= bot_member.top_role:
        return "Role đã chọn phải thấp hơn role cao nhất của bot."

    if not moderator.guild_permissions.manage_roles:
        return f"Bạn không còn quyền Manage Roles để {action} role."
    if moderator.id != guild.owner_id and role >= moderator.top_role:
        return f"Bạn chỉ có thể {action} role thấp hơn role cao nhất của mình."

    if remove and role not in target.roles:
        return f"{target.mention} không có role {role.mention}."
    if not remove and role in target.roles:
        return f"{target.mention} đã có role {role.mention} rồi."
    return None


def role_assignment_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
    role: discord.Role,
) -> str | None:
    """Return a user-facing reason when a selected role cannot be assigned."""
    return _role_change_denial(
        guild,
        moderator,
        target,
        role,
        remove=False,
    )


def role_removal_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
    role: discord.Role,
) -> str | None:
    """Return a user-facing reason when a selected role cannot be removed."""
    return _role_change_denial(
        guild,
        moderator,
        target,
        role,
        remove=True,
    )


class RoleChangeSelect(discord.ui.RoleSelect):
    def __init__(self, role_view: "RoleChangeView") -> None:
        self.role_view = role_view
        super().__init__(
            custom_id=role_view.select_custom_id,
            placeholder=f"Chọn role muốn {role_view.action}",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.role_view.apply_role(interaction, self.values[0])


class RoleChangeView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        target: discord.Member,
        remove: bool,
    ) -> None:
        super().__init__(timeout=ROLE_MENU_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.target_id = target.id
        self.remove = remove
        self.command_name = "roleunroll" if remove else "roleroll"
        self.action = "gỡ" if remove else "gán"
        self.select_custom_id = (
            ROLE_UNROLL_SELECT_CUSTOM_ID if remove else ROLE_ROLL_SELECT_CUSTOM_ID
        )
        self.message: discord.Message | None = None
        self.completed = False
        self.role_select = RoleChangeSelect(self)
        self.add_item(self.role_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"Chỉ người đã gọi lệnh {self.command_name} mới có thể chọn role.",
                ephemeral=True,
            )
            return False

        guild_permissions = getattr(interaction.user, "guild_permissions", None)
        if guild_permissions is None or not guild_permissions.manage_roles:
            await interaction.response.send_message(
                "Bạn không còn quyền Manage Roles để dùng menu này.",
                ephemeral=True,
            )
            return False
        return True

    async def apply_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        if self.completed:
            await interaction.response.send_message(
                "Menu chọn role này đã được sử dụng.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Menu chọn role chỉ dùng được trong server.",
                ephemeral=True,
            )
            return

        moderator = interaction.user
        target = guild.get_member(self.target_id)
        if target is None:
            await interaction.response.send_message(
                f"Thành viên cần {self.action} role không còn ở trong server.",
                ephemeral=True,
            )
            return

        denial = (
            role_removal_denial(guild, moderator, target, role)
            if self.remove
            else role_assignment_denial(guild, moderator, target, role)
        )
        if denial is not None:
            await interaction.response.send_message(
                denial,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        # Set this before the first await so two rapid selections cannot mutate twice.
        self.completed = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.completed = False
            raise

        try:
            if self.remove:
                await target.remove_roles(
                    role,
                    reason=f"roleunroll by moderator {moderator.id}",
                )
            else:
                await target.add_roles(
                    role,
                    reason=f"roleroll by moderator {moderator.id}",
                )
        except discord.NotFound:
            self.completed = False
            await interaction.followup.send(
                "Thành viên hoặc role không còn tồn tại.",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            self.completed = False
            await interaction.followup.send(
                (
                    f"Bot không thể {self.action} role này. "
                    "Hãy kiểm tra quyền và thứ bậc role."
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            self.completed = False
            logger.exception(
                "Discord rejected %s target=%s role=%s moderator=%s",
                self.command_name,
                target.id,
                role.id,
                moderator.id,
            )
            await interaction.followup.send(
                "Discord từ chối cập nhật role. Vui lòng thử lại.",
                ephemeral=True,
            )
            return

        for item in self.children:
            item.disabled = True
        success_message = (
            f"Đã gỡ {role.mention} khỏi {target.mention} thành công!"
            if self.remove
            else f"Đã gán {role.mention} cho {target.mention} thành công!"
        )
        try:
            await interaction.edit_original_response(
                content=success_message,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception(
                "Could not update %s message target=%s role=%s moderator=%s",
                self.command_name,
                target.id,
                role.id,
                moderator.id,
            )
            try:
                await interaction.followup.send(
                    (
                        f"Đã {self.action} role thành công nhưng "
                        "không thể cập nhật menu."
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not send %s completion follow-up",
                    self.command_name,
                )
        finally:
            self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class RoleRollView(RoleChangeView):
    def __init__(self, *, author_id: int, target: discord.Member) -> None:
        super().__init__(author_id=author_id, target=target, remove=False)

    async def assign_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        await self.apply_role(interaction, role)


class RoleUnrollView(RoleChangeView):
    def __init__(self, *, author_id: int, target: discord.Member) -> None:
        super().__init__(author_id=author_id, target=target, remove=True)

    async def remove_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        await self.apply_role(interaction, role)


class RollCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _open_role_menu(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        remove: bool,
    ) -> None:
        action = "gỡ" if remove else "gán"
        bot_member = ctx.guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            await ctx.reply(
                f"Bot không có quyền Manage Roles để {action} role.",
                mention_author=False,
            )
            return

        view = (
            RoleUnrollView(author_id=ctx.author.id, target=member)
            if remove
            else RoleRollView(author_id=ctx.author.id, target=member)
        )
        prompt = (
            f"Chọn role muốn gỡ khỏi {member.mention}:"
            if remove
            else f"Chọn role muốn gán cho {member.mention}:"
        )
        view.message = await ctx.reply(
            prompt,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False,
        )

    async def _handle_role_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
        *,
        command_name: str,
        remove: bool,
    ) -> None:
        action = "gỡ" if remove else "gán"
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                f"Bạn không có quyền Manage Roles để {action} role.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"Cách dùng: `{ctx.clean_prefix}{command_name} @user`",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Mình không tìm thấy thành viên đó trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Hãy mention một thành viên hợp lệ trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                f"Lệnh {command_name} chỉ dùng được trong server.",
                mention_author=False,
            )
            return
        logger.error(
            "Unhandled %s command error",
            command_name,
            exc_info=(type(error), error, error.__traceback__),
        )
        await ctx.reply(
            "Đã xảy ra lỗi khi mở menu chọn role.",
            mention_author=False,
        )

    @commands.command(
        name="roleroll",
        help="Mở menu chọn role để gán cho thành viên.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def give_role(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ) -> None:
        await self._open_role_menu(ctx, member, remove=False)

    @give_role.error
    async def give_role_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        await self._handle_role_command_error(
            ctx,
            error,
            command_name="roleroll",
            remove=False,
        )

    @commands.command(
        name="roleunroll",
        help="Mở menu chọn role để gỡ khỏi thành viên.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def remove_role(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ) -> None:
        await self._open_role_menu(ctx, member, remove=True)

    @remove_role.error
    async def remove_role_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        await self._handle_role_command_error(
            ctx,
            error,
            command_name="roleunroll",
            remove=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollCog(bot))
