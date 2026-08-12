import logging
from dataclasses import dataclass

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)

ROLE_ROLL_SELECT_CUSTOM_ID = "roleroll:role"
ROLE_UNROLL_SELECT_CUSTOM_ID = "roleunroll:role"
ROLE_MENU_TIMEOUT_SECONDS = 60
ROLE_ROLL_TIMEOUT_SECONDS = ROLE_MENU_TIMEOUT_SECONDS
ROLE_COPY_COOLDOWN_SECONDS = 15


def _role_manageability_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    role: discord.Role,
    *,
    action: str,
) -> str | None:
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
    return None


def _role_change_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
    role: discord.Role,
    *,
    remove: bool,
) -> str | None:
    action = "gỡ" if remove else "gán"
    denial = _role_manageability_denial(
        guild,
        moderator,
        role,
        action=action,
    )
    if denial is not None:
        return denial

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


@dataclass(frozen=True)
class RoleCopyPlan:
    eligible: tuple[discord.Role, ...]
    already_present: tuple[discord.Role, ...]
    unmanageable: tuple[discord.Role, ...]


def plan_role_copy(
    guild: discord.Guild,
    moderator: discord.Member,
    source: discord.Member,
    target: discord.Member,
) -> RoleCopyPlan:
    """Classify source roles for an additive, hierarchy-safe copy."""
    eligible: list[discord.Role] = []
    already_present: list[discord.Role] = []
    unmanageable: list[discord.Role] = []

    for role in source.roles:
        denial = _role_manageability_denial(
            guild,
            moderator,
            role,
            action="gán",
        )
        if denial is not None:
            unmanageable.append(role)
        elif role in target.roles:
            already_present.append(role)
        else:
            eligible.append(role)

    return RoleCopyPlan(
        eligible=tuple(eligible),
        already_present=tuple(already_present),
        unmanageable=tuple(unmanageable),
    )


def _format_role_copy_result(
    source: discord.Member,
    target: discord.Member,
    plan: RoleCopyPlan,
    copied: list[discord.Role],
    failed: list[discord.Role],
    not_attempted: list[discord.Role],
    stop_reason: str | None,
) -> str:
    if copied:
        lines = [
            (
                f"Đã sao chép **{len(copied)}** role từ {source.mention} "
                f"sang {target.mention}."
            )
        ]
    else:
        lines = [
            f"Không có role mới nào được sao chép từ {source.mention} "
            f"sang {target.mention}."
        ]

    skipped_parts: list[str] = []
    if plan.already_present:
        skipped_parts.append(f"{len(plan.already_present)} role đích đã có")
    if plan.unmanageable:
        skipped_parts.append(f"{len(plan.unmanageable)} role không thể quản lý")
    if skipped_parts:
        lines.append("Bỏ qua: " + " · ".join(skipped_parts) + ".")
    if failed or not_attempted:
        lines.append(
            f"Lỗi: {len(failed)} role · Chưa thử: {len(not_attempted)} role."
        )
    if stop_reason is not None:
        lines.append(stop_reason)
    return "\n".join(lines)


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

    @commands.command(
        name="rolecopy",
        help="Sao chép thêm các role đủ điều kiện giữa hai thành viên.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.max_concurrency(
        1,
        per=commands.BucketType.guild,
        wait=False,
    )
    @commands.cooldown(
        1,
        ROLE_COPY_COOLDOWN_SECONDS,
        commands.BucketType.user,
    )
    async def copy_roles(
        self,
        ctx: commands.Context,
        source: discord.Member,
        target: discord.Member,
    ) -> None:
        if source.id == target.id:
            await ctx.reply(
                "Member nguồn và member đích phải khác nhau.",
                mention_author=False,
            )
            return

        bot_member = ctx.guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            await ctx.reply(
                "Bot không có quyền Manage Roles để sao chép role.",
                mention_author=False,
            )
            return

        plan = plan_role_copy(ctx.guild, ctx.author, source, target)
        if not plan.eligible:
            await ctx.reply(
                _format_role_copy_result(
                    source,
                    target,
                    plan,
                    copied=[],
                    failed=[],
                    not_attempted=[],
                    stop_reason=None,
                ),
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
            )
            return

        copied: list[discord.Role] = []
        failed: list[discord.Role] = []
        not_attempted: list[discord.Role] = []
        newly_present: list[discord.Role] = []
        newly_unmanageable: list[discord.Role] = []
        stop_reason: str | None = None
        audit_reason = (
            f"rolecopy source={source.id} target={target.id} "
            f"moderator={ctx.author.id}"
        )

        for index, role in enumerate(plan.eligible):
            manageability_denial = _role_manageability_denial(
                ctx.guild,
                ctx.author,
                role,
                action="gán",
            )
            if manageability_denial is not None:
                newly_unmanageable.append(role)
                continue
            if role in target.roles:
                newly_present.append(role)
                continue

            try:
                await target.add_roles(role, reason=audit_reason)
            except discord.NotFound:
                failed.append(role)
                not_attempted.extend(plan.eligible[index + 1 :])
                stop_reason = (
                    "Đã dừng vì member hoặc role không còn tồn tại trong server."
                )
                logger.warning(
                    "rolecopy resource missing source=%s target=%s role=%s moderator=%s",
                    source.id,
                    target.id,
                    role.id,
                    ctx.author.id,
                )
                break
            except discord.Forbidden:
                failed.append(role)
                not_attempted.extend(plan.eligible[index + 1 :])
                stop_reason = (
                    "Đã dừng vì bot không còn đủ quyền hoặc thứ bậc role đã thay đổi."
                )
                logger.warning(
                    "rolecopy forbidden source=%s target=%s role=%s moderator=%s",
                    source.id,
                    target.id,
                    role.id,
                    ctx.author.id,
                )
                break
            except discord.HTTPException:
                failed.append(role)
                not_attempted.extend(plan.eligible[index + 1 :])
                stop_reason = "Đã dừng vì Discord từ chối cập nhật role."
                logger.exception(
                    "rolecopy failed source=%s target=%s role=%s moderator=%s",
                    source.id,
                    target.id,
                    role.id,
                    ctx.author.id,
                )
                break
            else:
                copied.append(role)

        result_plan = RoleCopyPlan(
            eligible=plan.eligible,
            already_present=plan.already_present + tuple(newly_present),
            unmanageable=plan.unmanageable + tuple(newly_unmanageable),
        )
        await ctx.reply(
            _format_role_copy_result(
                source,
                target,
                result_plan,
                copied,
                failed,
                not_attempted,
                stop_reason,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False,
        )

    @copy_roles.error
    async def copy_roles_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn không có quyền Manage Roles để sao chép role.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"Cách dùng: `{ctx.clean_prefix}rolecopy @source @target`",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Mình không tìm thấy một trong hai thành viên trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Hãy mention member nguồn và member đích hợp lệ.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh rolecopy chỉ dùng được trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử lại rolecopy sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MaxConcurrencyReached):
            await ctx.reply(
                "Một lệnh rolecopy khác đang chạy trong server. Hãy thử lại sau.",
                mention_author=False,
            )
            return
        logger.error(
            "Unhandled rolecopy command error",
            exc_info=(type(error), error, error.__traceback__),
        )
        await ctx.reply(
            "Đã xảy ra lỗi khi sao chép role.",
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollCog(bot))
