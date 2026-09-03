"""Recurring guild-scoped bedtime reminders."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Mapping

import discord
from discord.ext import commands, tasks
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.bedtime_remind._bedtime_helpers import (
    SleepWindow,
    active_sleep_window,
    as_utc,
    bedtime_date_key,
    format_clock_time,
    next_reminder_deadline,
    parse_clock_time,
    to_mongo_utc,
)


logger = logging.getLogger(__name__)

BEDTIME_REMINDERS_COLLECTION = "bedtime_reminders"
REMINDERS_PER_PAGE = 10
DUE_REMINDER_BATCH_SIZE = 100
RETRY_DELAY = timedelta(minutes=1)
MAX_MONGO_INT64 = 2**63 - 1

SCHEDULED_REMINDER_TEXT = (
    "🌙 {mention}, đến giờ đi ngủ rồi! Chúc bạn ngủ ngon nhé."
)
CHAT_REMINDER_TEXT = "🌙 {mention}, đến giờ đi ngủ rồi đó. Đi ngủ thôi!"

NO_MENTIONS = discord.AllowedMentions.none()


def _utcnow() -> datetime:
    return discord.utils.utcnow()


def _target_mentions(target: discord.abc.Snowflake) -> discord.AllowedMentions:
    """Allow only the member deliberately targeted by a reminder."""

    return discord.AllowedMentions(
        everyone=False,
        users=[target],
        roles=False,
        replied_user=False,
    )


def _valid_discord_id(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_MONGO_INT64
    )


def _valid_minute(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1440


class BedtimeReminderCog(commands.Cog):
    """Manage and deliver daily bedtime reminders in each guild."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.collection = self.db[BEDTIME_REMINDERS_COLLECTION]
        self.reminders_by_member: dict[tuple[int, int], dict[str, Any]] = {}
        self._member_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._ensure_indexes()
        self._load_reminders()
        self.reminder_loop.start()

    def cog_unload(self) -> None:
        self.reminder_loop.cancel()

    def _ensure_indexes(self) -> None:
        try:
            self.collection.create_index(
                [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
                name="guild_user_bedtime_unique",
            )
            self.collection.create_index(
                [("next_mention_at", ASCENDING)],
                name="bedtime_next_mention_due",
            )
        except PyMongoError as exc:
            logger.exception("Failed to create bedtime-reminder indexes")
            raise RuntimeError(
                "Could not enforce bedtime-reminder indexes"
            ) from exc

    @staticmethod
    def _validated_document(document: Mapping[str, Any]) -> dict[str, Any] | None:
        guild_id = document.get("guild_id")
        user_id = document.get("user_id")
        channel_id = document.get("channel_id")
        bedtime = document.get("bedtime_minutes")
        wake = document.get("wake_minutes")
        next_mention_at = document.get("next_mention_at")
        last_announced = document.get("last_announced_bedtime_date")

        if not all(
            _valid_discord_id(value)
            for value in (guild_id, user_id, channel_id)
        ):
            return None
        if not _valid_minute(bedtime) or not _valid_minute(wake) or bedtime == wake:
            return None
        if not isinstance(next_mention_at, datetime):
            return None
        if last_announced is not None:
            if not isinstance(last_announced, str):
                return None
            try:
                if date.fromisoformat(last_announced).isoformat() != last_announced:
                    return None
            except ValueError:
                return None

        normalized = dict(document)
        normalized["next_mention_at"] = to_mongo_utc(next_mention_at)
        return normalized

    def _load_reminders(self) -> None:
        try:
            documents = self.collection.find({})
            for raw_document in documents:
                document = self._validated_document(raw_document)
                if document is None:
                    logger.warning(
                        "Skipping malformed bedtime reminder id=%s",
                        raw_document.get("_id"),
                    )
                    continue
                key = (document["guild_id"], document["user_id"])
                self.reminders_by_member[key] = document
        except PyMongoError as exc:
            logger.exception("Failed to load bedtime reminders")
            raise RuntimeError(
                "Could not load the bedtime-reminder cache"
            ) from exc

    @staticmethod
    def _next_window_start(window: SleepWindow) -> datetime:
        return window.starts_at + timedelta(days=1)

    def _member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._member_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._member_locks[key] = lock
        return lock

    @staticmethod
    def _document_key(
        document: Mapping[str, Any],
    ) -> tuple[int, int] | None:
        guild_id = document.get("guild_id")
        user_id = document.get("user_id")
        if not _valid_discord_id(guild_id) or not _valid_discord_id(user_id):
            return None
        return int(guild_id), int(user_id)

    @staticmethod
    def _same_schedule_revision(
        first: Mapping[str, Any],
        second: Mapping[str, Any],
    ) -> bool:
        """Return whether two snapshots describe the same admin revision."""

        revision_fields = (
            "channel_id",
            "bedtime_minutes",
            "wake_minutes",
            "updated_at",
        )
        return all(first.get(field) == second.get(field) for field in revision_fields)

    def _cache_state(
        self,
        document: Mapping[str, Any],
        fields: Mapping[str, Any],
    ) -> None:
        key = (int(document["guild_id"]), int(document["user_id"]))
        cached = dict(self.reminders_by_member.get(key, document))
        cached.update(fields)
        self.reminders_by_member[key] = cached

    def _persist_scheduler_state(
        self,
        document: Mapping[str, Any],
        fields: Mapping[str, Any],
    ) -> bool:
        """Persist a scheduler transition, then mirror it into the cache."""

        query: dict[str, Any] = {
            "guild_id": int(document["guild_id"]),
            "user_id": int(document["user_id"]),
        }
        if "_id" in document:
            query = {"_id": document["_id"]}
        for field in (
            "channel_id",
            "bedtime_minutes",
            "wake_minutes",
            "updated_at",
        ):
            if field in document:
                query[field] = document[field]

        try:
            result = self.collection.update_one(query, {"$set": dict(fields)})
        except PyMongoError:
            logger.exception(
                "Failed to update bedtime-reminder state guild=%s user=%s",
                document.get("guild_id"),
                document.get("user_id"),
            )
            return False

        if getattr(result, "matched_count", 1) == 0:
            key = (int(document["guild_id"]), int(document["user_id"]))
            cached = self.reminders_by_member.get(key)
            if cached is not None and self._same_schedule_revision(
                document,
                cached,
            ):
                self.reminders_by_member.pop(key, None)
            return False

        self._cache_state(document, fields)
        return True

    def _quarantine_malformed_document(
        self,
        document: Mapping[str, Any],
        now: datetime,
    ) -> bool:
        """Remove a malformed row from the due queue without deleting it."""

        query: dict[str, Any]
        if "_id" in document:
            query = {"_id": document["_id"]}
        else:
            key = self._document_key(document)
            if key is None:
                logger.error(
                    "Cannot quarantine malformed bedtime reminder without an "
                    "_id or valid guild/user IDs"
                )
                return False
            query = {"guild_id": key[0], "user_id": key[1]}

        for field in ("next_mention_at", "updated_at"):
            if field in document:
                query[field] = document[field]

        try:
            result = self.collection.update_one(
                query,
                {
                    "$set": {
                        "invalid_at": to_mongo_utc(now),
                        "invalid_reason": "malformed bedtime reminder",
                    },
                    "$unset": {"next_mention_at": ""},
                },
            )
        except PyMongoError:
            logger.exception(
                "Failed to quarantine malformed bedtime reminder id=%s",
                document.get("_id"),
            )
            return False

        if getattr(result, "matched_count", 1) == 0:
            return False

        key = self._document_key(document)
        if key is not None:
            cached = self.reminders_by_member.get(key)
            if cached is None or self._same_schedule_revision(document, cached):
                self.reminders_by_member.pop(key, None)
        logger.error(
            "Quarantined malformed bedtime reminder id=%s",
            document.get("_id"),
        )
        return True

    def _advance_past_window(
        self,
        document: Mapping[str, Any],
        window: SleepWindow,
        *,
        announced: bool,
    ) -> bool:
        fields: dict[str, Any] = {
            "next_mention_at": to_mongo_utc(self._next_window_start(window)),
        }
        if announced:
            fields["last_announced_bedtime_date"] = bedtime_date_key(window)
            # Remember a successful Discord delivery before the database write.
            # If MongoDB is temporarily read-only, subsequent loop ticks must not
            # ping the member every minute while the same transition is retried.
            self._cache_state(document, fields)
        return self._persist_scheduler_state(document, fields)

    def _schedule_retry(
        self,
        document: Mapping[str, Any],
        window: SleepWindow,
        now: datetime,
    ) -> bool:
        retry_at = now + RETRY_DELAY
        if retry_at >= window.ends_at:
            retry_at = self._next_window_start(window)
        return self._persist_scheduler_state(
            document,
            {"next_mention_at": to_mongo_utc(retry_at)},
        )

    def _due_documents(
        self,
        now: datetime,
        excluded_ids: list[Any] | None = None,
    ) -> list[Mapping[str, Any]]:
        query = {"next_mention_at": {"$lte": to_mongo_utc(now)}}
        if excluded_ids:
            query["_id"] = {"$nin": list(excluded_ids)}
        cursor = self.collection.find(query)
        if isinstance(cursor, (list, tuple)):
            documents = list(cursor)
            documents.sort(
                key=lambda item: as_utc(item["next_mention_at"])
                if isinstance(item.get("next_mention_at"), datetime)
                else datetime.max.replace(tzinfo=now.tzinfo)
            )
            return documents[:DUE_REMINDER_BATCH_SIZE]
        return list(
            cursor.sort([("next_mention_at", ASCENDING)]).limit(
                DUE_REMINDER_BATCH_SIZE
            )
        )

    @staticmethod
    def _bot_member(guild: discord.Guild, bot: commands.Bot) -> discord.Member | None:
        member = guild.me
        if member is not None:
            return member
        if bot.user is None:
            return None
        return guild.get_member(bot.user.id)

    @classmethod
    def _can_send_to_channel(
        cls,
        guild: discord.Guild,
        channel: discord.TextChannel,
        bot: commands.Bot,
    ) -> bool:
        bot_member = cls._bot_member(guild, bot)
        if bot_member is None:
            return False
        permissions = channel.permissions_for(bot_member)
        return bool(permissions.view_channel and permissions.send_messages)

    async def _process_due_document(
        self,
        raw_document: Mapping[str, Any],
        now: datetime,
    ) -> bool:
        key = self._document_key(raw_document)
        if key is None:
            logger.warning(
                "Quarantining malformed due bedtime reminder id=%s",
                raw_document.get("_id"),
            )
            return self._quarantine_malformed_document(raw_document, now)

        async with self._member_lock(*key):
            cached = self.reminders_by_member.get(key)
            document = self._validated_document(raw_document)
            if document is None:
                if cached is not None and not self._same_schedule_revision(
                    raw_document,
                    cached,
                ):
                    return False
                logger.warning(
                    "Quarantining malformed due bedtime reminder id=%s",
                    raw_document.get("_id"),
                )
                return self._quarantine_malformed_document(raw_document, now)

            # Every valid persisted schedule is loaded into the cache. A missing
            # entry means an administrator removed it after this due batch was
            # queried; a differing revision means it was updated. Never deliver
            # or persist state derived from either stale snapshot.
            if cached is None or not self._same_schedule_revision(
                document,
                cached,
            ):
                return False

            return await self._process_due_document_locked(document, now)

    async def _process_due_document_locked(
        self,
        document: Mapping[str, Any],
        now: datetime,
    ) -> bool:
        """Process one validated snapshot while holding its member lock."""

        if as_utc(document["next_mention_at"]) > now:
            return False

        bedtime = int(document["bedtime_minutes"])
        wake = int(document["wake_minutes"])
        window = active_sleep_window(now, bedtime, wake)
        if window is None:
            next_deadline = next_reminder_deadline(now, bedtime, wake)
            return self._persist_scheduler_state(
                document,
                {"next_mention_at": to_mongo_utc(next_deadline)},
            )

        guild_id = int(document["guild_id"])
        user_id = int(document["user_id"])
        cached = self.reminders_by_member.get((guild_id, user_id))
        last_announced = document.get("last_announced_bedtime_date")
        if cached is not None:
            last_announced = cached.get(
                "last_announced_bedtime_date",
                last_announced,
            )
        if last_announced == bedtime_date_key(window):
            return self._advance_past_window(document, window, announced=True)

        channel_id = int(document["channel_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            logger.warning(
                "Skipping bedtime reminder for missing guild=%s user=%s",
                guild_id,
                user_id,
            )
            return self._advance_past_window(document, window, announced=False)

        member = guild.get_member(user_id)
        if member is None:
            logger.info(
                "Skipping bedtime reminder for absent member guild=%s user=%s",
                guild_id,
                user_id,
            )
            return self._advance_past_window(document, window, announced=False)

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Skipping bedtime reminder for missing text channel guild=%s "
                "user=%s channel=%s",
                guild_id,
                user_id,
                channel_id,
            )
            return self._advance_past_window(document, window, announced=False)

        if not self._can_send_to_channel(guild, channel, self.bot):
            logger.warning(
                "Skipping bedtime reminder without channel permission guild=%s "
                "user=%s channel=%s",
                guild_id,
                user_id,
                channel_id,
            )
            return self._advance_past_window(document, window, announced=False)

        try:
            await channel.send(
                SCHEDULED_REMINDER_TEXT.format(mention=f"<@{user_id}>"),
                allowed_mentions=_target_mentions(member),
            )
        except (discord.Forbidden, discord.NotFound):
            logger.warning(
                "Permanent Discord failure for bedtime reminder guild=%s "
                "user=%s channel=%s",
                guild_id,
                user_id,
                channel_id,
                exc_info=True,
            )
            return self._advance_past_window(document, window, announced=False)
        except discord.HTTPException:
            logger.warning(
                "Transient Discord failure for bedtime reminder guild=%s "
                "user=%s channel=%s",
                guild_id,
                user_id,
                channel_id,
                exc_info=True,
            )
            return self._schedule_retry(document, window, now)

        return self._advance_past_window(document, window, announced=True)

    async def _safely_process_due_document(
        self,
        document: Mapping[str, Any],
        now: datetime,
    ) -> bool:
        try:
            return await self._process_due_document(document, now)
        except Exception:
            logger.exception(
                "Unexpected bedtime-reminder failure guild=%s user=%s",
                document.get("guild_id"),
                document.get("user_id"),
            )
            return False

    async def process_due_reminders(self, now: datetime | None = None) -> int:
        """Drain due rows in bounded concurrent batches."""

        fixed_now = as_utc(now) if now is not None else None
        excluded_ids: list[Any] = []
        processed = 0
        while True:
            current = fixed_now or as_utc(_utcnow())
            try:
                documents = self._due_documents(current, excluded_ids)
            except PyMongoError:
                logger.exception("Failed to query due bedtime reminders")
                break
            if not documents:
                break

            results = await asyncio.gather(
                *(
                    self._safely_process_due_document(
                        document,
                        fixed_now or as_utc(_utcnow()),
                    )
                    for document in documents
                )
            )
            processed += sum(results)

            newly_excluded = 0
            for document in documents:
                document_id = document.get("_id")
                if document_id is not None and document_id not in excluded_ids:
                    excluded_ids.append(document_id)
                    newly_excluded += 1

            if len(documents) < DUE_REMINDER_BATCH_SIZE or newly_excluded == 0:
                break
        return processed

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        await self.process_due_reminders()

    @reminder_loop.before_loop
    async def before_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
        ):
            return

        document = self.reminders_by_member.get(
            (message.guild.id, message.author.id)
        )
        if document is None:
            return

        window = active_sleep_window(
            _utcnow(),
            int(document["bedtime_minutes"]),
            int(document["wake_minutes"]),
        )
        if window is None:
            return

        try:
            await message.reply(
                CHAT_REMINDER_TEXT.format(mention=f"<@{message.author.id}>"),
                mention_author=False,
                allowed_mentions=_target_mentions(message.author),
            )
        except discord.NotFound:
            logger.info(
                "Bedtime-reminder source disappeared guild=%s user=%s",
                message.guild.id,
                message.author.id,
            )
        except discord.Forbidden:
            logger.warning(
                "Missing permission for bedtime reply guild=%s user=%s",
                message.guild.id,
                message.author.id,
            )
        except discord.HTTPException:
            logger.exception(
                "Failed bedtime reply guild=%s user=%s",
                message.guild.id,
                message.author.id,
            )

    @commands.group(
        name="bedtime",
        invoke_without_command=True,
        help="Quản lý giờ đi ngủ hằng ngày của thành viên.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def bedtime(self, ctx: commands.Context) -> None:
        prefix = getattr(ctx, "clean_prefix", None) or ctx.prefix
        await ctx.send(
            "**Nhắc giờ đi ngủ (UTC+7)**\n"
            f"`{prefix}bedtime add @member <giờ_ngủ> <giờ_dậy> #channel`\n"
            f"`{prefix}bedtime remove <@member|user_id>`\n"
            f"`{prefix}bedtime list`\n"
            "Thời gian dùng định dạng `H:MM` hoặc `HH:MM`.",
            allowed_mentions=NO_MENTIONS,
        )

    @bedtime.command(name="add", help="Thêm hoặc cập nhật giờ ngủ của thành viên.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def bedtime_add(
        self,
        ctx: commands.Context,
        member: discord.Member,
        bedtime: str,
        wake: str,
        channel: discord.TextChannel,
    ) -> None:
        assert ctx.guild is not None
        if member.bot:
            await ctx.send("Không thể đặt giờ ngủ cho bot.")
            return
        if member.guild.id != ctx.guild.id:
            await ctx.send("Thành viên phải thuộc server này.")
            return
        if channel.guild.id != ctx.guild.id:
            await ctx.send("Kênh nhắc phải thuộc server này.")
            return
        if not self._can_send_to_channel(ctx.guild, channel, self.bot):
            await ctx.send(
                "Bot cần quyền View Channel và Send Messages trong kênh nhắc."
            )
            return

        try:
            bedtime_minutes = parse_clock_time(bedtime)
            wake_minutes = parse_clock_time(wake)
        except (TypeError, ValueError):
            await ctx.send(
                "Giờ không hợp lệ. Hãy dùng `H:MM` hoặc `HH:MM` theo đồng hồ 24 giờ."
            )
            return
        if bedtime_minutes == wake_minutes:
            await ctx.send("Giờ ngủ và giờ dậy phải khác nhau.")
            return

        key = (ctx.guild.id, member.id)
        async with self._member_lock(*key):
            existing = self.reminders_by_member.get(key)
            now = as_utc(_utcnow())
            window = active_sleep_window(now, bedtime_minutes, wake_minutes)
            same_bedtime = bool(
                existing is not None
                and existing.get("bedtime_minutes") == bedtime_minutes
            )
            # The dedupe key identifies a local bedtime occurrence. Changing
            # its channel or wake cutoff does not create a second occurrence,
            # so preserve delivery state whenever bedtime itself is unchanged.
            last_announced = (
                existing.get("last_announced_bedtime_date")
                if same_bedtime and existing is not None
                else None
            )
            if window is not None and last_announced == bedtime_date_key(window):
                next_mention_at = self._next_window_start(window)
            else:
                next_mention_at = next_reminder_deadline(
                    now,
                    bedtime_minutes,
                    wake_minutes,
                )

            updated_fields: dict[str, Any] = {
                "guild_id": ctx.guild.id,
                "user_id": member.id,
                "channel_id": channel.id,
                "bedtime_minutes": bedtime_minutes,
                "wake_minutes": wake_minutes,
                "next_mention_at": to_mongo_utc(next_mention_at),
                "last_announced_bedtime_date": last_announced,
                "updated_by": ctx.author.id,
                "updated_at": to_mongo_utc(now),
            }
            created_fields: dict[str, Any] = {
                "created_by": ctx.author.id,
                "created_at": to_mongo_utc(now),
            }

            try:
                self.collection.update_one(
                    {"guild_id": ctx.guild.id, "user_id": member.id},
                    {
                        "$set": updated_fields,
                        "$setOnInsert": created_fields,
                        "$unset": {"invalid_at": "", "invalid_reason": ""},
                    },
                    upsert=True,
                )
            except DuplicateKeyError:
                logger.exception(
                    "Duplicate bedtime reminder during upsert guild=%s user=%s",
                    ctx.guild.id,
                    member.id,
                )
                await ctx.send(
                    "Lịch ngủ vừa được thay đổi ở nơi khác. Hãy thử lại."
                )
                return
            except PyMongoError:
                logger.exception(
                    "Failed to save bedtime reminder guild=%s user=%s",
                    ctx.guild.id,
                    member.id,
                )
                await ctx.send("Không thể lưu lịch ngủ vào database lúc này.")
                return

            cached = dict(existing or created_fields)
            cached.update(updated_fields)
            cached.pop("invalid_at", None)
            cached.pop("invalid_reason", None)
            self.reminders_by_member[key] = cached

        action = "cập nhật" if existing is not None else "thêm"
        display_name = discord.utils.escape_markdown(member.display_name)
        await ctx.send(
            f"Đã {action} lịch ngủ cho **{display_name}** (`{member.id}`): "
            f"{format_clock_time(bedtime_minutes)}–"
            f"{format_clock_time(wake_minutes)} (UTC+7), nhắc tại "
            f"**#{discord.utils.escape_markdown(channel.name)}**.",
            allowed_mentions=NO_MENTIONS,
        )

    @bedtime.command(name="remove", help="Xóa lịch ngủ của thành viên.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def bedtime_remove(
        self,
        ctx: commands.Context,
        member: discord.Member | int,
    ) -> None:
        assert ctx.guild is not None
        user_id = member if isinstance(member, int) else member.id
        if not _valid_discord_id(user_id):
            await ctx.send("User ID phải là Discord ID hợp lệ.")
            return

        key = (ctx.guild.id, user_id)
        async with self._member_lock(*key):
            was_cached = key in self.reminders_by_member
            try:
                result = self.collection.delete_one(
                    {"guild_id": ctx.guild.id, "user_id": user_id}
                )
            except PyMongoError:
                logger.exception(
                    "Failed to remove bedtime reminder guild=%s user=%s",
                    ctx.guild.id,
                    user_id,
                )
                await ctx.send("Không thể xóa lịch ngủ khỏi database lúc này.")
                return

            deleted_count = getattr(result, "deleted_count", int(was_cached))
            if deleted_count == 0 and not was_cached:
                await ctx.send("Thành viên này chưa có lịch ngủ trong server.")
                return

            self.reminders_by_member.pop(key, None)
        current_member = (
            ctx.guild.get_member(user_id)
            if isinstance(member, int)
            else member
        )
        if current_member is None:
            await ctx.send(f"Đã xóa lịch ngủ của user ID `{user_id}`.")
            return

        display_name = discord.utils.escape_markdown(current_member.display_name)
        await ctx.send(
            f"Đã xóa lịch ngủ của **{display_name}** (`{user_id}`).",
            allowed_mentions=NO_MENTIONS,
        )

    @bedtime.command(name="list", help="Liệt kê lịch ngủ trong server.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def bedtime_list(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        reminders = sorted(
            (
                document
                for (guild_id, _), document in self.reminders_by_member.items()
                if guild_id == ctx.guild.id
            ),
            key=lambda document: int(document["user_id"]),
        )
        if not reminders:
            await ctx.send("Server chưa có lịch nhắc giờ đi ngủ nào.")
            return

        page_count = (len(reminders) + REMINDERS_PER_PAGE - 1) // REMINDERS_PER_PAGE
        for page_start in range(0, len(reminders), REMINDERS_PER_PAGE):
            page = reminders[page_start : page_start + REMINDERS_PER_PAGE]
            lines: list[str] = []
            for reminder in page:
                user_id = int(reminder["user_id"])
                channel_id = int(reminder["channel_id"])
                member = ctx.guild.get_member(user_id)
                channel = ctx.guild.get_channel(channel_id)
                member_name = (
                    discord.utils.escape_markdown(member.display_name)
                    if member is not None
                    else "Thành viên đã rời server"
                )
                channel_name = (
                    f"#{discord.utils.escape_markdown(channel.name)}"
                    if isinstance(channel, discord.TextChannel)
                    else "Kênh không còn tồn tại"
                )
                lines.append(
                    f"**{member_name}** (`{user_id}`) · "
                    f"{format_clock_time(int(reminder['bedtime_minutes']))}–"
                    f"{format_clock_time(int(reminder['wake_minutes']))} · "
                    f"{channel_name} (`{channel_id}`)"
                )

            page_number = page_start // REMINDERS_PER_PAGE + 1
            embed = discord.Embed(
                title=f"Lịch nhắc giờ ngủ · {page_number}/{page_count}",
                description="\n".join(lines),
                color=discord.Color.dark_purple(),
            )
            embed.set_footer(text="Múi giờ cố định: UTC+7")
            await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    async def _send_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> bool:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Administrator để quản lý lịch ngủ.")
            return True
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Lệnh bedtime chỉ dùng được trong server.")
            return True
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Thiếu tham số. Xem cú pháp bằng lệnh `bedtime`.")
            return True
        if isinstance(error, commands.MemberNotFound):
            await ctx.send("Không tìm thấy thành viên đó trong server.")
            return True
        if isinstance(error, commands.ChannelNotFound):
            await ctx.send("Không tìm thấy text channel đó trong server.")
            return True
        if isinstance(error, commands.BadUnionArgument):
            await ctx.send("Hãy mention thành viên hoặc nhập user ID hợp lệ.")
            return True
        if isinstance(error, commands.BadArgument):
            await ctx.send("Thành viên hoặc text channel không hợp lệ.")
            return True
        return False

    async def cog_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if not await self._send_command_error(ctx, error):
            raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BedtimeReminderCog(bot))
