import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from cogs.mod._member_state import ACTIVE_ROLE_MUTATION_TARGETS
from cogs.onboarding._role_exam_helpers import (
    RoleExamConfig,
    RoleExamConfigError,
    RoleExamQuestion,
    is_passing_score,
    load_role_exam_config,
    required_correct_count,
    score_answers,
    shuffled_questions,
    unsafe_role_permission_names,
)


logger = logging.getLogger(__name__)

ROLE_EXAM_DATA_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "role_exam.json"
)
INVITATION_TIMEOUT_SECONDS = 120
EXAM_TIMEOUT_SECONDS = 600
START_BUTTON_CUSTOM_ID = "role-exam:start"
PREVIOUS_BUTTON_CUSTOM_ID = "role-exam:previous"
NEXT_BUTTON_CUSTOM_ID = "role-exam:next"
CANCEL_BUTTON_CUSTOM_ID = "role-exam:cancel"
CHOICE_CUSTOM_ID_PREFIX = "role-exam:choice:"
CHOICE_LABELS = "ABCDE"

NO_MENTIONS = discord.AllowedMentions.none()


@dataclass(frozen=True)
class ExamFinalizeResult:
    outcome: str
    private_embed: discord.Embed
    public_embed: discord.Embed


def _score_percent(correct: int, total: int) -> int:
    if total <= 0:
        return 0
    return correct * 100 // total


def _member_top_role(member: discord.Member) -> discord.Role | None:
    return getattr(member, "top_role", None)


def role_assignment_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
    role: discord.Role,
    *,
    allow_existing: bool = False,
) -> str | None:
    """Return why the configured exam role cannot be assigned safely."""
    moderator_permissions = getattr(moderator, "guild_permissions", None)
    if moderator_permissions is None or not getattr(
        moderator_permissions,
        "manage_roles",
        False,
    ):
        return "Bạn không còn quyền Manage Roles để mở bài kiểm tra này."

    role_guild = getattr(role, "guild", None)
    if role_guild is not None and role_guild.id != guild.id:
        return "Role phần thưởng không thuộc server này."
    if role.is_default():
        return "Không thể dùng role mặc định `@everyone` làm phần thưởng."
    if role.managed:
        return "Role phần thưởng đang do Discord hoặc integration quản lý."

    unsafe_permissions = unsafe_role_permission_names(role.permissions)
    if unsafe_permissions:
        return (
            "Role phần thưởng có quyền đặc biệt không an toàn: "
            + ", ".join(unsafe_permissions)
            + "."
        )

    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "manage_roles", False):
        return "Bot đang thiếu quyền Manage Roles."

    bot_top_role = _member_top_role(bot_member)
    if bot_top_role is None or role >= bot_top_role:
        return "Role phần thưởng phải thấp hơn role cao nhất của bot."

    moderator_top_role = _member_top_role(moderator)
    if moderator.id != guild.owner_id and (
        moderator_top_role is None or role >= moderator_top_role
    ):
        return "Role phần thưởng phải thấp hơn role cao nhất của bạn."

    target_guild = getattr(target, "guild", None)
    if target_guild is not None and target_guild.id != guild.id:
        return "Thành viên được chọn không thuộc server này."
    if getattr(target, "bot", False):
        return "Không thể mở bài kiểm tra role cho bot."

    target_top_role = _member_top_role(target)
    if (
        target.id != moderator.id
        and moderator.id != guild.owner_id
        and target_top_role is not None
        and moderator_top_role is not None
        and target_top_role >= moderator_top_role
    ):
        return "Bạn chỉ có thể mở bài kiểm tra cho thành viên thấp hơn mình."
    if (
        target_top_role is not None
        and bot_top_role is not None
        and target_top_role >= bot_top_role
    ):
        return "Role cao nhất của bot phải cao hơn thành viên được chọn."

    if not allow_existing and role in target.roles:
        return f"{target.mention} đã có role `{role.name}` rồi."
    return None


def build_invitation_embed(
    config: RoleExamConfig,
    target_mention: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=config.title,
        description=(
            f"{target_mention}, bạn được mời làm bài kiểm tra nhận role.\n"
            "Đạt mức điểm yêu cầu trong cấu hình để nhận role.\n\n"
            f"{config.instructions}\n\n"
            "Bấm **Bắt đầu** để mở bài kiểm tra riêng tư."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Lời mời hết hạn sau 2 phút.")
    return embed


def build_public_status_embed(
    config: RoleExamConfig,
    target_mention: str,
    *,
    status: str,
    role_mention: str | None = None,
) -> discord.Embed:
    status_content = {
        "in_progress": (
            "🧠 Đang làm bài",
            f"{target_mention} đã bắt đầu bài kiểm tra riêng tư.",
            discord.Color.blurple(),
        ),
        "passed": (
            "✅ Đã vượt qua bài kiểm tra",
            (
                f"{target_mention} đã vượt qua bài kiểm tra"
                + (f" và nhận {role_mention}." if role_mention else ".")
            ),
            discord.Color.green(),
        ),
        "passed_pending": (
            "⚠️ Đã đạt nhưng chưa gán được role",
            (
                f"{target_mention} đã đạt bài kiểm tra, nhưng bot không thể "
                "gán role. Staff hãy kiểm tra cấu hình và gán thủ công; "
                "không cần thi lại."
            ),
            discord.Color.orange(),
        ),
        "failed": (
            "❌ Chưa đạt bài kiểm tra",
            (
                f"{target_mention} chưa đạt. Staff cần gọi lại "
                "`role_exam @user` để mở lượt mới."
            ),
            discord.Color.red(),
        ),
        "cancelled": (
            "🛑 Đã hủy bài kiểm tra",
            (
                f"{target_mention} đã hủy bài kiểm tra. Staff cần gọi lại "
                "lệnh để mở lượt mới."
            ),
            discord.Color.dark_grey(),
        ),
        "expired": (
            "⌛ Bài kiểm tra đã hết hạn",
            (
                f"Lượt của {target_mention} đã hết hạn. Staff cần gọi lại "
                "lệnh để mở lượt mới."
            ),
            discord.Color.dark_grey(),
        ),
        "reloading": (
            "🔄 Bài kiểm tra đã dừng",
            (
                f"Lượt của {target_mention} đã dừng vì bot đang tải lại. "
                "Staff cần gọi lại lệnh."
            ),
            discord.Color.dark_grey(),
        ),
        "technical_error": (
            "⚠️ Không thể mở bài kiểm tra",
            (
                f"Lượt của {target_mention} gặp lỗi kỹ thuật. "
                "Staff hãy gọi lại lệnh sau."
            ),
            discord.Color.orange(),
        ),
    }
    title, description, color = status_content[status]
    return discord.Embed(title=title, description=description, color=color)


class ExamChoiceButton(discord.ui.Button["RoleExamView"]):
    def __init__(
        self,
        *,
        label: str,
        choice_id: str,
        selected: bool,
    ) -> None:
        super().__init__(
            label=label,
            style=(
                discord.ButtonStyle.primary
                if selected
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"{CHOICE_CUSTOM_ID_PREFIX}{choice_id}",
            row=0,
        )
        self.choice_id = choice_id

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, RoleExamView):
            await view.choose_answer(interaction, self.choice_id)


class PreviousQuestionButton(discord.ui.Button["RoleExamView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Câu trước",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            custom_id=PREVIOUS_BUTTON_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, RoleExamView):
            await view.previous_question(interaction)


class NextQuestionButton(discord.ui.Button["RoleExamView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Câu tiếp",
            emoji="➡️",
            style=discord.ButtonStyle.success,
            custom_id=NEXT_BUTTON_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, RoleExamView):
            await view.next_question(interaction)


class CancelExamButton(discord.ui.Button["RoleExamView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Hủy",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            custom_id=CANCEL_BUTTON_CUSTOM_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, RoleExamView):
            await view.cancel(interaction)


class RoleExamView(discord.ui.View):
    """Private, target-only reviewable exam UI."""

    def __init__(
        self,
        cog: "RoleExamCog",
        *,
        config: RoleExamConfig,
        questions: tuple[RoleExamQuestion, ...],
        guild_id: int,
        moderator_id: int,
        target_id: int,
        target_mention: str,
        session_key: tuple[int, int],
        public_message: discord.Message | None,
    ) -> None:
        super().__init__(timeout=EXAM_TIMEOUT_SECONDS)
        self.cog = cog
        self.config = config
        self.questions = questions
        self.guild_id = guild_id
        self.moderator_id = moderator_id
        self.target_id = target_id
        self.target_mention = target_mention
        self.session_key = session_key
        self.public_message = public_message
        self.message: discord.Message | None = None
        self.current_index = 0
        self.answers: dict[str, str] = {}
        self.completed = False
        self.submitting = False
        self._action_lock = asyncio.Lock()
        self.previous_button = PreviousQuestionButton()
        self.next_button = NextQuestionButton()
        self.cancel_button = CancelExamButton()
        self._sync_components()

    @property
    def current_question(self) -> RoleExamQuestion:
        return self.questions[self.current_index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "Chỉ thành viên được mời mới có thể làm bài kiểm tra này.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return False
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bài kiểm tra này đã hoàn tất hoặc hết hạn.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return False
        if self.submitting:
            await interaction.response.send_message(
                "Bài đang được chấm, vui lòng chờ.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return False
        return True

    async def _begin_action(self, interaction: discord.Interaction) -> bool:
        if not await self.interaction_check(interaction):
            return False
        if self._action_lock.locked():
            await interaction.response.send_message(
                "Một thao tác khác đang được xử lý, vui lòng thử lại.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return False
        return True

    def _sync_components(self) -> None:
        self.clear_items()
        question = self.current_question
        selected_id = self.answers.get(question.id)
        for index, choice in enumerate(question.choices):
            self.add_item(
                ExamChoiceButton(
                    label=CHOICE_LABELS[index],
                    choice_id=choice.id,
                    selected=choice.id == selected_id,
                )
            )

        self.previous_button.disabled = self.current_index == 0
        self.next_button.disabled = selected_id is None
        if self.current_index == len(self.questions) - 1:
            self.next_button.label = "Nộp bài"
            self.next_button.emoji = "✅"
        else:
            self.next_button.label = "Câu tiếp"
            self.next_button.emoji = "➡️"
        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self.add_item(self.cancel_button)

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    def build_embed(self) -> discord.Embed:
        question = self.current_question
        required = required_correct_count(
            len(self.questions),
            self.config.required_percent,
        )
        choice_lines = [
            f"**{CHOICE_LABELS[index]}.** {choice.text}"
            for index, choice in enumerate(question.choices)
        ]
        embed = discord.Embed(
            title=self.config.title,
            description=(
                f"**Câu {self.current_index + 1}:** {question.prompt}\n\n"
                "**Chọn một đáp án**\n"
                + "\n".join(choice_lines)
            ),
            color=discord.Color.blurple(),
        )
        selected_id = self.answers.get(question.id)
        if selected_id is not None:
            selected_index = next(
                index
                for index, choice in enumerate(question.choices)
                if choice.id == selected_id
            )
            embed.add_field(
                name="Đã chọn",
                value=CHOICE_LABELS[selected_index],
                inline=True,
            )
        embed.set_footer(
            text=(
                f"Câu {self.current_index + 1}/{len(self.questions)} • "
                f"Đã trả lời {len(self.answers)}/{len(self.questions)} • "
                f"Cần {required}/{len(self.questions)} "
                f"({self.config.required_percent}%) • Không hiển thị đáp án đúng"
            )
        )
        return embed

    async def choose_answer(
        self,
        interaction: discord.Interaction,
        choice_id: str,
    ) -> None:
        if not await self._begin_action(interaction):
            return
        async with self._action_lock:
            valid_choice_ids = {
                choice.id for choice in self.current_question.choices
            }
            if choice_id not in valid_choice_ids:
                await interaction.response.send_message(
                    "Đáp án đã chọn không hợp lệ.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            self.answers[self.current_question.id] = choice_id
            self._sync_components()
            await interaction.response.edit_message(
                embed=self.build_embed(),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )

    async def previous_question(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._begin_action(interaction):
            return
        async with self._action_lock:
            if self.current_index == 0:
                await interaction.response.send_message(
                    "Bạn đang ở câu đầu tiên.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            self.current_index -= 1
            self._sync_components()
            await interaction.response.edit_message(
                embed=self.build_embed(),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )

    async def next_question(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._begin_action(interaction):
            return
        async with self._action_lock:
            if self.current_question.id not in self.answers:
                await interaction.response.send_message(
                    "Hãy chọn một đáp án trước khi tiếp tục.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            if self.current_index < len(self.questions) - 1:
                self.current_index += 1
                self._sync_components()
                await interaction.response.edit_message(
                    embed=self.build_embed(),
                    view=self,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            await self._submit_unlocked(interaction)

    async def _submit_unlocked(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if len(self.answers) != len(self.questions):
            await interaction.response.send_message(
                "Bạn cần trả lời đủ tất cả câu hỏi trước khi nộp bài.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        self.submitting = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.submitting = False
            raise

        correct = score_answers(self.questions, self.answers)
        try:
            result = await self.cog.finalize_attempt(self, correct)
        except Exception:
            self.submitting = False
            logger.exception(
                "Unexpected role exam finalize failure guild=%s target=%s",
                self.guild_id,
                self.target_id,
            )
            try:
                await interaction.followup.send(
                    "Không thể chấm bài lúc này. Hãy thử bấm Nộp bài lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not report role exam finalize failure guild=%s target=%s",
                    self.guild_id,
                    self.target_id,
                )
            return

        self.submitting = False
        self.completed = True
        self.disable_all()
        self.stop()
        self.cog.unregister_session(self.session_key, self)

        try:
            await interaction.edit_original_response(
                embed=result.private_embed,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.exception(
                "Could not update private role exam result guild=%s target=%s",
                self.guild_id,
                self.target_id,
            )
            if self.message is not None:
                try:
                    await self.message.edit(
                        embed=result.private_embed,
                        view=self,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not edit stored private role exam result guild=%s target=%s",
                        self.guild_id,
                        self.target_id,
                    )
        await self._edit_public(result.public_embed)

    async def cancel(self, interaction: discord.Interaction) -> None:
        if not await self._begin_action(interaction):
            return
        async with self._action_lock:
            self.completed = True
            self.disable_all()
            self.stop()
            self.cog.unregister_session(self.session_key, self)
            private_embed = discord.Embed(
                title="🛑 Đã hủy bài kiểm tra",
                description="Staff cần gọi lại lệnh để mở lượt thi mới.",
                color=discord.Color.dark_grey(),
            )
            await interaction.response.edit_message(
                embed=private_embed,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
            await self._edit_public(
                build_public_status_embed(
                    self.config,
                    self.target_mention,
                    status="cancelled",
                )
            )
            logger.info(
                "Role exam cancelled guild=%s moderator=%s target=%s",
                self.guild_id,
                self.moderator_id,
                self.target_id,
            )

    async def _edit_public(self, embed: discord.Embed) -> None:
        if self.public_message is None:
            return
        try:
            await self.public_message.edit(
                embed=embed,
                view=None,
                allowed_mentions=NO_MENTIONS,
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug(
                "Could not update public role exam panel guild=%s target=%s",
                self.guild_id,
                self.target_id,
                exc_info=True,
            )

    async def on_timeout(self) -> None:
        async with self._action_lock:
            if self.completed:
                return
            self.completed = True
            self.disable_all()
            self.stop()
            self.cog.unregister_session(self.session_key, self)
        private_embed = discord.Embed(
            title="⌛ Bài kiểm tra đã hết hạn",
            description="Staff cần gọi lại lệnh để mở lượt thi mới.",
            color=discord.Color.dark_grey(),
        )
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=private_embed,
                    view=self,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                logger.debug(
                    "Could not disable expired private role exam guild=%s target=%s",
                    self.guild_id,
                    self.target_id,
                    exc_info=True,
                )
        await self._edit_public(
            build_public_status_embed(
                self.config,
                self.target_mention,
                status="expired",
            )
        )
        logger.info(
            "Role exam expired guild=%s moderator=%s target=%s",
            self.guild_id,
            self.moderator_id,
            self.target_id,
        )

    async def close_for_unload(self) -> None:
        async with self._action_lock:
            if self.completed:
                return
            self.completed = True
            self.disable_all()
            self.stop()
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                logger.debug(
                    "Could not disable private role exam during unload guild=%s target=%s",
                    self.guild_id,
                    self.target_id,
                    exc_info=True,
                )
        await self._edit_public(
            build_public_status_embed(
                self.config,
                self.target_mention,
                status="reloading",
            )
        )


class StartExamButton(discord.ui.Button["RoleExamInvitationView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Bắt đầu",
            emoji="📝",
            style=discord.ButtonStyle.success,
            custom_id=START_BUTTON_CUSTOM_ID,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, RoleExamInvitationView):
            await view.start_exam(interaction)


class RoleExamInvitationView(discord.ui.View):
    """Public invitation whose Start button opens the target's private exam."""

    def __init__(
        self,
        cog: "RoleExamCog",
        *,
        config: RoleExamConfig,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        target_mention: str,
        session_key: tuple[int, int],
    ) -> None:
        super().__init__(timeout=INVITATION_TIMEOUT_SECONDS)
        self.cog = cog
        self.config = config
        self.guild_id = guild_id
        self.moderator_id = moderator_id
        self.target_id = target_id
        self.target_mention = target_mention
        self.session_key = session_key
        self.message: discord.Message | None = None
        self.completed = False
        self.starting = False
        self._action_lock = asyncio.Lock()
        self.start_button = StartExamButton()
        self.add_item(self.start_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "Chỉ thành viên được nhắc đến mới có thể bắt đầu bài kiểm tra.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return False
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Lời mời này đã hoàn tất hoặc hết hạn.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return False
        if self.starting:
            await interaction.response.send_message(
                "Bài kiểm tra đang được mở, vui lòng chờ.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return False
        return True

    async def start_exam(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        if self._action_lock.locked():
            await interaction.response.send_message(
                "Bài kiểm tra đang được mở, vui lòng chờ.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        async with self._action_lock:
            if not self.cog.is_active_session(self.session_key, self):
                await interaction.response.send_message(
                    "Lời mời này không còn hiệu lực.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            self.starting = True
            exam_view = RoleExamView(
                self.cog,
                config=self.config,
                questions=shuffled_questions(self.config),
                guild_id=self.guild_id,
                moderator_id=self.moderator_id,
                target_id=self.target_id,
                target_mention=self.target_mention,
                session_key=self.session_key,
                public_message=self.message,
            )
            try:
                await interaction.response.send_message(
                    embed=exam_view.build_embed(),
                    view=exam_view,
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                self.starting = False
                exam_view.stop()
                logger.exception(
                    "Could not open private role exam guild=%s target=%s",
                    self.guild_id,
                    self.target_id,
                )
                await self._finish_with_status("technical_error")
                return

            try:
                exam_view.message = await interaction.original_response()
            except discord.HTTPException:
                logger.debug(
                    "Could not retain private role exam message guild=%s target=%s",
                    self.guild_id,
                    self.target_id,
                    exc_info=True,
                )

            if not self.cog.replace_session(
                self.session_key,
                self,
                exam_view,
            ):
                exam_view.stop()
                self.starting = False
                try:
                    await interaction.edit_original_response(
                        content="Lời mời này không còn hiệu lực.",
                        embed=None,
                        view=None,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    logger.debug(
                        "Could not close stale private role exam guild=%s target=%s",
                        self.guild_id,
                        self.target_id,
                        exc_info=True,
                    )
                return

            self.starting = False
            self.completed = True
            self.start_button.disabled = True
            self.stop()
            if self.message is not None:
                try:
                    await self.message.edit(
                        embed=build_public_status_embed(
                            self.config,
                            self.target_mention,
                            status="in_progress",
                        ),
                        view=self,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    logger.debug(
                        "Could not update started role exam panel guild=%s target=%s",
                        self.guild_id,
                        self.target_id,
                        exc_info=True,
                    )
            logger.info(
                "Role exam started guild=%s moderator=%s target=%s",
                self.guild_id,
                self.moderator_id,
                self.target_id,
            )

    async def _finish_with_status(self, status: str) -> None:
        self.completed = True
        self.start_button.disabled = True
        self.stop()
        self.cog.unregister_session(self.session_key, self)
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=build_public_status_embed(
                    self.config,
                    self.target_mention,
                    status=status,
                ),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not close role exam invitation guild=%s target=%s status=%s",
                self.guild_id,
                self.target_id,
                status,
                exc_info=True,
            )

    async def on_timeout(self) -> None:
        async with self._action_lock:
            if self.completed:
                return
            await self._finish_with_status("expired")
        logger.info(
            "Role exam invitation expired guild=%s moderator=%s target=%s",
            self.guild_id,
            self.moderator_id,
            self.target_id,
        )

    async def close_for_unload(self) -> None:
        async with self._action_lock:
            if self.completed:
                return
            await self._finish_with_status("reloading")


class RoleExamCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        config_path: Path = ROLE_EXAM_DATA_FILE,
    ) -> None:
        self.bot = bot
        self.config_path = config_path
        self.config: RoleExamConfig | None = None
        self.config_error: str | None = None
        self.active_sessions: dict[
            tuple[int, int],
            RoleExamInvitationView | RoleExamView,
        ] = {}
        self._starting_sessions: set[tuple[int, int]] = set()
        self._cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._unloading = False
        self._load_config()

    def _load_config(self) -> None:
        try:
            self.config = load_role_exam_config(self.config_path)
        except (RoleExamConfigError, OSError) as exc:
            self.config = None
            self.config_error = str(exc)
            logger.error(
                "Could not load role exam configuration from %s: %s",
                self.config_path,
                exc,
            )
        except Exception as exc:
            self.config = None
            self.config_error = "Lỗi cấu hình ngoài dự kiến."
            logger.exception(
                "Unexpected role exam configuration failure path=%s",
                self.config_path,
            )
        else:
            self.config_error = None

    def is_active_session(
        self,
        key: tuple[int, int],
        view: RoleExamInvitationView | RoleExamView,
    ) -> bool:
        return self.active_sessions.get(key) is view

    def replace_session(
        self,
        key: tuple[int, int],
        current: RoleExamInvitationView | RoleExamView,
        replacement: RoleExamInvitationView | RoleExamView,
    ) -> bool:
        if self.active_sessions.get(key) is not current:
            return False
        self.active_sessions[key] = replacement
        return True

    def unregister_session(
        self,
        key: tuple[int, int],
        view: RoleExamInvitationView | RoleExamView,
    ) -> None:
        if self.active_sessions.get(key) is view:
            self.active_sessions.pop(key, None)

    def cog_unload(self) -> None:
        self._unloading = True
        sessions = list(self.active_sessions.items())
        self.active_sessions.clear()
        self._starting_sessions.clear()
        for _, view in sessions:
            try:
                task = asyncio.create_task(
                    view.close_for_unload(),
                    name=(
                        f"role-exam-unload-{view.guild_id}-{view.target_id}"
                    ),
                )
            except RuntimeError:
                view.stop()
                logger.exception(
                    "No running loop available to close role exam guild=%s target=%s",
                    view.guild_id,
                    view.target_id,
                )
                continue
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)

    def _resolve_configured_role(
        self,
        guild: discord.Guild,
    ) -> tuple[discord.Role | None, str | None]:
        if self.config is None:
            detail = f" Chi tiết: {self.config_error}" if self.config_error else ""
            return (
                None,
                "Không thể đọc `data/role_exam.json`." + detail,
            )
        if self.config.role_id is None:
            return (
                None,
                (
                    "`role_id` trong `data/role_exam.json` đang là `null`. "
                    "Hãy điền Discord role ID rồi khởi động lại bot."
                ),
            )
        role = guild.get_role(self.config.role_id)
        if role is None:
            return (
                None,
                (
                    "Không tìm thấy role đã cấu hình trong server này. "
                    "Hãy kiểm tra `role_id` trong `data/role_exam.json`."
                ),
            )
        return role, None

    async def finalize_attempt(
        self,
        view: RoleExamView,
        correct: int,
    ) -> ExamFinalizeResult:
        total = len(view.questions)
        passed = is_passing_score(
            correct,
            total,
            view.config.required_percent,
        )
        percent = _score_percent(correct, total)
        required = required_correct_count(
            total,
            view.config.required_percent,
        )

        if not passed:
            logger.info(
                "Role exam failed guild=%s moderator=%s target=%s score=%s/%s",
                view.guild_id,
                view.moderator_id,
                view.target_id,
                correct,
                total,
            )
            return ExamFinalizeResult(
                outcome="failed",
                private_embed=discord.Embed(
                    title="❌ Bạn chưa đạt bài kiểm tra",
                    description=(
                        f"Kết quả: **{correct}/{total} câu ({percent}%)**.\n"
                        f"Yêu cầu: **{required}/{total} câu "
                        f"({view.config.required_percent}%)**.\n"
                        "Staff cần gọi lại lệnh để mở lượt thi mới."
                    ),
                    color=discord.Color.red(),
                ),
                public_embed=build_public_status_embed(
                    view.config,
                    view.target_mention,
                    status="failed",
                ),
            )

        grant_outcome, grant_message, role_mention = await self._grant_passed_role(
            view,
            correct,
            total,
        )
        if grant_outcome in {"granted", "already_has"}:
            logger.info(
                "Role exam passed guild=%s moderator=%s target=%s score=%s/%s outcome=%s",
                view.guild_id,
                view.moderator_id,
                view.target_id,
                correct,
                total,
                grant_outcome,
            )
            return ExamFinalizeResult(
                outcome=grant_outcome,
                private_embed=discord.Embed(
                    title="✅ Bạn đã vượt qua bài kiểm tra",
                    description=(
                        f"Kết quả: **{correct}/{total} câu ({percent}%)**.\n"
                        f"{grant_message}"
                    ),
                    color=discord.Color.green(),
                ),
                public_embed=build_public_status_embed(
                    view.config,
                    view.target_mention,
                    status="passed",
                    role_mention=role_mention,
                ),
            )

        logger.warning(
            "Role exam passed but grant pending guild=%s moderator=%s "
            "target=%s score=%s/%s reason=%s",
            view.guild_id,
            view.moderator_id,
            view.target_id,
            correct,
            total,
            grant_message,
        )
        return ExamFinalizeResult(
            outcome="passed_pending",
            private_embed=discord.Embed(
                title="⚠️ Bạn đã đạt nhưng bot chưa gán được role",
                description=(
                    f"Kết quả: **{correct}/{total} câu ({percent}%)**.\n"
                    f"{grant_message}\n"
                    "Staff có thể gán role thủ công; bạn không cần thi lại."
                ),
                color=discord.Color.orange(),
            ),
            public_embed=build_public_status_embed(
                view.config,
                view.target_mention,
                status="passed_pending",
            ),
        )

    async def _fetch_member(
        self,
        guild: discord.Guild,
        member_id: int,
    ) -> discord.Member | None:
        try:
            return await guild.fetch_member(member_id)
        except discord.NotFound:
            return None

    async def _grant_passed_role(
        self,
        view: RoleExamView,
        correct: int,
        total: int,
    ) -> tuple[str, str, str | None]:
        guild = self.bot.get_guild(view.guild_id)
        if guild is None:
            return "pending", "Bot không còn kết nối với server.", None

        try:
            target = await self._fetch_member(guild, view.target_id)
            moderator = await self._fetch_member(guild, view.moderator_id)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not refresh role exam members guild=%s target=%s",
                view.guild_id,
                view.target_id,
            )
            return (
                "pending",
                "Bot không thể làm mới thông tin member để gán role an toàn.",
                None,
            )

        if target is None:
            return "pending", "Bạn không còn ở trong server.", None
        if moderator is None:
            return "pending", "Staff mở bài kiểm tra không còn ở trong server.", None

        if view.config.role_id is None:
            return "pending", "Role phần thưởng chưa được cấu hình.", None
        role = guild.get_role(view.config.role_id)
        if role is None:
            return "pending", "Role phần thưởng không còn tồn tại.", None

        if role in target.roles:
            return (
                "already_has",
                f"Bạn đã có role `{role.name}`.",
                role.mention,
            )

        denial = role_assignment_denial(
            guild,
            moderator,
            target,
            role,
        )
        if denial is not None:
            return "pending", denial, None

        mutation_key = (guild.id, target.id)
        if mutation_key in ACTIVE_ROLE_MUTATION_TARGETS:
            return (
                "pending",
                "Một thao tác role khác cho bạn đang chạy.",
                None,
            )

        ACTIVE_ROLE_MUTATION_TARGETS.add(mutation_key)
        try:
            await target.add_roles(
                role,
                reason=(
                    "Passed role_exam initiated by "
                    f"{moderator} ({moderator.id}): {correct}/{total}"
                ),
            )
        except discord.NotFound:
            return "pending", "Member hoặc role không còn tồn tại.", None
        except discord.Forbidden:
            return (
                "pending",
                "Discord từ chối gán role; hãy kiểm tra quyền và thứ bậc role.",
                None,
            )
        except discord.HTTPException:
            logger.exception(
                "Discord role exam grant failed guild=%s target=%s role=%s",
                guild.id,
                target.id,
                role.id,
            )
            return "pending", "Discord tạm thời không thể gán role.", None
        finally:
            ACTIVE_ROLE_MUTATION_TARGETS.discard(mutation_key)

        return "granted", f"Bạn đã nhận role `{role.name}`.", role.mention

    @commands.command(
        name="role_exam",
        help="Mở bài kiểm tra riêng tư để member nhận role đã cấu hình.",
        usage="role_exam @user",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def role_exam(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ) -> None:
        guild = ctx.guild
        if guild is None:
            return
        if self._unloading:
            await ctx.reply(
                "Tính năng role exam đang tải lại, vui lòng thử sau.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if not isinstance(ctx.author, discord.Member):
            await ctx.reply(
                "Không thể xác định staff mở bài kiểm tra.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if member.bot:
            await ctx.reply(
                "Không thể mở bài kiểm tra role cho bot.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        role, config_error = self._resolve_configured_role(guild)
        if role is None or self.config is None:
            await ctx.reply(
                config_error or "Bài kiểm tra chưa được cấu hình.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        denial = role_assignment_denial(
            guild,
            ctx.author,
            member,
            role,
        )
        if denial is not None:
            await ctx.reply(
                denial,
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        key = (guild.id, member.id)
        if key in self._starting_sessions or key in self.active_sessions:
            await ctx.reply(
                "Thành viên này đang có một lời mời hoặc bài kiểm tra chưa kết thúc.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        self._starting_sessions.add(key)
        view = RoleExamInvitationView(
            self,
            config=self.config,
            guild_id=guild.id,
            moderator_id=ctx.author.id,
            target_id=member.id,
            target_mention=member.mention,
            session_key=key,
        )
        self.active_sessions[key] = view
        try:
            try:
                view.message = await ctx.reply(
                    embed=build_invitation_embed(self.config, member.mention),
                    view=view,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        users=[member],
                        roles=False,
                        replied_user=False,
                    ),
                )
                if self._unloading or not self.is_active_session(key, view):
                    view.completed = True
                    view.start_button.disabled = True
                    view.stop()
                    self.unregister_session(key, view)
                    try:
                        await view.message.edit(
                            embed=build_public_status_embed(
                                self.config,
                                member.mention,
                                status="reloading",
                            ),
                            view=view,
                            allowed_mentions=NO_MENTIONS,
                        )
                    except discord.HTTPException:
                        logger.debug(
                            "Could not close raced role exam invitation "
                            "guild=%s target=%s",
                            guild.id,
                            member.id,
                            exc_info=True,
                        )
            except discord.HTTPException:
                view.stop()
                self.unregister_session(key, view)
                logger.exception(
                    "Could not send role exam invitation guild=%s moderator=%s target=%s",
                    guild.id,
                    ctx.author.id,
                    member.id,
                )
                try:
                    await ctx.send(
                        "Không thể gửi lời mời role exam. Vui lòng thử lại.",
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not report role exam invitation failure guild=%s target=%s",
                        guild.id,
                        member.id,
                    )
        finally:
            self._starting_sessions.discard(key)

    @role_exam.error
    async def role_exam_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Lệnh này chỉ dùng được trong server.")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn cần quyền Manage Roles để mở bài kiểm tra.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"Cú pháp: `{ctx.clean_prefix}role_exam @user`.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Không tìm thấy member được nhắc đến trong server.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RoleExamCog(bot))
