import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import discord

from cogs.mod._case_helpers import clean_case_reason


logger = logging.getLogger(__name__)

UNBAN_UI_TIMEOUT_SECONDS = 180
REINVITE_MAX_AGE_SECONDS = 604800

REINVITE_SELECT_CUSTOM_ID = "unban:reinvite"
REASON_SELECT_CUSTOM_ID = "unban:reason"
CUSTOM_REASON_BUTTON_CUSTOM_ID = "unban:custom-reason"
CUSTOM_REASON_MODAL_CUSTOM_ID = "unban:custom-reason-modal"
CUSTOM_REASON_INPUT_CUSTOM_ID = "unban:custom-reason-input"
CONFIRM_BUTTON_CUSTOM_ID = "unban:confirm"
CANCEL_BUTTON_CUSTOM_ID = "unban:cancel"
BACK_BUTTON_CUSTOM_ID = "unban:back"

REASON_PRESETS = (
    (
        "appeal",
        "Chấp nhận kháng nghị",
        "Chấp nhận kháng nghị của thành viên",
        "Kháng nghị đã được moderator xem xét và chấp nhận",
    ),
    (
        "expired",
        "Đã hết thời hạn ban",
        "Thời hạn xử lý đã kết thúc",
        "Thành viên đã hoàn thành thời hạn xử lý",
    ),
    (
        "mistake",
        "Ban nhầm",
        "Gỡ ban do thao tác hoặc quyết định nhầm",
        "Sửa một quyết định ban được thực hiện nhầm",
    ),
    (
        "second-chance",
        "Cho cơ hội quay lại",
        "Cho thành viên cơ hội quay lại cộng đồng",
        "Thành viên được phép quay lại và tuân thủ nội quy",
    ),
)


@dataclass(frozen=True)
class UnbanRequest:
    target_id: int
    reinvite: bool
    reason: str


@dataclass(frozen=True)
class UnbanActionResult:
    completed: bool
    message: str
    private_message: str | None = None


UnbanSubmitter = Callable[
    [discord.Interaction, UnbanRequest],
    Awaitable[UnbanActionResult],
]


def unban_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    """Return a user-facing reason when the unban cannot currently proceed."""
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "ban_members", False):
        return "Bạn không còn quyền Ban Members để thực hiện thao tác này."

    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "ban_members", False):
        return "Bot không có quyền Ban Members trong server này."
    return None


def _safe_text(value: str, *, max_length: int = 1000) -> str:
    escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(value))
    if len(escaped) <= max_length:
        return escaped
    return f"{escaped[: max_length - 1]}…"


def _format_reinvite(
    reinvite: bool,
    destination: str | None = None,
) -> str:
    if reinvite:
        value = "Có · lời mời dùng 1 lần, hiệu lực 7 ngày và gửi qua DM"
        if destination is not None:
            value += f"\nĐiểm đến: {_safe_text(destination, max_length=100)}"
        return value
    return "Không gửi lời mời"


class ReinviteSelect(discord.ui.Select):
    def __init__(self, workflow: "UnbanWorkflowView") -> None:
        self.workflow = workflow
        yes_description = (
            "Tạo link dùng 1 lần, hiệu lực 7 ngày và gửi qua DM"
            if workflow.reinvite_available
            else "Hiện không thể tạo hoặc gửi lời mời cho thành viên"
        )
        super().__init__(
            custom_id=REINVITE_SELECT_CUSTOM_ID,
            placeholder="Chọn có gửi lời mời quay lại hay không",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Có, gửi lời mời quay lại",
                    value="yes",
                    description=yes_description,
                    emoji="📨",
                ),
                discord.SelectOption(
                    label="Không gửi lời mời",
                    value="no",
                    description="Chỉ gỡ ban, không tạo hoặc gửi link mời",
                    emoji="🚫",
                ),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            selected = self.values[0]
        except IndexError:
            await interaction.response.send_message(
                "Lựa chọn gửi lời mời không hợp lệ. Hãy mở lại bảng unban.",
                ephemeral=True,
            )
            return

        if selected not in {"yes", "no"}:
            await interaction.response.send_message(
                "Lựa chọn gửi lời mời không còn khả dụng. Hãy mở lại bảng unban.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_reinvite(interaction, selected == "yes")


class ReasonSelect(discord.ui.Select):
    def __init__(self, workflow: "UnbanWorkflowView") -> None:
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
                    description=_safe_text(
                        workflow.initial_reason,
                        max_length=100,
                    ),
                    emoji="⌨️",
                ),
            )
        super().__init__(
            custom_id=REASON_SELECT_CUSTOM_ID,
            placeholder="Chọn lý do unban",
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
                "Lý do đã chọn không hợp lệ. Hãy mở lại bảng unban.",
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
                "Lý do này không còn khả dụng. Hãy mở lại bảng unban.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_reason(interaction, reason)


class CustomReasonModal(discord.ui.Modal):
    def __init__(self, workflow: "UnbanWorkflowView") -> None:
        super().__init__(
            title="Nhập lý do unban",
            custom_id=CUSTOM_REASON_MODAL_CUSTOM_ID,
            timeout=UNBAN_UI_TIMEOUT_SECONDS,
        )
        self.workflow = workflow
        self.reason = discord.ui.TextInput(
            label="Lý do",
            style=discord.TextStyle.paragraph,
            custom_id=CUSTOM_REASON_INPUT_CUSTOM_ID,
            placeholder="Mô tả ngắn gọn lý do gỡ ban",
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


class CustomReasonButton(discord.ui.Button):
    def __init__(self, workflow: "UnbanWorkflowView") -> None:
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
    def __init__(self, workflow: "UnbanWorkflowView") -> None:
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
    def __init__(self, workflow: "UnbanWorkflowView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Có, unban thành viên",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=CONFIRM_BUTTON_CUSTOM_ID,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.confirm(interaction)


class CancelButton(discord.ui.Button):
    def __init__(self, workflow: "UnbanWorkflowView") -> None:
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


class UnbanWorkflowView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        guild_id: int,
        target: discord.abc.User,
        submitter: UnbanSubmitter,
        initial_reason: str | None = None,
        reinvite_available: bool = True,
        reinvite_destination: str | None = None,
    ) -> None:
        super().__init__(timeout=UNBAN_UI_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.guild_id = guild_id
        self.target_id = target.id
        self.target_name = str(target)
        self.target = target
        self.submitter = submitter
        self.initial_reason = (
            clean_case_reason(initial_reason)
            if initial_reason and initial_reason.strip()
            else None
        )
        self.reinvite_available = reinvite_available
        self.reinvite_destination = reinvite_destination
        self.reinvite: bool | None = None
        self.reason: str | None = None
        self.step = "reinvite"
        self.message: discord.Message | None = None
        self.completed = False
        self.submitting = False

        self.reinvite_select = ReinviteSelect(self)
        self.reason_select = ReasonSelect(self)
        self.custom_reason_button = CustomReasonButton(self)
        self.back_button = BackButton(self)
        self.confirm_button = ConfirmButton(self)
        self.cancel_button = CancelButton(self)
        self._show_reinvite_step()

    def _show_reinvite_step(self) -> None:
        self.step = "reinvite"
        self.clear_items()
        self.cancel_button.label = "Hủy"
        self.cancel_button.row = 1
        self.add_item(self.reinvite_select)
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
        if self.step == "reinvite":
            embed = discord.Embed(
                title="🔓 Unban thành viên · Bước 1/3",
                description=(
                    f"Mục tiêu: **{target}**\n"
                    "Chọn có tạo lời mời quay lại và gửi qua DM hay không."
                ),
                color=discord.Color.blurple(),
            )
            if self.reinvite_available:
                embed.set_footer(
                    text=(
                        "Lời mời sẽ dùng được 1 lần, hết hạn sau 7 ngày"
                        + (
                            f" và dẫn đến {self.reinvite_destination}."
                            if self.reinvite_destination is not None
                            else "."
                        )
                    )
                )
            else:
                embed.set_footer(
                    text="Tùy chọn gửi lời mời hiện không khả dụng."
                )
            return embed

        if self.step == "reason":
            embed = discord.Embed(
                title="🔓 Unban thành viên · Bước 2/3",
                description=(
                    f"Mục tiêu: **{target}**\n"
                    "Chọn một lý do có sẵn hoặc tự nhập lý do khác."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Mời quay lại",
                value=_format_reinvite(
                    bool(self.reinvite),
                    self.reinvite_destination,
                ),
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
            title="⚠️ Unban thành viên · Bước 3/3",
            description=(
                f"Bạn có chắc muốn unban **{target}**? "
                "Thao tác này sẽ được thực hiện ngay khi bấm xác nhận."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Mời quay lại",
            value=_format_reinvite(
                bool(self.reinvite),
                self.reinvite_destination,
            ),
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
                "Chỉ moderator đã mở bảng unban này mới có thể sử dụng.",
                ephemeral=True,
            )
            return False
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng unban này đã hoàn tất hoặc hết hạn. Hãy gọi lại lệnh.",
                ephemeral=True,
            )
            return False
        if self.submitting:
            await interaction.response.send_message(
                "Yêu cầu unban đang được xử lý, vui lòng chờ.",
                ephemeral=True,
            )
            return False

        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await interaction.response.send_message(
                "Bảng unban này chỉ dùng được trong server đã mở bảng.",
                ephemeral=True,
            )
            return False
        denial = unban_permission_denial(guild, interaction.user)
        if denial is not None:
            await interaction.response.send_message(denial, ephemeral=True)
            return False
        return True

    async def modal_check(self, interaction: discord.Interaction) -> bool:
        return await self.interaction_check(interaction)

    async def choose_reinvite(
        self,
        interaction: discord.Interaction,
        reinvite: bool,
    ) -> None:
        if reinvite and not self.reinvite_available:
            await interaction.response.send_message(
                (
                    "Tùy chọn gửi lại lời mời hiện không khả dụng. "
                    "Hãy chọn không gửi lời mời để tiếp tục."
                ),
                ephemeral=True,
            )
            return

        self.reinvite = reinvite
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
        self._show_reinvite_step()
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
                f"Đã hủy unban {_safe_text(self.target_name)} "
                f"(`{self.target_id}`)."
            ),
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def confirm(self, interaction: discord.Interaction) -> None:
        if self.submitting:
            await interaction.response.send_message(
                "Yêu cầu unban đang được xử lý, vui lòng chờ.",
                ephemeral=True,
            )
            return
        if self.reinvite is None or self.reason is None:
            await interaction.response.send_message(
                "Bảng unban chưa đủ thông tin. Hãy mở lại lệnh và thử lại.",
                ephemeral=True,
            )
            return
        if self.reinvite and not self.reinvite_available:
            await interaction.response.send_message(
                (
                    "Tùy chọn gửi lại lời mời không còn khả dụng. "
                    "Hãy quay lại và chọn không gửi lời mời."
                ),
                ephemeral=True,
            )
            return

        request = UnbanRequest(
            target_id=self.target_id,
            reinvite=self.reinvite,
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
                "Unexpected failure while submitting unban target=%s moderator=%s",
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
                logger.exception("Could not report unexpected unban UI failure")
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
                logger.exception("Could not report retryable unban UI failure")
            return

        self.completed = True
        self.disable_all()
        try:
            try:
                await interaction.edit_original_response(
                    content=result.message,
                    embed=None,
                    view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not update completed unban UI target=%s moderator=%s",
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
                        logger.exception(
                            "Could not update stored completed unban UI"
                        )
                if not fallback_succeeded:
                    try:
                        await interaction.followup.send(
                            result.message,
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except discord.HTTPException:
                        logger.exception(
                            "Could not deliver completed unban UI result"
                        )

            if result.private_message is not None:
                try:
                    await interaction.followup.send(
                        result.private_message,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.exception("Could not deliver private unban UI result")
        finally:
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("Could not disable expired unban UI", exc_info=True)
