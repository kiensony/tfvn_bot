"""Persistent multiplayer Crocodile Dentist for Discord."""

from __future__ import annotations

import asyncio
import logging
import random
import weakref
from datetime import datetime, timezone
from typing import Any, Mapping

import discord
from discord.ext import commands, tasks
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import PyMongoError

from cogs.minigames.crocodile_dentist._crocodile_helpers import (
    ACTIVE_TIMEOUT,
    INVITATION_TIMEOUT,
    MAX_TOOTH_COUNT,
    RESPONSE_ACCEPTED,
    RESPONSE_DECLINED,
    RESPONSE_PENDING,
    RESPONSE_TIMED_OUT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_FINISHED,
    STATUS_PENDING,
    GameStateError,
    apply_invitation_response,
    is_game_expired,
    parse_challenge_arguments,
    press_tooth,
    resolve_expired_game,
    validate_invitees,
)


logger = logging.getLogger(__name__)

CROCODILE_GAMES_COLLECTION = "crocodile_games"
COUNTERS_COLLECTION = "feature_counters"

CONFIRM_CUSTOM_ID = "tfvn:crocodile:confirm"
DECLINE_CUSTOM_ID = "tfvn:crocodile:decline"
TOOTH_CUSTOM_ID_PREFIX = "tfvn:crocodile:tooth:"

EXPIRY_BATCH_SIZE = 50
STATUS_LIST_LIMIT = 10
NO_MENTIONS = discord.AllowedMentions.none()
INVITEE_MENTIONS = discord.AllowedMentions(
    everyone=False,
    users=True,
    roles=False,
    replied_user=False,
)


def _utcnow() -> datetime:
    return discord.utils.utcnow()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _mentions(user_ids: list[int] | tuple[int, ...]) -> str:
    return ", ".join(f"<@{int(user_id)}>" for user_id in user_ids) or "Không có"


class CrocodileConfirmationView(discord.ui.View):
    """Persistent invitation buttons resolved through the panel message ID."""

    def __init__(self, cog: "CrocodileDentistCog", *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        if disabled:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(
        label="Tham gia",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id=CONFIRM_CUSTOM_ID,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.handle_invitation_response(interaction, accepted=True)

    @discord.ui.button(
        label="Từ chối",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id=DECLINE_CUSTOM_ID,
    )
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.handle_invitation_response(interaction, accepted=False)


class CrocodileToothButton(discord.ui.Button["CrocodileGameView"]):
    """One numbered tooth with a stable persistent component ID."""

    def __init__(
        self,
        tooth_number: int,
        *,
        pressed: bool = False,
        disabled: bool = False,
    ) -> None:
        self.tooth_number = int(tooth_number)
        super().__init__(
            label=str(self.tooth_number),
            emoji="🦷",
            style=(
                discord.ButtonStyle.secondary
                if pressed
                else discord.ButtonStyle.primary
            ),
            custom_id=f"{TOOTH_CUSTOM_ID_PREFIX}{self.tooth_number}",
            row=(self.tooth_number - 1) // 5,
            disabled=pressed or disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, CrocodileGameView):
            await view.cog.handle_tooth_press(interaction, self.tooth_number)


class CrocodileGameView(discord.ui.View):
    """Persistent tooth board; MongoDB remains authoritative for button state."""

    def __init__(
        self,
        cog: "CrocodileDentistCog",
        *,
        tooth_count: int,
        pressed_teeth: list[int] | tuple[int, ...] = (),
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        pressed = {int(value) for value in pressed_teeth}
        for tooth_number in range(1, int(tooth_count) + 1):
            self.add_item(
                CrocodileToothButton(
                    tooth_number,
                    pressed=tooth_number in pressed,
                    disabled=disabled,
                )
            )


class CrocodileDentistCog(commands.Cog):
    """Create and resume Mongo-backed Crocodile Dentist games."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.games = self.db[CROCODILE_GAMES_COLLECTION]
        self.counters = self.db[COUNTERS_COLLECTION]
        self._game_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._ensure_indexes()

        # Generic persistent dispatchers handle every canonical panel after restart.
        self.bot.add_view(CrocodileConfirmationView(self))
        self.bot.add_view(
            CrocodileGameView(self, tooth_count=MAX_TOOTH_COUNT)
        )
        self.expiry_loop.start()

    def cog_unload(self) -> None:
        self.expiry_loop.cancel()

    def _ensure_indexes(self) -> None:
        try:
            self.games.create_index(
                [("guild_id", ASCENDING), ("game_id", ASCENDING)],
                unique=True,
                name="guild_game_unique",
            )
            self.games.create_index(
                [("panel_message_id", ASCENDING)],
                unique=True,
                partialFilterExpression={
                    "panel_message_id": {"$type": "number"}
                },
                name="canonical_panel_unique",
            )
            self.games.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("participant_ids", ASCENDING),
                    ("status", ASCENDING),
                    ("updated_at", DESCENDING),
                ],
                name="guild_participant_open",
            )
            self.games.create_index(
                [("status", ASCENDING), ("invitation_expires_at", ASCENDING)],
                name="pending_expiry",
            )
            self.games.create_index(
                [("status", ASCENDING), ("activity_expires_at", ASCENDING)],
                name="active_expiry",
            )
        except PyMongoError:
            logger.exception("Failed to ensure Crocodile Dentist indexes")

    def _lock_for(self, game: Mapping[str, Any]) -> asyncio.Lock:
        key = str(game.get("_id", f"{game.get('guild_id')}:{game.get('game_id')}"))
        return self._game_locks.setdefault(key, asyncio.Lock())

    def _next_game_id(self, guild_id: int) -> int:
        counter = self.counters.find_one_and_update(
            {"_id": f"crocodile_game:{int(guild_id)}"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(counter["value"])

    def _cas_state(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        *,
        expected: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        updates = {key: value for key, value in after.items() if key != "_id"}
        query: dict[str, Any] = {
            "_id": before["_id"],
            "revision": int(before.get("revision", 0)),
            "status": before.get("status"),
        }
        if "panel_message_id" in before:
            query["panel_message_id"] = before.get("panel_message_id")
        if "panel_channel_id" in before:
            query["panel_channel_id"] = before.get("panel_channel_id")
        if expected:
            query.update(expected)
        return self.games.find_one_and_update(
            query,
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )

    def _responses(self, game: Mapping[str, Any]) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in (game.get("responses") or {}).items()
        }

    def _build_pending_embed(self, game: Mapping[str, Any]) -> discord.Embed:
        responses = self._responses(game)
        invitees = [int(value) for value in game.get("original_player_ids", [])[1:]]
        accepted = [
            user_id
            for user_id in invitees
            if responses.get(str(user_id)) == RESPONSE_ACCEPTED
        ]
        waiting = [
            user_id
            for user_id in invitees
            if responses.get(str(user_id)) == RESPONSE_PENDING
        ]
        declined = [
            user_id
            for user_id in invitees
            if responses.get(str(user_id)) == RESPONSE_DECLINED
        ]

        embed = discord.Embed(
            title=f"🐊 Cá sấu nha sĩ · Ván #{game['game_id']}",
            description=(
                f"<@{game['host_id']}> đang mời mọi người chơi với "
                f"**{game['tooth_count']} chiếc răng**.\n"
                "Mỗi người được mời chỉ có thể trả lời một lần."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="✅ Đã tham gia", value=_mentions(accepted), inline=False)
        embed.add_field(name="⏳ Chờ phản hồi", value=_mentions(waiting), inline=False)
        if declined:
            embed.add_field(name="❌ Đã từ chối", value=_mentions(declined), inline=False)
        expires_at = game.get("invitation_expires_at")
        if isinstance(expires_at, datetime):
            embed.add_field(
                name="Thời hạn",
                value=discord.utils.format_dt(_aware_utc(expires_at), style="R"),
                inline=False,
            )
        embed.set_footer(text="Chủ phòng được tính là đã xác nhận.")
        return embed

    def _build_active_embed(self, game: Mapping[str, Any]) -> discord.Embed:
        player_ids = [int(value) for value in game.get("player_ids", [])]
        current_player_id = game.get("current_player_id")
        order = []
        for position, user_id in enumerate(player_ids, start=1):
            marker = " 👈 đến lượt" if user_id == current_player_id else ""
            order.append(f"**{position}.** <@{user_id}>{marker}")

        pressed = [int(value) for value in game.get("pressed_teeth", [])]
        tooth_count = int(game.get("tooth_count", 0))
        embed = discord.Embed(
            title=f"🐊 Cá sấu nha sĩ · Ván #{game['game_id']}",
            description=(
                f"Đến lượt <@{current_player_id}> chọn **một chiếc răng**.\n"
                "Răng an toàn sẽ chuyển lượt; răng nguy hiểm kết thúc ván ngay."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="Thứ tự", value="\n".join(order), inline=False)
        embed.add_field(
            name="Hàm răng",
            value=f"Đã nhấn **{len(pressed)}/{tooth_count}** chiếc.",
            inline=True,
        )
        expires_at = game.get("activity_expires_at")
        if isinstance(expires_at, datetime):
            embed.add_field(
                name="Hết hạn nếu không chơi",
                value=discord.utils.format_dt(_aware_utc(expires_at), style="R"),
                inline=True,
            )
        embed.set_footer(text="Chỉ người đang đến lượt mới bấm được răng.")
        return embed

    def _build_terminal_embed(self, game: Mapping[str, Any]) -> discord.Embed:
        if game.get("status") == STATUS_FINISHED:
            result = game.get("result") or {}
            loser_id = result.get("loser_id")
            winner_ids = [int(value) for value in result.get("winner_ids", [])]
            dangerous_tooth = result.get("dangerous_tooth")
            embed = discord.Embed(
                title=f"💥 CÁ SẤU CẮN! · Ván #{game['game_id']}",
                description=(
                    f"<@{loser_id}> đã nhấn trúng răng nguy hiểm số "
                    f"**{dangerous_tooth}** và thua cuộc."
                ),
                color=discord.Color.red(),
            )
            embed.add_field(
                name="🏆 Người thắng",
                value=_mentions(winner_ids),
                inline=False,
            )
            return embed

        reasons = {
            "no_accepted_invitees": "Không còn đủ người tham gia sau thời hạn xác nhận.",
            "inactivity_timeout": "Ván đã bị hủy vì không có lượt hợp lệ trong 7 ngày.",
            "setup_send_failed": "Không thể tạo bảng mời ban đầu.",
            "setup_bind_failed": "Không thể liên kết bảng mời với dữ liệu đã lưu.",
        }
        return discord.Embed(
            title=f"⌛ Ván #{game['game_id']} đã bị hủy",
            description=reasons.get(
                str(game.get("cancel_reason")),
                "Ván chơi không còn hoạt động.",
            ),
            color=discord.Color.dark_grey(),
        )

    def build_embed(self, game: Mapping[str, Any]) -> discord.Embed:
        if game.get("status") == STATUS_PENDING:
            return self._build_pending_embed(game)
        if game.get("status") == STATUS_ACTIVE:
            return self._build_active_embed(game)
        return self._build_terminal_embed(game)

    def build_view(
        self,
        game: Mapping[str, Any],
        *,
        disabled: bool = False,
    ) -> discord.ui.View:
        status = game.get("status")
        if status == STATUS_PENDING or (
            status == STATUS_CANCELLED and game.get("started_at") is None
        ):
            return CrocodileConfirmationView(
                self,
                disabled=disabled or status != STATUS_PENDING,
            )
        return CrocodileGameView(
            self,
            tooth_count=int(game.get("tooth_count", 13)),
            pressed_teeth=[int(value) for value in game.get("pressed_teeth", [])],
            disabled=disabled or status != STATUS_ACTIVE,
        )

    async def _interaction_error(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        await interaction.response.send_message(
            message,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    async def _edit_interaction_state(
        self,
        interaction: discord.Interaction,
        game: Mapping[str, Any],
    ) -> bool:
        """Render committed state, with recovery guidance if Discord rejects it."""

        try:
            await interaction.response.edit_message(
                embed=self.build_embed(game),
                view=self.build_view(game),
                allowed_mentions=NO_MENTIONS,
            )
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Crocodile state committed but panel edit failed guild=%s game=%s",
                game.get("guild_id"),
                game.get("game_id"),
            )

        recovery = (
            "Trạng thái ván đã được lưu nhưng không cập nhật được bảng. "
            f"Chủ phòng hãy dùng `crocodile fire {game.get('game_id')}`."
        )
        try:
            is_done = getattr(interaction.response, "is_done", None)
            if callable(is_done) and is_done() and hasattr(interaction, "followup"):
                await interaction.followup.send(
                    recovery,
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            else:
                await interaction.response.send_message(
                    recovery,
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug(
                "Could not send Crocodile panel recovery notice game=%s",
                game.get("game_id"),
                exc_info=True,
            )
        return False

    def _game_for_panel(
        self,
        interaction: discord.Interaction,
    ) -> dict[str, Any] | None:
        if interaction.message is None or interaction.guild is None:
            return None
        channel_id = getattr(interaction, "channel_id", None)
        if channel_id is None and getattr(interaction, "channel", None) is not None:
            channel_id = interaction.channel.id
        query: dict[str, Any] = {
            "guild_id": int(interaction.guild.id),
            "panel_message_id": int(interaction.message.id),
        }
        if channel_id is not None:
            query["panel_channel_id"] = int(channel_id)
        return self.games.find_one(
            query
        )

    async def _fetch_panel_message(
        self,
        game: Mapping[str, Any],
    ) -> discord.Message | None:
        channel_id = game.get("panel_channel_id")
        message_id = game.get("panel_message_id")
        if not channel_id or not message_id:
            return None

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if not hasattr(channel, "fetch_message"):
            return None
        try:
            return await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden):
            return None
        except discord.HTTPException:
            logger.exception(
                "Failed to fetch Crocodile panel guild=%s game=%s message=%s",
                game.get("guild_id"),
                game.get("game_id"),
                message_id,
            )
            return None

    async def _edit_saved_panel(
        self,
        game: Mapping[str, Any],
        *,
        disabled: bool = False,
    ) -> None:
        message = await self._fetch_panel_message(game)
        if message is None:
            return
        try:
            await message.edit(
                embed=self.build_embed(game),
                view=self.build_view(game, disabled=disabled),
                allowed_mentions=NO_MENTIONS,
            )
        except (discord.NotFound, discord.Forbidden):
            return
        except discord.HTTPException:
            logger.exception(
                "Failed to edit Crocodile panel guild=%s game=%s",
                game.get("guild_id"),
                game.get("game_id"),
            )

    def _settle_expired_state(
        self,
        game: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        if not is_game_expired(game, now):
            return dict(game)
        updated = resolve_expired_game(game, now)
        if updated is None:
            return dict(game)
        persisted = self._cas_state(game, updated)
        if persisted is not None:
            return persisted
        fresh = self.games.find_one({"_id": game["_id"]})
        return fresh or dict(game)

    async def _settle_and_edit(
        self,
        game: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        async with self._lock_for(game):
            fresh = self.games.find_one({"_id": game["_id"]})
            if fresh is None:
                return dict(game)
            before_revision = int(fresh.get("revision", 0))
            settled = self._settle_expired_state(fresh, now)
        if int(settled.get("revision", 0)) != before_revision:
            await self._edit_saved_panel(settled)
        return settled

    async def expire_due_games(self, now: datetime | None = None) -> int:
        """Settle a bounded batch of invitation and inactivity deadlines."""

        current = now or _utcnow()
        queries = (
            {
                "status": STATUS_PENDING,
                "invitation_expires_at": {"$lte": current},
            },
            {
                "status": STATUS_ACTIVE,
                "activity_expires_at": {"$lte": current},
            },
        )
        settled_count = 0
        seen: set[Any] = set()
        for query in queries:
            for game in list(self.games.find(query).limit(EXPIRY_BATCH_SIZE)):
                game_key = game.get("_id")
                if game_key in seen:
                    continue
                seen.add(game_key)
                before_revision = int(game.get("revision", 0))
                settled = await self._settle_and_edit(game, current)
                if int(settled.get("revision", 0)) != before_revision:
                    settled_count += 1
        return settled_count

    @tasks.loop(minutes=1)
    async def expiry_loop(self) -> None:
        try:
            await self.expire_due_games()
        except PyMongoError:
            logger.exception("Crocodile Dentist expiry sweep failed")
        except Exception:
            logger.exception("Unexpected Crocodile Dentist expiry failure")

    @expiry_loop.before_loop
    async def before_expiry_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def handle_invitation_response(
        self,
        interaction: discord.Interaction,
        *,
        accepted: bool,
    ) -> None:
        try:
            game = self._game_for_panel(interaction)
        except PyMongoError:
            logger.exception("Failed to load Crocodile invitation")
            await self._interaction_error(
                interaction,
                "Không thể đọc dữ liệu ván chơi lúc này. Hãy thử lại sau.",
            )
            return
        if game is None:
            await self._interaction_error(
                interaction,
                "Bảng này đã cũ hoặc không còn là bảng chính của ván chơi.",
            )
            return

        async with self._lock_for(game):
            try:
                current = self._game_for_panel(interaction)
                if current is None:
                    await self._interaction_error(
                        interaction,
                        "Bảng này đã cũ. Chủ phòng hãy mở lại bảng mới.",
                    )
                    return

                now = _utcnow()
                if is_game_expired(current, now):
                    settled = self._settle_expired_state(current, now)
                    if settled.get("panel_message_id") != current.get(
                        "panel_message_id"
                    ):
                        await self._interaction_error(
                            interaction,
                            "Bảng này vừa được thay thế. Hãy dùng bảng mới.",
                        )
                        return
                    await self._edit_interaction_state(interaction, settled)
                    return

                try:
                    updated = apply_invitation_response(
                        current,
                        int(interaction.user.id),
                        accepted,
                        now,
                    )
                except GameStateError as exc:
                    await self._interaction_error(interaction, str(exc))
                    return

                persisted = self._cas_state(
                    current,
                    updated,
                    expected={
                        f"responses.{int(interaction.user.id)}": RESPONSE_PENDING,
                    },
                )
                if persisted is None:
                    await self._interaction_error(
                        interaction,
                        "Ván vừa thay đổi ở nơi khác. Hãy kiểm tra bảng mới nhất.",
                    )
                    return
            except PyMongoError:
                logger.exception(
                    "Failed invitation response guild=%s game=%s",
                    game.get("guild_id"),
                    game.get("game_id"),
                )
                await self._interaction_error(
                    interaction,
                    "Không thể lưu phản hồi lúc này. Hãy thử lại sau.",
                )
                return

        await self._edit_interaction_state(interaction, persisted)

    async def handle_tooth_press(
        self,
        interaction: discord.Interaction,
        tooth_number: int,
    ) -> None:
        try:
            game = self._game_for_panel(interaction)
        except PyMongoError:
            logger.exception("Failed to load Crocodile tooth panel")
            await self._interaction_error(
                interaction,
                "Không thể đọc dữ liệu ván chơi lúc này. Hãy thử lại sau.",
            )
            return
        if game is None:
            await self._interaction_error(
                interaction,
                "Bảng này đã cũ hoặc không còn là bảng chính của ván chơi.",
            )
            return

        async with self._lock_for(game):
            try:
                current = self._game_for_panel(interaction)
                if current is None:
                    await self._interaction_error(
                        interaction,
                        "Bảng này đã cũ. Chủ phòng hãy mở lại bảng mới.",
                    )
                    return

                now = _utcnow()
                if is_game_expired(current, now):
                    settled = self._settle_expired_state(current, now)
                    if settled.get("panel_message_id") != current.get(
                        "panel_message_id"
                    ):
                        await self._interaction_error(
                            interaction,
                            "Bảng này vừa được thay thế. Hãy dùng bảng mới.",
                        )
                        return
                    await self._edit_interaction_state(interaction, settled)
                    return

                try:
                    updated = press_tooth(
                        current,
                        int(interaction.user.id),
                        int(tooth_number),
                        now,
                    )
                except GameStateError as exc:
                    await self._interaction_error(interaction, str(exc))
                    return

                persisted = self._cas_state(
                    current,
                    updated,
                    expected={
                        "current_player_id": int(interaction.user.id),
                        "pressed_teeth": {"$ne": int(tooth_number)},
                    },
                )
                if persisted is None:
                    await self._interaction_error(
                        interaction,
                        "Chiếc răng hoặc lượt chơi vừa thay đổi. Hãy xem lại bảng.",
                    )
                    return
            except PyMongoError:
                logger.exception(
                    "Failed tooth press guild=%s game=%s tooth=%s",
                    game.get("guild_id"),
                    game.get("game_id"),
                    tooth_number,
                )
                await self._interaction_error(
                    interaction,
                    "Không thể lưu lượt chơi lúc này. Hãy thử lại sau.",
                )
                return

        await self._edit_interaction_state(interaction, persisted)

    async def _resolve_invitee_members(
        self,
        guild: discord.Guild,
        invitee_ids: tuple[int, ...],
    ) -> list[discord.Member]:
        members: list[discord.Member] = []
        for user_id in invitee_ids:
            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None
            if member is None:
                raise ValueError(
                    f"Không tìm thấy <@{user_id}> trong server này."
                )
            members.append(member)
        return members

    def _cancel_setup_game(self, game_id: Any, reason: str) -> bool:
        now = _utcnow()
        try:
            result = self.games.update_one(
                {
                    "_id": game_id,
                    "status": STATUS_PENDING,
                    "revision": 0,
                    "panel_message_id": None,
                },
                {
                    "$set": {
                        "status": STATUS_CANCELLED,
                        "cancel_reason": reason,
                        "completed_at": now,
                        "updated_at": now,
                        "current_turn": None,
                        "current_player_id": None,
                        "activity_expires_at": None,
                    },
                    "$inc": {"revision": 1},
                },
            )
        except PyMongoError:
            logger.exception("Failed to cancel Crocodile setup game=%s", game_id)
            return False
        return bool(getattr(result, "modified_count", 0))

    def _status_value(self, game: Mapping[str, Any]) -> str:
        if game.get("status") == STATUS_PENDING:
            responses = self._responses(game)
            invitees = [
                int(value) for value in game.get("original_player_ids", [])[1:]
            ]
            accepted = [
                user_id
                for user_id in invitees
                if responses.get(str(user_id)) == RESPONSE_ACCEPTED
            ]
            waiting = [
                user_id
                for user_id in invitees
                if responses.get(str(user_id)) == RESPONSE_PENDING
            ]
            deadline = game.get("invitation_expires_at")
            deadline_text = (
                discord.utils.format_dt(_aware_utc(deadline), style="R")
                if isinstance(deadline, datetime)
                else "không rõ"
            )
            return (
                f"Chủ phòng: <@{game['host_id']}>\n"
                f"Đã nhận lời: {_mentions(accepted)}\n"
                f"Đang chờ: {_mentions(waiting)}\n"
                f"Hết hạn: {deadline_text}"
            )

        player_ids = [int(value) for value in game.get("player_ids", [])]
        deadline = game.get("activity_expires_at")
        deadline_text = (
            discord.utils.format_dt(_aware_utc(deadline), style="R")
            if isinstance(deadline, datetime)
            else "không rõ"
        )
        return (
            f"Chủ phòng: <@{game['host_id']}>\n"
            f"Người chơi: {_mentions(player_ids)}\n"
            f"Đến lượt: <@{game.get('current_player_id')}>\n"
            f"Hết hạn: {deadline_text}"
        )

    @commands.group(
        name="crocodile",
        invoke_without_command=True,
        help="Xem và chơi Cá sấu nha sĩ nhiều người.",
    )
    @commands.guild_only()
    async def crocodile(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        query = {
            "guild_id": int(ctx.guild.id),
            "participant_ids": int(ctx.author.id),
            "status": {"$in": [STATUS_PENDING, STATUS_ACTIVE]},
        }
        try:
            initial = list(
                self.games.find(query)
                .sort("updated_at", DESCENDING)
                .limit(STATUS_LIST_LIMIT)
            )
            now = _utcnow()
            for game in initial:
                if is_game_expired(game, now):
                    await self._settle_and_edit(game, now)

            # Settling the first page can reveal another page of overdue
            # records after a long restart. Drain a bounded number of those
            # pages so the user never sees already-expired games as open.
            games: list[dict[str, Any]] = []
            for _ in range(5):
                candidates = list(
                    self.games.find(query)
                    .sort("updated_at", DESCENDING)
                    .limit(STATUS_LIST_LIMIT)
                )
                expired = [
                    game for game in candidates if is_game_expired(game, now)
                ]
                if not expired:
                    games = candidates
                    break
                for game in expired:
                    await self._settle_and_edit(game, now)
            else:
                games = [
                    game
                    for game in candidates
                    if not is_game_expired(game, now)
                ]
        except PyMongoError:
            logger.exception(
                "Failed Crocodile status query guild=%s user=%s",
                ctx.guild.id,
                ctx.author.id,
            )
            await ctx.reply(
                "Không thể đọc danh sách ván chơi lúc này. Hãy thử lại sau.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if not games:
            await ctx.reply(
                "Bạn không có ván Cá sấu nha sĩ nào đang chờ hoặc đang chơi.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        embed = discord.Embed(
            title="🐊 Các ván Cá sấu nha sĩ của bạn",
            description=(
                f"Hiển thị tối đa **{STATUS_LIST_LIMIT}** ván mới nhất. "
                "Chủ phòng có thể dùng `crocodile fire <game_id>` để mở lại bảng."
            ),
            color=discord.Color.green(),
        )
        for game in games:
            status = "Chờ xác nhận" if game["status"] == STATUS_PENDING else "Đang chơi"
            embed.add_field(
                name=f"Ván #{game['game_id']} · {status}",
                value=self._status_value(game),
                inline=False,
            )
        await ctx.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )

    @crocodile.command(
        name="challenge",
        help="Thách đấu 1–4 người chơi Cá sấu nha sĩ.",
        usage="crocodile challenge [số_răng] @user1 [@user2 @user3 @user4]",
    )
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def crocodile_challenge(
        self,
        ctx: commands.Context,
        *,
        arguments: str = "",
    ) -> None:
        assert ctx.guild is not None
        try:
            parsed = parse_challenge_arguments(arguments)
            invitees = await self._resolve_invitee_members(
                ctx.guild,
                parsed.invitee_ids,
            )
            invitee_ids = validate_invitees(int(ctx.author.id), invitees)
        except ValueError as exc:
            await ctx.reply(
                str(exc),
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        now = _utcnow()
        try:
            game_id = self._next_game_id(int(ctx.guild.id))
            all_players = [int(ctx.author.id), *invitee_ids]
            game: dict[str, Any] = {
                "guild_id": int(ctx.guild.id),
                "game_id": game_id,
                "status": STATUS_PENDING,
                "revision": 0,
                "host_id": int(ctx.author.id),
                "original_player_ids": all_players,
                "participant_ids": list(all_players),
                "player_ids": [int(ctx.author.id)],
                "responses": {
                    str(user_id): (
                        RESPONSE_ACCEPTED
                        if user_id == int(ctx.author.id)
                        else RESPONSE_PENDING
                    )
                    for user_id in all_players
                },
                "tooth_count": int(parsed.teeth_count),
                "dangerous_tooth": random.randint(1, int(parsed.teeth_count)),
                "pressed_teeth": [],
                "current_turn": None,
                "current_player_id": None,
                "result": None,
                "cancel_reason": None,
                "panel_channel_id": None,
                "panel_message_id": None,
                "created_at": now,
                "updated_at": now,
                "invitation_expires_at": now + INVITATION_TIMEOUT,
                "started_at": None,
                "last_activity_at": None,
                "activity_expires_at": None,
                "completed_at": None,
            }
            inserted = self.games.insert_one(game)
            game["_id"] = inserted.inserted_id
        except PyMongoError:
            logger.exception(
                "Failed to persist Crocodile challenge guild=%s host=%s",
                ctx.guild.id,
                ctx.author.id,
            )
            await ctx.reply(
                "Không thể tạo ván chơi trong cơ sở dữ liệu. Hãy thử lại sau.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        content = " ".join(f"<@{user_id}>" for user_id in invitee_ids)
        try:
            message = await ctx.send(
                content=content,
                embed=self.build_embed(game),
                view=self.build_view(game),
                allowed_mentions=INVITEE_MENTIONS,
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Failed to send Crocodile challenge guild=%s game=%s",
                ctx.guild.id,
                game_id,
            )
            self._cancel_setup_game(game["_id"], "setup_send_failed")
            try:
                await ctx.reply(
                    "Không thể gửi bảng mời. Ván đã được hủy; hãy thử lại sau.",
                    mention_author=False,
                    allowed_mentions=NO_MENTIONS,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        bind_write_ambiguous = False
        try:
            bound = self.games.find_one_and_update(
                {
                    "_id": game["_id"],
                    "status": STATUS_PENDING,
                    "revision": 0,
                    "panel_message_id": None,
                },
                {
                    "$set": {
                        "panel_channel_id": int(ctx.channel.id),
                        "panel_message_id": int(message.id),
                        "updated_at": _utcnow(),
                    },
                    "$inc": {"revision": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            bound = None
            bind_write_ambiguous = True
            logger.exception(
                "Failed to bind Crocodile challenge panel game=%s",
                game_id,
            )

        if bound is None:
            verification_failed = False
            try:
                fresh = self.games.find_one({"_id": game["_id"]})
            except PyMongoError:
                fresh = None
                verification_failed = True
                logger.exception(
                    "Failed to verify Crocodile challenge binding game=%s",
                    game_id,
                )

            if verification_failed and bind_write_ambiguous:
                # The bind may have committed before its acknowledgement was
                # lost. Keep the only possible canonical panel available; its
                # buttons remain harmless if the write did not commit.
                try:
                    await ctx.reply(
                        "Không thể xác nhận việc lưu bảng mời. "
                        "Hãy dùng `crocodile` để kiểm tra rồi `fire` lại nếu cần.",
                        mention_author=False,
                        allowed_mentions=NO_MENTIONS,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

            # The write may have succeeded before a network error, or `fire`
            # may already have installed another canonical panel. Never cancel
            # either authoritative state from this setup cleanup path.
            if fresh is not None and fresh.get("panel_message_id") == int(message.id):
                return
            has_other_authoritative_state = fresh is not None and (
                fresh.get("panel_message_id") is not None
                or int(fresh.get("revision", 0)) != 0
                or fresh.get("status") != STATUS_PENDING
            )
            if not has_other_authoritative_state:
                self._cancel_setup_game(game["_id"], "setup_bind_failed")
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            if not has_other_authoritative_state:
                try:
                    await ctx.reply(
                        "Không thể liên kết bảng mời với dữ liệu ván. Ván đã được hủy.",
                        mention_author=False,
                        allowed_mentions=NO_MENTIONS,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @crocodile.command(
        name="fire",
        help="Mở lại bảng của một ván Cá sấu nha sĩ còn hoạt động.",
        usage="crocodile fire <game_id>",
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def crocodile_fire(
        self,
        ctx: commands.Context,
        game_id: int,
    ) -> None:
        assert ctx.guild is not None
        try:
            game = self.games.find_one(
                {"guild_id": int(ctx.guild.id), "game_id": int(game_id)}
            )
        except PyMongoError:
            logger.exception(
                "Failed to load Crocodile game guild=%s game=%s",
                ctx.guild.id,
                game_id,
            )
            await ctx.reply(
                "Không thể đọc ván chơi lúc này. Hãy thử lại sau.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if game is None:
            await ctx.reply(
                "Không tìm thấy ván Cá sấu nha sĩ này trong server.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if int(game.get("host_id", 0)) != int(ctx.author.id):
            await ctx.reply(
                "Chỉ chủ phòng mới có thể mở lại bảng của ván này.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        try:
            if is_game_expired(game, _utcnow()):
                game = await self._settle_and_edit(game, _utcnow())
        except PyMongoError:
            logger.exception(
                "Failed to settle Crocodile game before fire game=%s",
                game_id,
            )
            await ctx.reply(
                "Không thể cập nhật thời hạn của ván. Hãy thử lại sau.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if game.get("status") not in {STATUS_PENDING, STATUS_ACTIVE}:
            await ctx.reply(
                "Ván này đã kết thúc hoặc bị hủy nên không thể mở lại bảng.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        previous: dict[str, Any] | None = None
        new_message: discord.Message | None = None
        persisted: dict[str, Any] | None = None
        ambiguous_panel_write = False
        async with self._lock_for(game):
            try:
                current = self.games.find_one({"_id": game["_id"]})
                if current is None:
                    await ctx.reply(
                        "Ván này không còn tồn tại.",
                        mention_author=False,
                        allowed_mentions=NO_MENTIONS,
                    )
                    return
                if is_game_expired(current, _utcnow()):
                    current = self._settle_expired_state(current, _utcnow())
                if current.get("status") not in {STATUS_PENDING, STATUS_ACTIVE}:
                    await ctx.reply(
                        "Ván này vừa hết hạn nên không thể mở lại bảng.",
                        mention_author=False,
                        allowed_mentions=NO_MENTIONS,
                    )
                    return

                previous = dict(current)
                new_message = await ctx.send(
                    embed=self.build_embed(current),
                    view=self.build_view(current),
                    allowed_mentions=NO_MENTIONS,
                )
                updated = dict(current)
                updated["panel_channel_id"] = int(ctx.channel.id)
                updated["panel_message_id"] = int(new_message.id)
                updated["updated_at"] = _utcnow()
                updated["revision"] = int(current.get("revision", 0)) + 1
                persisted = self._cas_state(current, updated)
            except PyMongoError:
                ambiguous_panel_write = new_message is not None
                logger.exception(
                    "Failed to replace Crocodile panel guild=%s game=%s",
                    ctx.guild.id,
                    game_id,
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Failed to send replacement Crocodile panel guild=%s game=%s",
                    ctx.guild.id,
                    game_id,
                )

        if persisted is None:
            canonical_was_verified = False
            verification_failed = False
            if ambiguous_panel_write and new_message is not None:
                try:
                    fresh = self.games.find_one({"_id": game["_id"]})
                except PyMongoError:
                    fresh = None
                    verification_failed = True
                    logger.exception(
                        "Failed to verify replacement Crocodile panel game=%s",
                        game_id,
                    )
                if (
                    fresh is not None
                    and fresh.get("panel_message_id") == int(new_message.id)
                ):
                    persisted = fresh
                    canonical_was_verified = True

            should_delete_new = new_message is not None and (
                not ambiguous_panel_write
                or (not canonical_was_verified and not verification_failed)
            )
            if should_delete_new and new_message is not None:
                try:
                    await new_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            if persisted is None:
                message = (
                    "Không thể xác nhận bảng nào đang là bảng chính. "
                    "Hãy dùng `crocodile` để kiểm tra rồi thử `fire` lại."
                    if verification_failed
                    else (
                        "Không thể mở bảng mới vì trạng thái ván vừa thay đổi. "
                        "Hãy thử lại."
                    )
                )
                await ctx.reply(
                    message,
                    mention_author=False,
                    allowed_mentions=NO_MENTIONS,
                )
                return

        if previous is not None and previous.get("panel_message_id"):
            await self._edit_saved_panel(previous, disabled=True)

    async def cog_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Bạn thao tác quá nhanh. Thử lại sau **{error.retry_after:.1f} giây**.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh Cá sấu nha sĩ chỉ dùng được trong server.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.reply(
                "Cú pháp: `crocodile fire <game_id>` hoặc "
                "`crocodile challenge [số_răng] @user...`.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CrocodileDentistCog(bot))
