"""Interactive Blackjack backed by the shared Trap Coin balance."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.minigames.blackjack._blackjack_helpers import (
    BlackjackGame,
    BlackjackOutcome,
    payout_return,
    score_hand,
)
from cogs.minigames._card_game_economy import (
    DEFAULT_BET,
    CardGameBank,
    validate_wager,
)
from cogs.minigames._playing_cards import create_deck, format_hand


logger = logging.getLogger(__name__)

BLACKJACK_TIMEOUT_SECONDS = 120
NO_MENTIONS = discord.AllowedMentions.none()


class BlackjackView(discord.ui.View):
    """Owner-only controls and exactly-once in-process game settlement."""

    def __init__(
        self,
        cog: "BlackjackCog",
        *,
        game: BlackjackGame,
        user_id: int,
        guild_id: int | None,
        display_name: str,
        bet: int,
        balance_after_wager: int,
        session_id: str,
    ) -> None:
        super().__init__(timeout=BLACKJACK_TIMEOUT_SECONDS)
        self.cog = cog
        self.game = game
        self.user_id = int(user_id)
        self.guild_id = guild_id
        self.display_name = display_name
        self.bet = int(bet)
        self.session_id = session_id
        self.message: discord.Message | None = None
        self.balance_after = int(balance_after_wager)
        self.return_amount = 0
        self._settled = False
        self._closed = False
        self._terminal_note: str | None = None
        self._action_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        try:
            await interaction.response.send_message(
                "Chỉ người bắt đầu ván Blackjack mới dùng được các nút này.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not reject non-owner Blackjack interaction session=%s",
                self.session_id,
                exc_info=True,
            )
        return False

    def _disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    def _outcome_text(self) -> str:
        labels = {
            BlackjackOutcome.PLAYER_BLACKJACK: (
                "🎉 **Blackjack! Bạn thắng với tỷ lệ 3:2.**"
            ),
            BlackjackOutcome.PLAYER_WIN: "🎉 **Bạn thắng nhà cái!**",
            BlackjackOutcome.DEALER_WIN: "💀 **Nhà cái thắng ván này.**",
            BlackjackOutcome.PUSH: "🤝 **Hòa! Tiền cược được trả lại.**",
        }
        return labels.get(self.game.outcome, "Ván bài đã kết thúc.")

    def build_embed(self) -> discord.Embed:
        finished = bool(self.game.finished or self._closed)
        player_cards = format_hand(self.game.player_hand)
        player_value = score_hand(self.game.player_hand).total

        if finished:
            dealer_cards = format_hand(self.game.dealer_hand)
            dealer_value = score_hand(self.game.dealer_hand).total
            dealer_line = f"{dealer_cards}\nĐiểm: **{dealer_value}**"
        else:
            visible = format_hand(self.game.dealer_hand[:1])
            dealer_line = f"{visible}  🂠\nĐiểm: **?**"

        if self._terminal_note is not None:
            status = self._terminal_note
        elif self.game.finished:
            status = self._outcome_text()
            if self._settled and self.return_amount:
                status += f"\nNhận lại: **{self.return_amount:,} TC**."
            elif not self._settled:
                status += "\n⏳ Đang thanh toán kết quả…"
        else:
            status = "Chọn **Rút bài** hoặc **Dừng**."

        color = discord.Color.blurple()
        if self._settled and self.game.outcome in {
            BlackjackOutcome.PLAYER_BLACKJACK,
            BlackjackOutcome.PLAYER_WIN,
        }:
            color = discord.Color.green()
        elif self._settled and self.game.outcome == BlackjackOutcome.DEALER_WIN:
            color = discord.Color.red()
        elif self._closed:
            color = discord.Color.dark_grey()

        embed = discord.Embed(
            title="🃏 Blackjack",
            description=(
                f"Người chơi: **{self.display_name}**\n"
                f"Cược: **{self.bet:,} TC**\n\n{status}"
            ),
            color=color,
        )
        embed.add_field(
            name="Bài của bạn",
            value=f"{player_cards}\nĐiểm: **{player_value}**",
            inline=False,
        )
        embed.add_field(name="Bài nhà cái", value=dealer_line, inline=False)
        embed.set_footer(text=f"Số dư: {self.balance_after:,} TC")
        return embed

    def _settle_finished_game(self) -> None:
        """Settle a resolved hand. Caller must hold ``_action_lock``."""

        if self._settled:
            return
        outcome = self.game.outcome
        if not self.game.finished or outcome is None:
            raise RuntimeError("Cannot settle an unfinished Blackjack hand")

        amount = int(payout_return(self.bet, outcome))
        if amount > 0:
            reason = "push" if outcome == BlackjackOutcome.PUSH else "win"
            self.balance_after = self.cog.bank.credit(
                self.user_id,
                self.guild_id,
                "blackjack",
                amount,
                self.session_id,
                reason,
            )
        self.return_amount = amount
        self._settled = True
        self._closed = True

    async def _safe_interaction_edit(
        self, interaction: discord.Interaction
    ) -> None:
        try:
            await interaction.response.edit_message(
                embed=self.build_embed(),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.exception(
                "Could not update Blackjack interaction session=%s",
                self.session_id,
            )

    async def _safe_message_edit(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=self.build_embed(),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.exception(
                "Could not edit Blackjack message session=%s",
                self.session_id,
            )

    async def finish_initial_hand(self) -> None:
        """Pay an opening natural only after its Discord message exists."""

        if not self.game.finished:
            return
        async with self._action_lock:
            try:
                self._settle_finished_game()
            except PyMongoError:
                logger.exception(
                    "Could not settle opening Blackjack hand session=%s",
                    self.session_id,
                )
                self._terminal_note = (
                    "⚠️ Chưa thể thanh toán. Hãy bấm một nút để thử lại."
                )
                await self._safe_message_edit()
                return

            self._terminal_note = None
            self._disable_controls()
            self.cog.unregister(self)
            self.stop()
            await self._safe_message_edit()

    async def _busy_reply(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.send_message(
                "Ván bài đang xử lý thao tác trước đó, chờ một chút nhé.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not send Blackjack busy reply session=%s",
                self.session_id,
                exc_info=True,
            )

    async def handle_action(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        if self._action_lock.locked():
            await self._busy_reply(interaction)
            return

        async with self._action_lock:
            if self._settled or self._closed:
                try:
                    await interaction.response.send_message(
                        "Ván Blackjack này đã kết thúc.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return

            # A natural or a previously failed payment is already resolved;
            # pressing either button simply retries its settlement.
            if not self.game.finished:
                if action == "hit":
                    self.game.hit()
                elif action == "stand":
                    self.game.stand()
                else:
                    raise ValueError(f"Unknown Blackjack action: {action}")

            if not self.game.finished:
                self._terminal_note = None
                await self._safe_interaction_edit(interaction)
                return

            try:
                self._settle_finished_game()
            except PyMongoError:
                logger.exception(
                    "Could not settle Blackjack hand session=%s",
                    self.session_id,
                )
                self._terminal_note = (
                    "⚠️ Chưa thể thanh toán. Hãy bấm một nút để thử lại."
                )
                await self._safe_interaction_edit(interaction)
                return

            self._terminal_note = None
            self._disable_controls()
            self.cog.unregister(self)
            self.stop()
            await self._safe_interaction_edit(interaction)

    async def _refund_open_wager(self, note: str, *, edit: bool) -> None:
        """Refund an abandoned hand once, even if lifecycle hooks race."""

        async with self._action_lock:
            if self._settled or self._closed:
                return
            try:
                self.balance_after = self.cog.bank.credit(
                    self.user_id,
                    self.guild_id,
                    "blackjack",
                    self.bet,
                    self.session_id,
                    "refund",
                )
                self.return_amount = self.bet
                self._settled = True
                self._terminal_note = f"{note} Đã hoàn **{self.bet:,} TC**."
            except PyMongoError:
                logger.exception(
                    "Could not refund Blackjack wager session=%s user=%s",
                    self.session_id,
                    self.user_id,
                )
                self._terminal_note = (
                    f"{note} Không thể hoàn tiền tự động; quản trị viên hãy "
                    f"kiểm tra mã ván `{self.session_id}`."
                )
            finally:
                self._closed = True
                self._disable_controls()
                self.cog.unregister(self)
                self.stop()

            if edit:
                await self._safe_message_edit()

    async def refund_send_failure(self) -> None:
        await self._refund_open_wager(
            "Không thể mở bàn Blackjack.",
            edit=False,
        )

    async def refund_for_unload(self) -> None:
        await self._refund_open_wager(
            "Ván bài dừng vì bot đang tải lại.",
            edit=True,
        )

    async def on_timeout(self) -> None:
        await self._refund_open_wager(
            "⌛ Hết thời gian thao tác.",
            edit=True,
        )

    @discord.ui.button(
        label="Rút bài",
        emoji="🃏",
        style=discord.ButtonStyle.primary,
    )
    async def hit_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.handle_action(interaction, "hit")

    @discord.ui.button(
        label="Dừng",
        emoji="✋",
        style=discord.ButtonStyle.secondary,
    )
    async def stand_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.handle_action(interaction, "stand")


class BlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bank = CardGameBank(bot.db)
        self.active_sessions: dict[int, BlackjackView] = {}
        self._starting_users: set[int] = set()
        self._refund_tasks: set[asyncio.Task[Any]] = set()
        self._unloading = False

    def unregister(self, view: BlackjackView) -> None:
        if self.active_sessions.get(view.user_id) is view:
            self.active_sessions.pop(view.user_id, None)

    def cog_unload(self) -> None:
        self._unloading = True
        views = list(self.active_sessions.values())
        for view in views:
            self.unregister(view)
            try:
                task = asyncio.create_task(
                    view.refund_for_unload(),
                    name=f"blackjack-refund-{view.session_id}",
                )
            except RuntimeError:
                logger.exception(
                    "No running loop available to refund Blackjack session=%s",
                    view.session_id,
                )
                continue
            self._refund_tasks.add(task)
            task.add_done_callback(self._refund_tasks.discard)

    async def _send_plain(self, ctx: commands.Context, content: str) -> None:
        await ctx.send(content, allowed_mentions=NO_MENTIONS)

    async def _refund_setup_failure(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        bet: int,
        session_id: str,
    ) -> None:
        try:
            self.bank.credit(
                user_id,
                guild_id,
                "blackjack",
                bet,
                session_id,
                "refund",
            )
        except PyMongoError:
            logger.exception(
                "Could not refund failed Blackjack setup session=%s user=%s",
                session_id,
                user_id,
            )

    @commands.command(
        name="blackjack",
        help="Chơi Blackjack bằng Trap Coin.",
        usage="blackjack [mức_cược]",
    )
    async def blackjack(
        self,
        ctx: commands.Context,
        bet: int = DEFAULT_BET,
    ) -> None:
        wager_error = validate_wager(bet)
        if wager_error:
            await self._send_plain(ctx, wager_error)
            return

        user_id = int(ctx.author.id)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        if self._unloading:
            await self._send_plain(
                ctx, "Blackjack đang tải lại, bạn thử lại sau một chút nhé."
            )
            return
        if user_id in self._starting_users or user_id in self.active_sessions:
            await self._send_plain(
                ctx,
                "Bạn đang có một ván bài chưa kết thúc. "
                "Hãy chơi xong ván đó trước.",
            )
            return

        self._starting_users.add(user_id)
        session_id = uuid.uuid4().hex
        try:
            try:
                balance_after = self.bank.reserve_wager(
                    user_id,
                    guild_id,
                    "blackjack",
                    bet,
                    session_id,
                )
            except PyMongoError:
                logger.exception(
                    "Could not reserve Blackjack wager session=%s user=%s",
                    session_id,
                    user_id,
                )
                await self._send_plain(
                    ctx, "Không thể trừ tiền cược lúc này. Vui lòng thử lại."
                )
                return

            if balance_after is None:
                await self._send_plain(
                    ctx,
                    f"Bạn không có đủ **{bet:,} TC** để chơi Blackjack.",
                )
                return

            try:
                game = BlackjackGame(create_deck())
                view = BlackjackView(
                    self,
                    game=game,
                    user_id=user_id,
                    guild_id=guild_id,
                    display_name=ctx.author.display_name,
                    bet=bet,
                    balance_after_wager=balance_after,
                    session_id=session_id,
                )
            except Exception:
                logger.exception(
                    "Could not create Blackjack hand session=%s user=%s",
                    session_id,
                    user_id,
                )
                await self._refund_setup_failure(
                    user_id=user_id,
                    guild_id=guild_id,
                    bet=bet,
                    session_id=session_id,
                )
                await self._send_plain(
                    ctx, "Không thể tạo ván Blackjack. Tiền cược đã được hoàn."
                )
                return

            if self._unloading:
                await view.refund_for_unload()
                return
            self.active_sessions[user_id] = view
        finally:
            self._starting_users.discard(user_id)

        try:
            view.message = await ctx.send(
                embed=view.build_embed(),
                view=view,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.exception(
                "Could not send Blackjack table session=%s user=%s",
                session_id,
                user_id,
            )
            await view.refund_send_failure()
            return
        except Exception:
            logger.exception(
                "Unexpected failure sending Blackjack table session=%s user=%s",
                session_id,
                user_id,
            )
            await view.refund_send_failure()
            raise

        if game.finished:
            await view.finish_initial_hand()

    @blackjack.error
    async def blackjack_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.BadArgument):
            await self._send_plain(
                ctx,
                "Mức cược phải là số nguyên. Ví dụ: "
                f"`{ctx.clean_prefix}blackjack 50`.",
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BlackjackCog(bot))
