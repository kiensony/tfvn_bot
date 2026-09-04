"""Administrator Discord UI for recurring bedtime reminders."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import discord

from cogs.bedtime_remind._bedtime_helpers import format_clock_time, parse_clock_time


if TYPE_CHECKING:
    from cogs.bedtime_remind.bedtime_remind import BedtimeReminderCog

logger = logging.getLogger(__name__)

BEDTIME_UI_TIMEOUT_SECONDS = 180
REMOVE_OPTIONS_PER_PAGE = 25
NO_MENTIONS = discord.AllowedMentions.none()

REMINDER_CHANNEL_TYPES = (
    discord.ChannelType.text,
    discord.ChannelType.news,
)

PANEL_ADD_CUSTOM_ID = "bedtime:panel:add"
PANEL_REMOVE_CUSTOM_ID = "bedtime:panel:remove"
PANEL_REFRESH_CUSTOM_ID = "bedtime:panel:refresh"
PANEL_PREV_CUSTOM_ID = "bedtime:panel:prev"
PANEL_NEXT_CUSTOM_ID = "bedtime:panel:next"
ADD_MEMBER_CUSTOM_ID = "bedtime:add:member"
ADD_CHANNEL_CUSTOM_ID = "bedtime:add:channel"
ADD_TIMES_CUSTOM_ID = "bedtime:add:times"
ADD_SAVE_CUSTOM_ID = "bedtime:add:save"
ADD_BACK_CUSTOM_ID = "bedtime:add:back"
TIMES_MODAL_CUSTOM_ID = "bedtime:times-modal"
REMOVE_SCHEDULE_CUSTOM_ID = "bedtime:remove:schedule"
REMOVE_CONFIRM_CUSTOM_ID = "bedtime:remove:confirm"
REMOVE_BACK_CUSTOM_ID = "bedtime:remove:back"
REMOVE_PREV_CUSTOM_ID = "bedtime:remove:prev"
REMOVE_NEXT_CUSTOM_ID = "bedtime:remove:next"


async def send_private(interaction: discord.Interaction, content: str) -> None:
    """Reply privately, whether or not the interaction is already acknowledged."""

    kwargs: dict[str, Any] = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": NO_MENTIONS,
    }
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.HTTPException:
        logger.debug(
            "Could not send private bedtime UI message guild=%s user=%s",
            getattr(interaction.guild, "id", None),
            getattr(interaction.user, "id", None),
            exc_info=True,
        )


def _is_administrator(user: object) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions is not None and permissions.administrator)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1] + "…"


class BedtimeOwnedView(discord.ui.View):
    """Owner-only bedtime workflow with live Administrator re-checks."""

    def __init__(
        self,
        cog: BedtimeReminderCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
    ) -> None:
        super().__init__(timeout=BEDTIME_UI_TIMEOUT_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.prefix = prefix
        self.message: discord.Message | None = None
        self.completed = False
        self.submitting = False
        self._action_lock = asyncio.Lock()

    def guild(self) -> discord.Guild | None:
        return self.cog.bot.get_guild(self.guild_id)

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await send_private(
                interaction,
                "Bảng giờ ngủ này chỉ dùng được trong server đã mở nó.",
            )
            return False
        if interaction.user.id != self.author_id:
            await send_private(
                interaction,
                "Chỉ Administrator đã mở bảng này mới có thể sử dụng.",
            )
            return False
        if not _is_administrator(interaction.user):
            await send_private(
                interaction,
                "Bạn cần quyền Administrator hiện tại để dùng bảng này.",
            )
            return False
        if self.completed or self.is_finished():
            await send_private(
                interaction,
                "Bảng giờ ngủ đã hoàn tất hoặc hết hạn. Hãy gọi lại lệnh `bedtime`.",
            )
            return False
        if self.submitting:
            await send_private(
                interaction,
                "Bảng giờ ngủ đang xử lý, vui lòng chờ.",
            )
            return False
        return True

    async def on_timeout(self) -> None:
        self.disable_all()
        self.stop()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug(
                "Could not disable expired bedtime UI guild=%s user=%s",
                self.guild_id,
                self.author_id,
                exc_info=True,
            )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        logger.exception(
            "Bedtime UI interaction failed guild=%s user=%s item=%s",
            self.guild_id,
            self.author_id,
            getattr(item, "custom_id", type(item).__name__),
        )
        await send_private(
            interaction,
            "Không thể xử lý thao tác giờ ngủ lúc này. Hãy thử lại.",
        )


class PanelAddButton(discord.ui.Button["BedtimePanelView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Thêm lịch",
            emoji="➕",
            style=discord.ButtonStyle.success,
            custom_id=PANEL_ADD_CUSTOM_ID,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimePanelView):
            await view.open_add(interaction)


class PanelRemoveButton(discord.ui.Button["BedtimePanelView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Xóa lịch",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            custom_id=PANEL_REMOVE_CUSTOM_ID,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimePanelView):
            await view.open_remove(interaction)


class PanelRefreshButton(discord.ui.Button["BedtimePanelView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Làm mới",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id=PANEL_REFRESH_CUSTOM_ID,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimePanelView):
            await view.refresh(interaction)


class PanelPrevButton(discord.ui.Button["BedtimePanelView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Trước",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            custom_id=PANEL_PREV_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimePanelView):
            await view.turn_page(interaction, -1)


class PanelNextButton(discord.ui.Button["BedtimePanelView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Sau",
            emoji="➡️",
            style=discord.ButtonStyle.secondary,
            custom_id=PANEL_NEXT_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimePanelView):
            await view.turn_page(interaction, 1)


class BedtimeMemberSelect(discord.ui.UserSelect["BedtimeAddView"]):
    def __init__(self, editor: BedtimeAddView) -> None:
        self.editor = editor
        super().__init__(
            custom_id=ADD_MEMBER_CUSTOM_ID,
            placeholder="Chọn thành viên",
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.editor.interaction_check(interaction):
            return
        selected = self.values[0] if self.values else None
        if selected is None:
            await send_private(interaction, "Hãy chọn một thành viên.")
            return
        await self.editor.choose_member(interaction, selected)


class BedtimeChannelSelect(discord.ui.ChannelSelect["BedtimeAddView"]):
    def __init__(self, editor: BedtimeAddView) -> None:
        self.editor = editor
        super().__init__(
            custom_id=ADD_CHANNEL_CUSTOM_ID,
            placeholder="Chọn kênh nhắc",
            channel_types=list(REMINDER_CHANNEL_TYPES),
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.editor.interaction_check(interaction):
            return
        selected = self.values[0] if self.values else None
        if selected is None:
            await send_private(interaction, "Hãy chọn một text channel.")
            return
        await self.editor.choose_channel(interaction, selected)


class AddTimesButton(discord.ui.Button["BedtimeAddView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Nhập giờ ngủ",
            emoji="⏰",
            style=discord.ButtonStyle.primary,
            custom_id=ADD_TIMES_CUSTOM_ID,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimeAddView):
            await view.open_times_modal(interaction)


class AddSaveButton(discord.ui.Button["BedtimeAddView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Lưu",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=ADD_SAVE_CUSTOM_ID,
            disabled=True,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimeAddView):
            await view.confirm(interaction)


class AddBackButton(discord.ui.Button["BedtimeAddView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Quay lại",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id=ADD_BACK_CUSTOM_ID,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimeAddView):
            await view.go_back(interaction)


class RemoveScheduleSelect(discord.ui.Select["BedtimeRemoveView"]):
    def __init__(self, editor: BedtimeRemoveView) -> None:
        self.editor = editor
        super().__init__(
            custom_id=REMOVE_SCHEDULE_CUSTOM_ID,
            placeholder=editor.select_placeholder(),
            min_values=1,
            max_values=1,
            options=editor.build_select_options(),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.editor.interaction_check(interaction):
            return
        selected = self.values[0] if self.values else None
        await self.editor.choose_user_id(interaction, selected)


class RemoveConfirmButton(discord.ui.Button["BedtimeRemoveView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Xóa",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            custom_id=REMOVE_CONFIRM_CUSTOM_ID,
            disabled=True,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimeRemoveView):
            await view.confirm(interaction)


class RemoveBackButton(discord.ui.Button["BedtimeRemoveView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Quay lại",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id=REMOVE_BACK_CUSTOM_ID,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimeRemoveView):
            await view.go_back(interaction)


class RemovePrevButton(discord.ui.Button["BedtimeRemoveView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Trước",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            custom_id=REMOVE_PREV_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimeRemoveView):
            await view.turn_page(interaction, -1)


class RemoveNextButton(discord.ui.Button["BedtimeRemoveView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Sau",
            emoji="➡️",
            style=discord.ButtonStyle.secondary,
            custom_id=REMOVE_NEXT_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BedtimeRemoveView):
            await view.turn_page(interaction, 1)


class BedtimeTimesModal(discord.ui.Modal):
    """Collect bedtime and wake clock times as H:MM / HH:MM."""

    def __init__(self, editor: BedtimeAddView) -> None:
        super().__init__(
            title="Nhập giờ ngủ (UTC+7)",
            custom_id=TIMES_MODAL_CUSTOM_ID,
            timeout=BEDTIME_UI_TIMEOUT_SECONDS,
        )
        self.editor = editor
        bedtime_default = (
            format_clock_time(editor.bedtime_minutes)
            if editor.bedtime_minutes is not None
            else None
        )
        wake_default = (
            format_clock_time(editor.wake_minutes)
            if editor.wake_minutes is not None
            else None
        )
        self.bedtime_input = discord.ui.TextInput(
            label="Giờ ngủ",
            placeholder="23:00",
            default=bedtime_default,
            min_length=4,
            max_length=5,
            required=True,
        )
        self.wake_input = discord.ui.TextInput(
            label="Giờ dậy",
            placeholder="07:00",
            default=wake_default,
            min_length=4,
            max_length=5,
            required=True,
        )
        self.add_item(self.bedtime_input)
        self.add_item(self.wake_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.editor.interaction_check(interaction):
            return
        try:
            bedtime_minutes = parse_clock_time(str(self.bedtime_input.value))
            wake_minutes = parse_clock_time(str(self.wake_input.value))
        except (TypeError, ValueError):
            await send_private(
                interaction,
                "Giờ không hợp lệ. Hãy dùng `H:MM` hoặc `HH:MM` theo đồng hồ 24 giờ.",
            )
            return
        if bedtime_minutes == wake_minutes:
            await send_private(
                interaction,
                "Giờ ngủ và giờ dậy phải khác nhau.",
            )
            return
        await self.editor.apply_times(
            interaction,
            bedtime_minutes,
            wake_minutes,
        )


class BedtimePanelView(BedtimeOwnedView):
    """Paginated schedule list with add/remove/refresh controls."""

    def __init__(
        self,
        cog: BedtimeReminderCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
        page: int = 0,
    ) -> None:
        super().__init__(
            cog,
            guild_id=guild_id,
            author_id=author_id,
            prefix=prefix,
        )
        self.page = max(0, page)
        self.add_button = PanelAddButton()
        self.remove_button = PanelRemoveButton()
        self.refresh_button = PanelRefreshButton()
        self.prev_button = PanelPrevButton()
        self.next_button = PanelNextButton()
        self.add_item(self.add_button)
        self.add_item(self.remove_button)
        self.add_item(self.refresh_button)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.sync_controls()

    def reminders(self) -> list[dict[str, Any]]:
        return self.cog.iter_guild_reminders(self.guild_id)

    def page_count(self, total: int) -> int:
        per_page = self.cog.reminders_per_page
        if total <= 0:
            return 1
        return (total + per_page - 1) // per_page

    def sync_controls(self) -> None:
        reminders = self.reminders()
        pages = self.page_count(len(reminders))
        self.page = min(self.page, pages - 1)
        self.remove_button.disabled = not reminders
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= pages - 1

    def build_embed(self) -> discord.Embed:
        guild = self.guild()
        reminders = self.reminders()
        pages = self.page_count(len(reminders))
        self.page = min(self.page, pages - 1)
        intro = (
            "Dùng nút bên dưới để thêm, xóa hoặc xem lịch. "
            f"Lệnh nhanh: `{self.prefix}bedtime add|remove|list`."
        )
        if not reminders:
            listing = "Server chưa có lịch nhắc giờ đi ngủ nào."
        elif guild is None:
            listing = "Không tìm thấy server để hiển thị lịch."
        else:
            per_page = self.cog.reminders_per_page
            start = self.page * per_page
            page_rows = reminders[start : start + per_page]
            listing = "\n".join(
                self.cog.format_reminder_line(guild, document)
                for document in page_rows
            )
        embed = discord.Embed(
            title=f"Lịch nhắc giờ ngủ · {self.page + 1}/{pages}",
            description=f"{intro}\n\n{listing}",
            color=discord.Color.dark_purple(),
        )
        embed.set_footer(text="Múi giờ cố định: UTC+7 · Bảng hết hạn sau 3 phút")
        return embed

    async def _replace_with(
        self,
        interaction: discord.Interaction,
        replacement: BedtimeOwnedView,
        *,
        deferred: bool = False,
    ) -> bool:
        replacement.message = self.message
        kwargs: dict[str, Any] = {
            "embed": replacement.build_embed(),
            "view": replacement,
            "allowed_mentions": NO_MENTIONS,
        }
        try:
            if deferred:
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)
        except discord.HTTPException:
            logger.exception(
                "Could not update bedtime UI guild=%s user=%s",
                self.guild_id,
                self.author_id,
            )
            await send_private(
                interaction,
                "Không thể cập nhật bảng giờ ngủ lúc này. Hãy thử lại.",
            )
            return False
        self.stop()
        return True

    async def show(
        self,
        interaction: discord.Interaction,
        *,
        deferred: bool = False,
    ) -> bool:
        self.sync_controls()
        kwargs: dict[str, Any] = {
            "embed": self.build_embed(),
            "view": self,
            "allowed_mentions": NO_MENTIONS,
        }
        try:
            if deferred:
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)
        except discord.HTTPException:
            logger.exception(
                "Could not refresh bedtime panel guild=%s user=%s",
                self.guild_id,
                self.author_id,
            )
            await send_private(
                interaction,
                "Không thể cập nhật bảng giờ ngủ lúc này. Hãy thử lại.",
            )
            return False
        return True

    async def open_add(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        editor = BedtimeAddView.from_panel(self)
        await self._replace_with(interaction, editor)

    async def open_remove(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        if not self.reminders():
            await send_private(
                interaction,
                "Server chưa có lịch nhắc giờ đi ngủ nào.",
            )
            return
        editor = BedtimeRemoveView.from_panel(self)
        await self._replace_with(interaction, editor)

    async def refresh(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        self.sync_controls()
        await self.show(interaction)

    async def turn_page(
        self,
        interaction: discord.Interaction,
        delta: int,
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        pages = self.page_count(len(self.reminders()))
        self.page = min(max(self.page + delta, 0), pages - 1)
        self.sync_controls()
        await self.show(interaction)


class BedtimeAddView(BedtimeOwnedView):
    """Collect member, channel, and clock times, then save the schedule."""

    def __init__(
        self,
        cog: BedtimeReminderCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
        return_page: int = 0,
    ) -> None:
        super().__init__(
            cog,
            guild_id=guild_id,
            author_id=author_id,
            prefix=prefix,
        )
        self.return_page = return_page
        self.member: discord.Member | None = None
        self.channel: discord.TextChannel | None = None
        self.bedtime_minutes: int | None = None
        self.wake_minutes: int | None = None
        self.member_select = BedtimeMemberSelect(self)
        self.channel_select = BedtimeChannelSelect(self)
        self.times_button = AddTimesButton()
        self.save_button = AddSaveButton()
        self.back_button = AddBackButton()
        self.add_item(self.member_select)
        self.add_item(self.channel_select)
        self.add_item(self.times_button)
        self.add_item(self.save_button)
        self.add_item(self.back_button)

    @classmethod
    def from_panel(cls, panel: BedtimePanelView) -> BedtimeAddView:
        return cls(
            panel.cog,
            guild_id=panel.guild_id,
            author_id=panel.author_id,
            prefix=panel.prefix,
            return_page=panel.page,
        )

    def _ready_to_save(self) -> bool:
        return (
            self.member is not None
            and self.channel is not None
            and self.bedtime_minutes is not None
            and self.wake_minutes is not None
            and self.bedtime_minutes != self.wake_minutes
        )

    def _sync_save_button(self) -> None:
        self.save_button.disabled = not self._ready_to_save()

    def build_embed(self) -> discord.Embed:
        member_value = (
            f"{discord.utils.escape_markdown(self.member.display_name)} "
            f"(`{self.member.id}`)"
            if self.member is not None
            else "Chưa chọn"
        )
        channel_value = (
            f"#{discord.utils.escape_markdown(self.channel.name)} "
            f"(`{self.channel.id}`)"
            if self.channel is not None
            else "Chưa chọn"
        )
        if self.bedtime_minutes is not None and self.wake_minutes is not None:
            times_value = (
                f"{format_clock_time(self.bedtime_minutes)}–"
                f"{format_clock_time(self.wake_minutes)} (UTC+7)"
            )
        else:
            times_value = "Chưa chọn"
        embed = discord.Embed(
            title="Thêm hoặc cập nhật lịch ngủ",
            description=(
                "Chọn **thành viên**, **kênh nhắc**, rồi bấm **Nhập giờ ngủ**. "
                "Bấm **Lưu** khi đã đủ thông tin."
            ),
            color=discord.Color.dark_purple(),
        )
        embed.add_field(name="Thành viên", value=member_value, inline=False)
        embed.add_field(name="Kênh nhắc", value=channel_value, inline=False)
        embed.add_field(name="Giờ ngủ", value=times_value, inline=False)
        embed.set_footer(text="Múi giờ cố định: UTC+7 · Bảng hết hạn sau 3 phút")
        return embed

    def _resolve_member(self, selected: object) -> discord.Member | None:
        guild = self.guild()
        if guild is None:
            return None
        if (
            isinstance(selected, discord.Member)
            and selected.guild.id == self.guild_id
        ):
            return selected
        user_id = getattr(selected, "id", None)
        if not isinstance(user_id, int):
            return None
        return guild.get_member(user_id)

    def _resolve_channel(self, selected: object) -> discord.TextChannel | None:
        guild = self.guild()
        if guild is None:
            return None
        if (
            isinstance(selected, discord.TextChannel)
            and selected.guild.id == self.guild_id
        ):
            return selected
        channel_id = getattr(selected, "id", None)
        if not isinstance(channel_id, int):
            return None
        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _prefill_from_existing(self, member: discord.Member) -> None:
        existing = self.cog.reminders_by_member.get((self.guild_id, member.id))
        if existing is None:
            return
        self.bedtime_minutes = int(existing["bedtime_minutes"])
        self.wake_minutes = int(existing["wake_minutes"])
        if self.channel is not None:
            return
        guild = self.guild()
        if guild is None:
            return
        channel = guild.get_channel(int(existing["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            self.channel = channel

    async def _edit_self(self, interaction: discord.Interaction) -> None:
        self._sync_save_button()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    async def choose_member(
        self,
        interaction: discord.Interaction,
        selected: object,
    ) -> None:
        member = self._resolve_member(selected)
        if member is None:
            await send_private(
                interaction,
                "Hãy chọn một thành viên đang ở trong server.",
            )
            return
        if member.bot:
            await send_private(interaction, "Không thể đặt giờ ngủ cho bot.")
            return
        self.member = member
        self._prefill_from_existing(member)
        await self._edit_self(interaction)

    async def choose_channel(
        self,
        interaction: discord.Interaction,
        selected: object,
    ) -> None:
        channel = self._resolve_channel(selected)
        if channel is None:
            await send_private(
                interaction,
                "Hãy chọn một text channel thuộc server này.",
            )
            return
        guild = self.guild()
        if guild is None or not self.cog.can_send_to_channel(guild, channel):
            await send_private(
                interaction,
                "Bot cần quyền View Channel và Send Messages trong kênh nhắc.",
            )
            return
        self.channel = channel
        await self._edit_self(interaction)

    async def open_times_modal(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        await interaction.response.send_modal(BedtimeTimesModal(self))

    async def apply_times(
        self,
        interaction: discord.Interaction,
        bedtime_minutes: int,
        wake_minutes: int,
    ) -> None:
        self.bedtime_minutes = bedtime_minutes
        self.wake_minutes = wake_minutes
        await self._edit_self(interaction)

    async def confirm(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        if not self._ready_to_save():
            await send_private(
                interaction,
                "Hãy chọn thành viên, kênh nhắc và giờ ngủ trước khi lưu.",
            )
            return
        if self._action_lock.locked():
            await send_private(interaction, "Bảng giờ ngủ đang xử lý, vui lòng chờ.")
            return

        async with self._action_lock:
            guild = self.guild()
            member = (
                guild.get_member(self.member.id)
                if guild is not None and self.member is not None
                else None
            )
            resolved_channel = (
                guild.get_channel(self.channel.id)
                if guild is not None and self.channel is not None
                else None
            )
            channel = (
                resolved_channel
                if isinstance(resolved_channel, discord.TextChannel)
                else self.channel
            )
            if guild is None or member is None or channel is None:
                await send_private(
                    interaction,
                    "Thành viên hoặc kênh không còn hợp lệ. Hãy chọn lại.",
                )
                return

            self.submitting = True
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                self.submitting = False
                raise

            result = await self.cog.save_schedule(
                guild=guild,
                member=member,
                channel=channel,
                bedtime_minutes=int(self.bedtime_minutes or 0),
                wake_minutes=int(self.wake_minutes or 0),
                actor_id=self.author_id,
            )
            if not result.ok:
                self.submitting = False
                await send_private(interaction, result.message)
                return

            panel = BedtimePanelView(
                self.cog,
                guild_id=self.guild_id,
                author_id=self.author_id,
                prefix=self.prefix,
                page=self.return_page,
            )
            panel.message = self.message
            shown = await panel.show(interaction, deferred=True)
            self.submitting = False
            if shown:
                self.stop()
                try:
                    await interaction.followup.send(
                        result.message,
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    logger.debug(
                        "Could not send bedtime save confirmation guild=%s user=%s",
                        self.guild_id,
                        self.author_id,
                        exc_info=True,
                    )

    async def go_back(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        panel = BedtimePanelView(
            self.cog,
            guild_id=self.guild_id,
            author_id=self.author_id,
            prefix=self.prefix,
            page=self.return_page,
        )
        panel.message = self.message
        if await panel.show(interaction):
            self.stop()


class BedtimeRemoveView(BedtimeOwnedView):
    """Pick an existing guild schedule and delete it."""

    def __init__(
        self,
        cog: BedtimeReminderCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
        return_page: int = 0,
        page: int = 0,
        selected_user_id: int | None = None,
        notice: str | None = None,
    ) -> None:
        super().__init__(
            cog,
            guild_id=guild_id,
            author_id=author_id,
            prefix=prefix,
        )
        self.return_page = return_page
        self.page = max(0, page)
        self.selected_user_id = selected_user_id
        self.notice = notice
        self.schedule_select: RemoveScheduleSelect | None = None
        self.prev_button = RemovePrevButton()
        self.next_button = RemoveNextButton()
        self.confirm_button = RemoveConfirmButton()
        self.back_button = RemoveBackButton()
        self._rebuild_items()

    @classmethod
    def from_panel(cls, panel: BedtimePanelView) -> BedtimeRemoveView:
        return cls(
            panel.cog,
            guild_id=panel.guild_id,
            author_id=panel.author_id,
            prefix=panel.prefix,
            return_page=panel.page,
        )

    def reminders(self) -> list[dict[str, Any]]:
        return self.cog.iter_guild_reminders(self.guild_id)

    def page_count(self, total: int) -> int:
        if total <= 0:
            return 1
        return (total + REMOVE_OPTIONS_PER_PAGE - 1) // REMOVE_OPTIONS_PER_PAGE

    def page_documents(self) -> list[dict[str, Any]]:
        reminders = self.reminders()
        pages = self.page_count(len(reminders))
        self.page = min(self.page, pages - 1)
        start = self.page * REMOVE_OPTIONS_PER_PAGE
        return reminders[start : start + REMOVE_OPTIONS_PER_PAGE]

    def select_placeholder(self) -> str:
        total = len(self.reminders())
        pages = self.page_count(total)
        if total == 0:
            return "Không còn lịch để xóa"
        return f"Chọn lịch để xóa ({self.page + 1}/{pages})"

    def build_select_options(self) -> list[discord.SelectOption]:
        guild = self.guild()
        options: list[discord.SelectOption] = []
        for document in self.page_documents():
            user_id = int(document["user_id"])
            member = guild.get_member(user_id) if guild is not None else None
            name = (
                member.display_name
                if member is not None
                else "Đã rời server"
            )
            options.append(
                discord.SelectOption(
                    label=_truncate(f"{name} ({user_id})", 100),
                    value=str(user_id),
                    description=_truncate(
                        f"{format_clock_time(int(document['bedtime_minutes']))}–"
                        f"{format_clock_time(int(document['wake_minutes']))}",
                        100,
                    ),
                    default=user_id == self.selected_user_id,
                )
            )
        if not options:
            options.append(
                discord.SelectOption(
                    label="Không còn lịch",
                    value="none",
                    description="Quay lại bảng chính",
                )
            )
        return options

    def _rebuild_items(self) -> None:
        self.clear_items()
        reminders = self.reminders()
        pages = self.page_count(len(reminders))
        self.page = min(self.page, pages - 1)
        if self.selected_user_id is not None and not any(
            int(document["user_id"]) == self.selected_user_id
            for document in reminders
        ):
            self.selected_user_id = None
        self.schedule_select = None
        if reminders:
            self.schedule_select = RemoveScheduleSelect(self)
            self.add_item(self.schedule_select)
            self.prev_button.disabled = self.page <= 0
            self.next_button.disabled = self.page >= pages - 1
            self.add_item(self.prev_button)
            self.add_item(self.next_button)
        self.confirm_button.disabled = self.selected_user_id is None
        self.add_item(self.confirm_button)
        self.add_item(self.back_button)

    def build_embed(self) -> discord.Embed:
        intro = "Chọn một lịch trong danh sách, rồi bấm **Xóa**."
        if self.notice:
            intro = f"{self.notice}\n\n{intro}"
        guild = self.guild()
        reminders = self.reminders()
        if not reminders:
            listing = "Server chưa có lịch nhắc giờ đi ngủ nào."
        elif guild is None:
            listing = "Không tìm thấy server để hiển thị lịch."
        else:
            listing = "\n".join(
                self.cog.format_reminder_line(guild, document)
                for document in self.page_documents()
            )
        embed = discord.Embed(
            title="Xóa lịch ngủ",
            description=f"{intro}\n\n{listing}",
            color=discord.Color.dark_purple(),
        )
        embed.set_footer(text="Múi giờ cố định: UTC+7 · Bảng hết hạn sau 3 phút")
        return embed

    async def _edit_self(self, interaction: discord.Interaction) -> None:
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    async def choose_user_id(
        self,
        interaction: discord.Interaction,
        raw_value: str | None,
    ) -> None:
        if raw_value is None or raw_value == "none":
            await send_private(interaction, "Hãy chọn một lịch hợp lệ.")
            return
        try:
            user_id = int(raw_value)
        except (TypeError, ValueError):
            await send_private(interaction, "Hãy chọn một lịch hợp lệ.")
            return
        if not any(
            int(document["user_id"]) == user_id for document in self.reminders()
        ):
            await send_private(
                interaction,
                "Lịch này không còn tồn tại. Hãy làm mới danh sách.",
            )
            return
        self.selected_user_id = user_id
        self.notice = None
        await self._edit_self(interaction)

    async def turn_page(
        self,
        interaction: discord.Interaction,
        delta: int,
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        pages = self.page_count(len(self.reminders()))
        self.page = min(max(self.page + delta, 0), pages - 1)
        self.selected_user_id = None
        self.notice = None
        await self._edit_self(interaction)

    async def confirm(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        if self.selected_user_id is None:
            await send_private(interaction, "Hãy chọn lịch cần xóa.")
            return
        if self._action_lock.locked():
            await send_private(interaction, "Bảng giờ ngủ đang xử lý, vui lòng chờ.")
            return

        async with self._action_lock:
            guild = self.guild()
            if guild is None:
                await send_private(
                    interaction,
                    "Không tìm thấy server để xóa lịch ngủ.",
                )
                return

            self.submitting = True
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                self.submitting = False
                raise

            result = await self.cog.delete_schedule(guild, self.selected_user_id)
            if not result.ok:
                self.submitting = False
                await send_private(interaction, result.message)
                return

            self.selected_user_id = None
            self.notice = result.message
            if not self.reminders():
                panel = BedtimePanelView(
                    self.cog,
                    guild_id=self.guild_id,
                    author_id=self.author_id,
                    prefix=self.prefix,
                    page=self.return_page,
                )
                panel.message = self.message
                shown = await panel.show(interaction, deferred=True)
                self.submitting = False
                if shown:
                    self.stop()
                    try:
                        await interaction.followup.send(
                            result.message,
                            ephemeral=True,
                            allowed_mentions=NO_MENTIONS,
                        )
                    except discord.HTTPException:
                        logger.debug(
                            "Could not send bedtime remove confirmation "
                            "guild=%s user=%s",
                            self.guild_id,
                            self.author_id,
                            exc_info=True,
                        )
                return

            self._rebuild_items()
            try:
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not refresh bedtime remove UI guild=%s user=%s",
                    self.guild_id,
                    self.author_id,
                )
            self.submitting = False

    async def go_back(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        panel = BedtimePanelView(
            self.cog,
            guild_id=self.guild_id,
            author_id=self.author_id,
            prefix=self.prefix,
            page=self.return_page,
        )
        panel.message = self.message
        if await panel.show(interaction):
            self.stop()
