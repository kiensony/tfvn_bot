import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import discord

from cogs.mod._case_helpers import can_moderate, clean_case_reason


logger = logging.getLogger(__name__)

BAN_UI_TIMEOUT_SECONDS = 180
MAX_DELETE_MESSAGE_HOURS = 168
DELETE_HOURS_SELECT_CUSTOM_ID = "ban:delete-hours"
CUSTOM_HOURS_BUTTON_CUSTOM_ID = "ban:custom-hours"
REASON_SELECT_CUSTOM_ID = "ban:reason"
CUSTOM_REASON_BUTTON_CUSTOM_ID = "ban:custom-reason"
CONFIRM_BUTTON_CUSTOM_ID = "ban:confirm"
CANCEL_BUTTON_CUSTOM_ID = "ban:cancel"
BACK_BUTTON_CUSTOM_ID = "ban:back"

DELETE_HOUR_PRESETS = (
    (0, "Không xóa", "Giữ lại toàn bộ tin nhắn cũ"),
    (1, "1 giờ", "Xóa tin nhắn trong 1 giờ gần nhất"),
    (6, "6 giờ", "Xóa tin nhắn trong 6 giờ gần nhất"),
    (12, "12 giờ", "Xóa tin nhắn trong 12 giờ gần nhất"),
    (24, "24 giờ", "Xóa tin nhắn trong 1 ngày gần nhất"),
    (72, "72 giờ", "Xóa tin nhắn trong 3 ngày gần nhất"),
    (168, "168 giờ", "Xóa tin nhắn trong 7 ngày gần nhất"),
)

REASON_PRESETS = (
    (
        "rules",
        "Vi phạm nội quy",
        "Vi phạm nội quy của server",
        "Vi phạm nội quy server",
    ),
    (
        "spam",
        "Spam hoặc quảng cáo",
        "Spam hoặc quảng cáo không được phép",
        "Spam, scam hoặc quảng cáo",
    ),
    (
        "harassment",
        "Quấy rối hoặc xúc phạm",
        "Quấy rối, công kích hoặc xúc phạm thành viên khác",
        "Quấy rối hoặc công kích",
    ),
    (
        "content",
        "Nội dung không phù hợp",
        "Đăng nội dung không phù hợp với cộng đồng",
        "Nội dung không phù hợp",
    ),
    (
        "raid",
        "Raid hoặc tài khoản đáng ngờ",
        "Hành vi raid hoặc tài khoản có dấu hiệu gây hại",
        "Raid hoặc tài khoản đáng ngờ",
    ),
)


@dataclass(frozen=True)
class BanRequest:
    target_id: int
    delete_message_hours: int
    reason: str

    @property
    def delete_message_seconds(self) -> int:
        return self.delete_message_hours * 60 * 60


@dataclass(frozen=True)
class BanActionResult:
    completed: bool
    message: str


BanSubmitter = Callable[
    [discord.Interaction, BanRequest],
    Awaitable[BanActionResult],
]


def parse_delete_message_hours(value: str) -> int:
    """Parse an integer Discord ban deletion window in hours."""
    stripped = value.strip()
    if not stripped or not stripped.isdecimal():
        raise ValueError("Delete-message hours must be a non-negative integer")

    hours = int(stripped)
    if hours > MAX_DELETE_MESSAGE_HOURS:
        raise ValueError("Delete-message hours exceed Discord's seven-day limit")
    return hours


def format_delete_message_window(hours: int) -> str:
    if hours == 0:
        return "Không xóa tin nhắn cũ"
    if hours == 1:
        return "1 giờ gần nhất"
    if hours in {24, 48, 72, 96, 120, 144, 168}:
        return f"{hours} giờ ({hours // 24} ngày) gần nhất"
    return f"{hours} giờ gần nhất"


def ban_target_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.abc.User,
) -> str | None:
    """Return a user-facing reason when this moderator or bot cannot ban."""
    target_guild = getattr(target, "guild", None)
    if target_guild is not None and target_guild.id != guild.id:
        return "Thành viên cần ban không thuộc server này."

    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not permissions.ban_members:
        return "Bạn không còn quyền Ban Members để thực hiện thao tác này."

    if target.id == moderator.id or target.id == guild.owner_id:
        return "Bạn không thể ban chính mình, server owner, hoặc role ngang/cao hơn."

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.ban_members:
        return "Bot không có quyền Ban Members trong server này."
    if bot_member.id == target.id:
        return "Bot không thể tự ban chính mình."

    current_target = guild.get_member(target.id)
    if current_target is None and isinstance(target, discord.Member):
        # REST-fetched members are not guaranteed to be present in the cache.
        current_target = target
    if current_target is None:
        return None
    if not can_moderate(moderator, current_target):
        return "Bạn không thể ban chính mình, server owner, hoặc role ngang/cao hơn."
    if (
        bot_member.id != guild.owner_id
        and bot_member.top_role <= current_target.top_role
    ):
        return "Role cao nhất của bot phải cao hơn role cao nhất của thành viên cần ban."
    return None


def _safe_text(value: str, *, max_length: int = 1000) -> str:
    escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(value))
    if len(escaped) <= max_length:
        return escaped
    return f"{escaped[: max_length - 1]}…"


class DeleteHoursSelect(discord.ui.Select):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        self.workflow = workflow
        super().__init__(
            custom_id=DELETE_HOURS_SELECT_CUSTOM_ID,
            placeholder="Chọn số giờ tin nhắn cần xóa",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=label,
                    value=str(hours),
                    description=description,
                    emoji="🧹" if hours else "💬",
                )
                for hours, label, description in DELETE_HOUR_PRESETS
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            hours = parse_delete_message_hours(self.values[0])
        except (IndexError, ValueError):
            await interaction.response.send_message(
                "Khoảng thời gian xóa tin nhắn không hợp lệ. Hãy mở lại bảng ban.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_delete_hours(interaction, hours)


class ReasonSelect(discord.ui.Select):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        self.workflow = workflow
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                description=description,
                emoji="🛡️",
            )
            for key, label, _, description in REASON_PRESETS
        ]
        if workflow.initial_reason is not None:
            options.insert(
                0,
                discord.SelectOption(
                    label="Dùng lý do đã nhập trong lệnh",
                    value="provided",
                    description=workflow.initial_reason[:100],
                    emoji="⌨️",
                ),
            )
        super().__init__(
            custom_id=REASON_SELECT_CUSTOM_ID,
            placeholder="Chọn lý do ban",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            selected = self.values[0]
        except IndexError:
            await interaction.response.send_message(
                "Lý do đã chọn không hợp lệ. Hãy mở lại bảng ban.",
                ephemeral=True,
            )
            return

        if selected == "provided" and self.workflow.initial_reason is not None:
            reason = self.workflow.initial_reason
        else:
            reason = next(
                (
                    reason
                    for key, _, reason, _ in REASON_PRESETS
                    if key == selected
                ),
                None,
            )
        if reason is None:
            await interaction.response.send_message(
                "Lý do này không còn khả dụng. Hãy mở lại bảng ban.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_reason(interaction, reason)


class CustomHoursModal(discord.ui.Modal):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        super().__init__(
            title="Số giờ tin nhắn cần xóa",
            timeout=BAN_UI_TIMEOUT_SECONDS,
        )
        self.workflow = workflow
        self.hours = discord.ui.TextInput(
            label="Số giờ (0–168)",
            placeholder="Ví dụ: 36",
            default=(
                str(workflow.delete_message_hours)
                if workflow.delete_message_hours is not None
                else None
            ),
            min_length=1,
            max_length=3,
        )
        self.add_item(self.hours)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.workflow.modal_check(interaction):
            return
        try:
            hours = parse_delete_message_hours(self.hours.value)
        except ValueError:
            await interaction.response.send_message(
                "Hãy nhập số nguyên từ 0 đến 168 giờ.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_delete_hours(interaction, hours)


class CustomReasonModal(discord.ui.Modal):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        super().__init__(
            title="Nhập lý do ban",
            timeout=BAN_UI_TIMEOUT_SECONDS,
        )
        self.workflow = workflow
        self.reason = discord.ui.TextInput(
            label="Lý do",
            style=discord.TextStyle.paragraph,
            placeholder="Mô tả ngắn gọn lý do ban",
            default=workflow.reason or workflow.initial_reason,
            min_length=1,
            max_length=1000,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.workflow.modal_check(interaction):
            return
        reason = self.reason.value.strip()
        if not reason:
            await interaction.response.send_message(
                "Lý do không được chỉ chứa khoảng trắng.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_reason(interaction, reason)


class CustomHoursButton(discord.ui.Button):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Nhập số giờ khác",
            emoji="⌨️",
            style=discord.ButtonStyle.secondary,
            custom_id=CUSTOM_HOURS_BUTTON_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CustomHoursModal(self.workflow))


class CustomReasonButton(discord.ui.Button):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Nhập lý do khác",
            emoji="✏️",
            style=discord.ButtonStyle.secondary,
            custom_id=CUSTOM_REASON_BUTTON_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CustomReasonModal(self.workflow))


class BackButton(discord.ui.Button):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Quay lại",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id=BACK_BUTTON_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.go_back(interaction)


class ConfirmButton(discord.ui.Button):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Có, ban thành viên",
            emoji="✅",
            style=discord.ButtonStyle.danger,
            custom_id=CONFIRM_BUTTON_CUSTOM_ID,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.confirm(interaction)


class CancelButton(discord.ui.Button):
    def __init__(self, workflow: "BanWorkflowView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Không, hủy",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            custom_id=CANCEL_BUTTON_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.cancel(interaction)


class BanWorkflowView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        guild_id: int,
        target: discord.abc.User,
        submitter: BanSubmitter,
        initial_reason: str | None = None,
    ) -> None:
        super().__init__(timeout=BAN_UI_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.guild_id = guild_id
        self.target_id = target.id
        self.target_name = str(target)
        self.target = target
        self.submitter = submitter
        self.initial_reason = (
            clean_case_reason(initial_reason) if initial_reason else None
        )
        self.delete_message_hours: int | None = None
        self.reason: str | None = None
        self.step = "delete"
        self.message: discord.Message | None = None
        self.completed = False
        self.submitting = False

        self.delete_hours_select = DeleteHoursSelect(self)
        self.custom_hours_button = CustomHoursButton(self)
        self.reason_select = ReasonSelect(self)
        self.custom_reason_button = CustomReasonButton(self)
        self.back_button = BackButton(self)
        self.confirm_button = ConfirmButton(self)
        self.cancel_button = CancelButton(self)
        self._show_delete_step()

    def _show_delete_step(self) -> None:
        self.step = "delete"
        self.clear_items()
        self.cancel_button.label = "Hủy"
        self.cancel_button.row = 1
        self.add_item(self.delete_hours_select)
        self.add_item(self.custom_hours_button)
        self.add_item(self.cancel_button)

    def _show_reason_step(self) -> None:
        self.step = "reason"
        self.clear_items()
        self.cancel_button.label = "Hủy"
        self.cancel_button.row = 1
        self.add_item(self.reason_select)
        self.add_item(self.custom_reason_button)
        self.add_item(self.back_button)
        self.add_item(self.cancel_button)

    def _show_confirm_step(self) -> None:
        self.step = "confirm"
        self.clear_items()
        self.cancel_button.label = "Không, hủy"
        self.cancel_button.row = 0
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    def build_embed(self) -> discord.Embed:
        target = f"{_safe_text(self.target_name, max_length=100)} (`{self.target_id}`)"
        if self.step == "delete":
            embed = discord.Embed(
                title="🔨 Ban thành viên · Bước 1/3",
                description=(
                    f"Mục tiêu: **{target}**\n"
                    "Chọn khoảng thời gian tin nhắn gần đây của thành viên cần xóa."
                ),
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Discord cho phép xóa tối đa 168 giờ (7 ngày).")
            return embed

        if self.step == "reason":
            embed = discord.Embed(
                title="🔨 Ban thành viên · Bước 2/3",
                description=(
                    f"Mục tiêu: **{target}**\n"
                    "Chọn một lý do có sẵn hoặc tự nhập lý do khác."
                ),
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Xóa tin nhắn",
                value=format_delete_message_window(self.delete_message_hours or 0),
                inline=False,
            )
            if self.initial_reason is not None:
                embed.add_field(
                    name="Lý do đã nhập trong lệnh",
                    value=_safe_text(self.initial_reason),
                    inline=False,
                )
            return embed

        embed = discord.Embed(
            title="⚠️ Ban thành viên · Bước 3/3",
            description=(
                f"Bạn có chắc muốn ban **{target}**? "
                "Thao tác này sẽ được thực hiện ngay khi bấm xác nhận."
            ),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Xóa tin nhắn",
            value=format_delete_message_window(self.delete_message_hours or 0),
            inline=False,
        )
        embed.add_field(
            name="Lý do",
            value=_safe_text(self.reason or "Không có lý do cụ thể"),
            inline=False,
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Chỉ moderator đã mở bảng ban này mới có thể sử dụng.",
                ephemeral=True,
            )
            return False
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng ban này đã hoàn tất hoặc hết hạn. Hãy gọi lại lệnh.",
                ephemeral=True,
            )
            return False
        if self.submitting:
            await interaction.response.send_message(
                "Yêu cầu ban đang được xử lý, vui lòng chờ.",
                ephemeral=True,
            )
            return False

        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await interaction.response.send_message(
                "Bảng ban này chỉ dùng được trong server đã mở bảng.",
                ephemeral=True,
            )
            return False
        target = guild.get_member(self.target_id) or self.target
        denial = ban_target_denial(guild, interaction.user, target)
        if denial is not None:
            await interaction.response.send_message(denial, ephemeral=True)
            return False
        return True

    async def modal_check(self, interaction: discord.Interaction) -> bool:
        return await self.interaction_check(interaction)

    async def choose_delete_hours(
        self,
        interaction: discord.Interaction,
        hours: int,
    ) -> None:
        self.delete_message_hours = hours
        self._show_reason_step()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def choose_reason(
        self,
        interaction: discord.Interaction,
        reason: str,
    ) -> None:
        self.reason = clean_case_reason(reason)
        self._show_confirm_step()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def go_back(self, interaction: discord.Interaction) -> None:
        self._show_delete_step()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        self.completed = True
        self.disable_all()
        self.stop()
        await interaction.response.edit_message(
            content=(
                f"Đã hủy ban {_safe_text(self.target_name)} (`{self.target_id}`)."
            ),
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def confirm(self, interaction: discord.Interaction) -> None:
        if self.delete_message_hours is None or self.reason is None:
            await interaction.response.send_message(
                "Bảng ban chưa đủ thông tin. Hãy mở lại lệnh và thử lại.",
                ephemeral=True,
            )
            return

        request = BanRequest(
            target_id=self.target_id,
            delete_message_hours=self.delete_message_hours,
            reason=self.reason,
        )
        self.submitting = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.submitting = False
            raise

        try:
            result = await self.submitter(interaction, request)
        except Exception:
            self.submitting = False
            logger.exception(
                "Unexpected failure while submitting ban target=%s moderator=%s",
                self.target_id,
                self.author_id,
            )
            try:
                await interaction.followup.send(
                    (
                        "Đã xảy ra lỗi ngoài dự kiến. Hãy kiểm tra Audit Log "
                        "trước khi thử lại."
                    ),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception("Could not report unexpected ban UI failure")
            return

        if not result.completed:
            self.submitting = False
            try:
                await interaction.followup.send(
                    result.message,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception("Could not report retryable ban UI failure")
            return

        self.completed = True
        self.disable_all()
        try:
            await interaction.edit_original_response(
                content=result.message,
                embed=None,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception(
                "Could not update completed ban UI target=%s moderator=%s",
                self.target_id,
                self.author_id,
            )
            fallback_succeeded = False
            if self.message is not None:
                try:
                    await self.message.edit(
                        content=result.message,
                        embed=None,
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    fallback_succeeded = True
                except discord.HTTPException:
                    logger.exception("Could not update stored completed ban UI")
            if not fallback_succeeded:
                try:
                    await interaction.followup.send(
                        result.message,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.exception("Could not deliver completed ban UI result")
        finally:
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("Could not disable expired ban UI", exc_info=True)
