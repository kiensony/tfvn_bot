"""Solo five-card draw poker played with Trap Coin wagers."""

from __future__ import annotations

import asyncio
import logging
import uuid

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.minigames._card_game_economy import (
    DEFAULT_BET,
    MAX_BET,
    MIN_BET,
    CardGameBank,
    validate_wager,
)
from cogs.minigames._playing_cards import create_deck, format_hand
from cogs.minigames.poker._poker_helpers import (
    PokerGame,
    evaluate_hand,
    rank_label,
)


logger = logging.getLogger(__name__)

POKER_TIMEOUT_SECONDS = 120
POKER_GAME_NAME = "poker"
HIDDEN_DEALER_HAND = "🂠 🂠 🂠 🂠 🂠"
NO_MENTIONS = discord.AllowedMentions.none()


class PokerCardButton(discord.ui.Button["PokerView"]):
    """Toggle one card in the player's hand for the draw."""

    def __init__(self, card_index: int) -> None:
        super().__init__(
            label=str(card_index + 1),
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.card_index = card_index

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, PokerView):
            await view.toggle_card(interaction, self.card_index)


class PokerView(discord.ui.View):
    """Owner-only controls for one wagered five-card draw round."""

    def __init__(
        self,
        cog: "PokerCog",
        *,
        author_id: int,
        owner_name: str,
        guild_id: int | None,
        bet: int,
        session_id: str,
        game: PokerGame,
        balance_after_reserve: int,
    ) -> None:
        super().__init__(timeout=POKER_TIMEOUT_SECONDS)
        self.cog = cog
        self.author_id = author_id
        self.owner_name = owner_name
        self.guild_id = guild_id
        self.bet = bet
        self.session_id = session_id
        self.game = game
        self.balance = balance_after_reserve
        self.message: discord.Message | None = None
        self.selected_indices: set[int] = set()
        self.processing = False
        self.completed = False
        self._money_closed = False
        self._action_lock = asyncio.Lock()

        self.card_buttons: list[PokerCardButton] = []
        for card_index in range(5):
            card_button = PokerCardButton(card_index)
            self.card_buttons.append(card_button)
            self.add_item(card_button)
        self.draw_button.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Chỉ người mở bàn poker mới dùng được các nút này.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
        return False

    def _disable_controls(self) -> None:
        for child in self.children:
            child.disabled = True

    def _refresh_selection_controls(self) -> None:
        for button in self.card_buttons:
            button.style = (
                discord.ButtonStyle.primary
                if button.card_index in self.selected_indices
                else discord.ButtonStyle.secondary
            )
        self.draw_button.disabled = not self.selected_indices

    def _selection_text(self) -> str:
        if not self.selected_indices:
            return "Chưa chọn lá nào."
        numbers = ", ".join(str(index + 1) for index in sorted(self.selected_indices))
        return f"Đổi lá số **{numbers}**."

    def build_embed(self) -> discord.Embed:
        """Render the table without revealing the dealer before showdown."""
        embed = discord.Embed(
            title="🃏 Poker 5 lá",
            description=(
                f"**{discord.utils.escape_markdown(self.owner_name)}** đang đấu với nhà cái."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Bài của bạn",
            value=format_hand(self.game.player_hand),
            inline=False,
        )
        embed.add_field(
            name="Bài nhà cái",
            value=HIDDEN_DEALER_HAND,
            inline=False,
        )
        embed.add_field(name="Đã chọn", value=self._selection_text(), inline=False)
        embed.add_field(name="Tiền cược", value=f"**{self.bet:,} TC**", inline=True)
        embed.add_field(
            name="Số dư",
            value=f"**{self.balance:,} TC**",
            inline=True,
        )
        embed.set_footer(
            text="Chọn tối đa 3 lá rồi bấm Đổi bài, hoặc bấm Dằn bài."
        )
        return embed

    def _showdown_embed(self) -> discord.Embed:
        player_label = rank_label(evaluate_hand(self.game.player_hand))
        dealer_label = rank_label(evaluate_hand(self.game.dealer_hand))

        if self.game.result == 1:
            title = "🎉 Bạn thắng!"
            result_text = (
                f"Bạn nhận **{self.bet * 2:,} TC** (gồm tiền cược)."
            )
            color = discord.Color.green()
        elif self.game.result == 0:
            title = "🤝 Hòa!"
            result_text = f"Bạn được hoàn **{self.bet:,} TC**."
            color = discord.Color.blurple()
        else:
            title = "💀 Nhà cái thắng"
            result_text = f"Bạn mất **{self.bet:,} TC**."
            color = discord.Color.red()

        embed = discord.Embed(title=title, description=result_text, color=color)
        embed.add_field(
            name=f"Bài của bạn · {player_label}",
            value=format_hand(self.game.player_hand),
            inline=False,
        )
        embed.add_field(
            name=f"Bài nhà cái · {dealer_label}",
            value=format_hand(self.game.dealer_hand),
            inline=False,
        )
        embed.add_field(
            name="Số dư",
            value=f"**{self.balance:,} TC**",
            inline=False,
        )
        return embed

    def _error_embed(self) -> discord.Embed:
        return discord.Embed(
            title="⚠️ Không thể quyết toán bàn poker",
            description=(
                "Đã xảy ra lỗi khi cập nhật Trap Coin. "
                "Vui lòng báo quản trị viên để kiểm tra giao dịch."
            ),
            color=discord.Color.orange(),
        )

    async def toggle_card(
        self,
        interaction: discord.Interaction,
        card_index: int,
    ) -> None:
        if self.completed or self.processing:
            await interaction.response.send_message(
                "Bàn poker này đang xử lý hoặc đã kết thúc.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if card_index in self.selected_indices:
            self.selected_indices.remove(card_index)
        elif len(self.selected_indices) >= 3:
            await interaction.response.send_message(
                "Bạn chỉ được chọn tối đa **3 lá** để đổi.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        else:
            self.selected_indices.add(card_index)

        self._refresh_selection_controls()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    def _settle_wager(self) -> None:
        """Apply the terminal payout at most once."""
        if self._money_closed:
            return
        if self.game.result not in (-1, 0, 1):
            raise RuntimeError("Poker game finished without a valid result")
        self._money_closed = True

        if self.game.result == 1:
            self.balance = self.cog.bank.credit(
                self.author_id,
                self.guild_id,
                POKER_GAME_NAME,
                self.bet * 2,
                self.session_id,
                "win",
            )
        elif self.game.result == 0:
            self.balance = self.cog.bank.credit(
                self.author_id,
                self.guild_id,
                POKER_GAME_NAME,
                self.bet,
                self.session_id,
                "push",
            )

    def refund_and_close(self, source: str) -> bool:
        """Refund an unfinished round and make all later cleanup a no-op."""
        if self._money_closed:
            return False
        self._money_closed = True
        refunded = False
        try:
            self.balance = self.cog.bank.credit(
                self.author_id,
                self.guild_id,
                POKER_GAME_NAME,
                self.bet,
                self.session_id,
                "refund",
            )
            refunded = True
        except PyMongoError:
            logger.exception(
                "Failed to refund poker session=%s user=%s source=%s",
                self.session_id,
                self.author_id,
                source,
            )

        self.completed = True
        self.processing = False
        self._disable_controls()
        self.cog.unregister_game(self)
        self.stop()
        return refunded

    async def _play(self, interaction: discord.Interaction, *, draw: bool) -> None:
        if self.completed or self.processing or self._action_lock.locked():
            await interaction.response.send_message(
                "Nước đi này đang được xử lý hoặc bàn đã kết thúc.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if draw and not self.selected_indices:
            await interaction.response.send_message(
                "Hãy chọn ít nhất **1 lá** trước khi đổi bài.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        async with self._action_lock:
            if self.completed:
                return
            # Set before any await so rapid clicks cannot settle the wager twice.
            self.processing = True
            try:
                if draw:
                    self.game.draw(sorted(self.selected_indices))
                else:
                    self.game.stand()
                if not self.game.finished:
                    raise RuntimeError("Poker action did not finish the round")
                self._settle_wager()
            except PyMongoError:
                logger.exception(
                    "Failed to settle poker session=%s user=%s",
                    self.session_id,
                    self.author_id,
                )
                self.completed = True
                self.processing = False
                self._disable_controls()
                self.cog.unregister_game(self)
                self.stop()
                try:
                    await interaction.response.edit_message(
                        embed=self._error_embed(),
                        view=self,
                        allowed_mentions=NO_MENTIONS,
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "Could not update failed poker session=%s",
                        self.session_id,
                    )
                return
            except (RuntimeError, ValueError):
                self.processing = False
                logger.exception(
                    "Invalid poker action session=%s user=%s",
                    self.session_id,
                    self.author_id,
                )
                await interaction.response.send_message(
                    "Không thể thực hiện nước đi này. Vui lòng thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            self.completed = True
            self.processing = False
            self.selected_indices.clear()
            self._disable_controls()
            self.cog.unregister_game(self)
            self.stop()
            try:
                await interaction.response.edit_message(
                    embed=self._showdown_embed(),
                    view=self,
                    allowed_mentions=NO_MENTIONS,
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Could not update completed poker session=%s",
                    self.session_id,
                )

    @discord.ui.button(
        label="Đổi bài",
        emoji="🔄",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def draw_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._play(interaction, draw=True)

    @discord.ui.button(
        label="Dằn bài",
        emoji="✋",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def stand_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._play(interaction, draw=False)

    async def on_timeout(self) -> None:
        async with self._action_lock:
            if self.completed:
                return
            refunded = self.refund_and_close("timeout")

        if self.message is None:
            return
        description = (
            f"Bàn đã hết thời gian. Đã hoàn **{self.bet:,} TC**."
            if refunded
            else "Bàn đã hết thời gian nhưng không thể tự động hoàn Trap Coin."
        )
        embed = discord.Embed(
            title="⌛ Bàn poker đã hết hạn",
            description=description,
            color=discord.Color.dark_grey(),
        )
        try:
            await self.message.edit(
                embed=embed,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not disable timed-out poker session=%s",
                self.session_id,
            )


class PokerCog(commands.Cog):
    """Run solo five-card draw tables backed by the shared Trap Coin bank."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bank = CardGameBank(bot.db)
        self.active_games: dict[int, PokerView] = {}

    def unregister_game(self, view: PokerView) -> None:
        current = self.active_games.get(view.author_id)
        if current is view:
            self.active_games.pop(view.author_id, None)

    def cog_unload(self) -> None:
        for view in tuple(self.active_games.values()):
            view.refund_and_close("cog_unload")
        self.active_games.clear()

    def _refund_setup_failure(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        bet: int,
        session_id: str,
    ) -> bool:
        try:
            self.bank.credit(
                user_id,
                guild_id,
                POKER_GAME_NAME,
                bet,
                session_id,
                "refund",
            )
        except PyMongoError:
            logger.exception(
                "Failed to refund poker setup session=%s user=%s",
                session_id,
                user_id,
            )
            return False
        return True

    @commands.command(
        name="poker",
        help="Chơi poker 5 lá với nhà cái bằng Trap Coin.",
    )
    async def poker(self, ctx: commands.Context, bet: int = DEFAULT_BET) -> None:
        wager_error = validate_wager(bet)
        if wager_error is not None:
            await ctx.send(wager_error, allowed_mentions=NO_MENTIONS)
            return

        if ctx.author.id in self.active_games:
            await ctx.send(
                "Bạn đã có một bàn poker đang chơi. Hãy kết thúc bàn đó trước.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        session_id = uuid.uuid4().hex
        guild_id = ctx.guild.id if ctx.guild is not None else None
        try:
            game = PokerGame(create_deck())
            balance = self.bank.reserve_wager(
                ctx.author.id,
                guild_id,
                POKER_GAME_NAME,
                bet,
                session_id,
            )
        except PyMongoError:
            logger.exception("Failed to reserve poker wager user=%s", ctx.author.id)
            await ctx.send(
                "Không thể truy cập số dư Trap Coin. Vui lòng thử lại.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        except (RuntimeError, ValueError):
            logger.exception("Failed to create poker game user=%s", ctx.author.id)
            await ctx.send(
                "Không thể tạo bàn poker lúc này. Vui lòng thử lại.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        if balance is None:
            await ctx.send(
                f"Bạn không có đủ Trap Coin để cược **{bet:,} TC**.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        try:
            view = PokerView(
                self,
                author_id=ctx.author.id,
                owner_name=ctx.author.display_name,
                guild_id=guild_id,
                bet=bet,
                session_id=session_id,
                game=game,
                balance_after_reserve=balance,
            )
        except Exception:
            logger.exception(
                "Failed to initialize poker table session=%s user=%s",
                session_id,
                ctx.author.id,
            )
            refunded = self._refund_setup_failure(
                user_id=ctx.author.id,
                guild_id=guild_id,
                bet=bet,
                session_id=session_id,
            )
            message = (
                "Không thể tạo bàn poker. Tiền cược đã được hoàn."
                if refunded
                else (
                    "Không thể tạo bàn poker và chưa thể tự động hoàn tiền. "
                    f"Hãy báo quản trị viên mã ván `{session_id}`."
                )
            )
            await ctx.send(message, allowed_mentions=NO_MENTIONS)
            return

        self.active_games[ctx.author.id] = view
        try:
            view.message = await ctx.send(
                embed=view.build_embed(),
                view=view,
                allowed_mentions=NO_MENTIONS,
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Failed to send poker table; refunding session=%s user=%s",
                session_id,
                ctx.author.id,
            )
            view.refund_and_close("send_failure")
        except Exception:
            logger.exception(
                "Unexpected failure sending poker table; refunding session=%s user=%s",
                session_id,
                ctx.author.id,
            )
            view.refund_and_close("send_failure")
            raise

    @poker.error
    async def poker_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send(
                f"Tiền cược phải là số nguyên từ **{MIN_BET:,}** "
                f"đến **{MAX_BET:,} TC**.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        logger.exception("Unexpected poker command error")
        await ctx.send(
            "Đã xảy ra lỗi khi chơi poker.",
            allowed_mentions=NO_MENTIONS,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PokerCog(bot))
