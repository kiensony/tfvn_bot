"""Tiên Lộ: persistent AFK cultivation, equipment, and PvE."""

from __future__ import annotations

import hashlib
import logging
import random
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.cultivation import _cultivation_helpers as rules


logger = logging.getLogger(__name__)

ACCOUNTS_COLLECTION = "user_accounts"
EVENTS_COLLECTION = "cultivation_events"
TRANSACTIONS_COLLECTION = "transaction_logs"
DASHBOARD_TIMEOUT_SECONDS = 180
CAS_RETRY_LIMIT = 3


class CultivationUnavailable(RuntimeError):
    """Raised when account uniqueness cannot be guaranteed."""


class ProfileMissing(rules.RuleError):
    """Raised when a member has not entered Tiên Lộ yet."""


class MutationBusy(rules.RuleError):
    """Raised after repeated concurrent compare-and-swap conflicts."""


@dataclass
class StateChange:
    state: dict
    event_type: str
    summary: str
    balance_delta: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MutationResult:
    state: dict
    balance: int
    event_type: str
    summary: str
    metadata: Mapping[str, object]
    duplicate: bool = False


Mutation = Callable[[dict, int], StateChange]


def _utcnow() -> datetime:
    return discord.utils.utcnow()


def _naive_timestamp(value: datetime) -> int:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return int(aware.timestamp())


def _stable_roll(request_id: str, namespace: str, upper: int) -> int:
    digest = hashlib.sha256(f"{namespace}:{request_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % upper


class CultivationView(discord.ui.View):
    """Short-lived dashboard controlled only by the invoking member."""

    def __init__(self, cog: "CultivationCog", author_id: int) -> None:
        super().__init__(timeout=DASHBOARD_TIMEOUT_SECONDS)
        self.cog = cog
        self.author_id = author_id
        self.message: discord.Message | None = None
        self.add_item(CultivationFocusSelect(cog, author_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Chỉ đạo hữu đã mở Tiên Lộ mới dùng được bảng này.",
            ephemeral=True,
        )
        return False

    async def _run(
        self,
        interaction: discord.Interaction,
        action: str,
        value: str | None = None,
    ) -> None:
        await self.cog.handle_dashboard_action(interaction, self, action, value)

    @discord.ui.button(label="Thu công", emoji="🧘", style=discord.ButtonStyle.success, row=0)
    async def claim_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "claim")

    @discord.ui.button(label="Đột phá", emoji="⚡", style=discord.ButtonStyle.primary, row=0)
    async def breakthrough_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "breakthrough")

    @discord.ui.button(label="Nâng Động Phủ", emoji="🏚️", style=discord.ButtonStyle.secondary, row=0)
    async def cave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "cave")

    @discord.ui.button(label="Thí luyện", emoji="⚔️", style=discord.ButtonStyle.secondary, row=0)
    async def trial_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._run(interaction, "trial")

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class CultivationFocusSelect(discord.ui.Select):
    def __init__(self, cog: "CultivationCog", author_id: int) -> None:
        self.cog = cog
        self.author_id = author_id
        super().__init__(
            placeholder="Chọn hướng Bế Quan…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=rules.FOCUS_NAMES[key],
                    value=key,
                    description=description,
                    emoji=emoji,
                )
                for key, description, emoji in (
                    (rules.FOCUS_BALANCED, "100% Tu Vi · 100% Linh Thạch", "⚖️"),
                    (rules.FOCUS_QI, "125% Tu Vi · 60% Linh Thạch", "🧘"),
                    (rules.FOCUS_STONES, "75% Tu Vi · 150% Linh Thạch", "⛏️"),
                )
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, CultivationView):
            return
        await view._run(interaction, "focus", self.values[0])


class CultivationCog(commands.Cog):
    """Global cultivation profile stored atomically beside Trap Coin."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.accounts = self.db[ACCOUNTS_COLLECTION]
        self.events = self.db[EVENTS_COLLECTION]
        self.transactions = self.db[TRANSACTIONS_COLLECTION]
        self.enabled = True
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        try:
            self.accounts.create_index(
                [("user_id", ASCENDING)],
                unique=True,
                name="user_accounts_user_unique",
            )
        except PyMongoError:
            self.enabled = False
            duplicate_ids: list[int] = []
            try:
                duplicates = self.accounts.aggregate(
                    [
                        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
                        {"$match": {"count": {"$gt": 1}}},
                        {"$limit": 20},
                    ]
                )
                duplicate_ids = [int(row["_id"]) for row in duplicates if row.get("_id")]
            except PyMongoError:
                logger.exception("Could not inspect duplicate cultivation accounts")
            logger.exception(
                "Tiên Lộ disabled: user_accounts.user_id is not unique; duplicate ids=%s",
                duplicate_ids,
            )
            return

        try:
            self.events.create_index(
                [("request_id", ASCENDING)],
                unique=True,
                name="cultivation_request_unique",
            )
            self.events.create_index(
                [("user_id", ASCENDING), ("created_at", DESCENDING)],
                name="cultivation_user_events",
            )
            self.transactions.create_index(
                [("user_id", ASCENDING), ("timestamp", DESCENDING)],
                name="user_transactions_recent",
            )
        except PyMongoError:
            # Audit indexes are desirable, but the account document remains the
            # authoritative idempotency boundary.
            logger.exception("Failed to create one or more Tiên Lộ audit indexes")

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise CultivationUnavailable(
                "Tiên Lộ tạm tắt vì dữ liệu tài khoản bị trùng. "
                "Quản trị viên cần kiểm tra log."
            )

    @staticmethod
    def _request_id(source: object, action: str, user_id: int) -> str:
        source_id = getattr(source, "id", None)
        if source_id is None:
            source_id = uuid.uuid4().hex
        return f"{source.__class__.__name__.lower()}:{source_id}:{user_id}:{action}"[:120]

    def _find_account(self, user_id: int) -> dict | None:
        return self.accounts.find_one({"user_id": int(user_id)})

    def _profile_account(self, user_id: int, now: datetime) -> tuple[dict, dict]:
        self._require_enabled()
        account = self._find_account(user_id)
        if account is None or not isinstance(account.get("cultivation"), Mapping):
            raise ProfileMissing("Bạn chưa nhập Tiên Lộ. Dùng `tutien batdau` trước.")
        return account, rules.normalize_state(account["cultivation"], now)

    def create_profile(self, user_id: int, now: datetime, request_id: str) -> tuple[dict, bool]:
        self._require_enabled()
        self.accounts.update_one(
            {"user_id": int(user_id)},
            {"$setOnInsert": {"balance": 0}},
            upsert=True,
        )
        existing = self._find_account(user_id) or {}
        if isinstance(existing.get("cultivation"), Mapping):
            return rules.normalize_state(existing["cultivation"], now), False
        state = rules.start_meditation(rules.default_state(now), now)
        state["processed_requests"] = [request_id]
        cultivation_filter: object = (
            existing["cultivation"]
            if "cultivation" in existing
            else {"$exists": False}
        )
        result = self.accounts.update_one(
            {"user_id": int(user_id), "cultivation": cultivation_filter},
            {"$set": {"cultivation": state}},
        )
        created = bool(getattr(result, "modified_count", 0))
        account = self._find_account(user_id) or {}
        persisted = rules.normalize_state(account.get("cultivation", state), now)
        if created:
            self._write_event(
                user_id=user_id,
                request_id=request_id,
                event_type="profile_start",
                state=persisted,
                metadata={},
                created_at=now,
            )
        return persisted, created

    def _mutate(
        self,
        user_id: int,
        request_id: str,
        now: datetime,
        mutation: Mutation,
    ) -> MutationResult:
        self._require_enabled()
        for _ in range(CAS_RETRY_LIMIT):
            account, state = self._profile_account(user_id, now)
            balance = max(0, int(account.get("balance", 0)))
            request_was_processed = request_id in state.get("processed_requests", [])
            if not request_was_processed:
                try:
                    request_was_processed = self.events.find_one(
                        {"request_id": request_id}
                    ) is not None
                except PyMongoError:
                    # The bounded account history still protects all practical
                    # Discord retries if the audit collection is unavailable.
                    logger.exception(
                        "Failed to inspect Tiên Lộ request receipt %s", request_id
                    )
            if request_was_processed:
                return MutationResult(
                    state=state,
                    balance=balance,
                    event_type="duplicate",
                    summary="Yêu cầu này đã được xử lý.",
                    metadata={},
                    duplicate=True,
                )

            change = mutation(deepcopy(state), balance)
            new_state = change.state
            if change.balance_delta < 0 and balance < -change.balance_delta:
                raise rules.RuleError("Không đủ Trap Coin.")
            processed = list(state.get("processed_requests", []))
            processed.append(request_id)
            new_state["processed_requests"] = processed[-rules.MAX_PROCESSED_REQUESTS :]
            expected_version = int(state.get("version", 1))
            new_state["version"] = expected_version + 1
            new_state["schema_version"] = rules.SCHEMA_VERSION
            new_state["updated_at"] = rules.naive_utc(now)

            persisted_raw = account.get("cultivation") or {}
            persisted_version = persisted_raw.get("version")
            # Match malformed legacy values exactly once, then replace them
            # with the normalized integer revision.
            version_filter: object = (
                persisted_version
                if persisted_version is not None
                else {"$exists": False}
            )
            query: dict = {
                "user_id": int(user_id),
                "cultivation.version": version_filter,
            }
            if change.balance_delta < 0:
                query["balance"] = {"$gte": -change.balance_delta}
            update: dict = {"$set": {"cultivation": new_state}}
            if change.balance_delta:
                update["$inc"] = {"balance": change.balance_delta}
            updated = self.accounts.find_one_and_update(
                query,
                update,
                return_document=ReturnDocument.AFTER,
            )
            if updated is None:
                continue

            persisted = rules.normalize_state(updated.get("cultivation"), now)
            new_balance = max(0, int(updated.get("balance", balance + change.balance_delta)))
            self._write_event(
                user_id=user_id,
                request_id=request_id,
                event_type=change.event_type,
                state=persisted,
                metadata=change.metadata,
                created_at=now,
            )
            if change.balance_delta:
                self._write_tc_transaction(
                    user_id=user_id,
                    event_type=change.event_type,
                    amount=abs(change.balance_delta),
                    credit=change.balance_delta > 0,
                    balance_after=new_balance,
                    request_id=request_id,
                    created_at=now,
                )
            return MutationResult(
                state=persisted,
                balance=new_balance,
                event_type=change.event_type,
                summary=change.summary,
                metadata=change.metadata,
            )
        raise MutationBusy("Dữ liệu vừa thay đổi nhiều lần. Hãy thử lại.")

    def _write_event(
        self,
        *,
        user_id: int,
        request_id: str,
        event_type: str,
        state: Mapping[str, object],
        metadata: Mapping[str, object],
        created_at: datetime,
    ) -> None:
        try:
            self.events.insert_one(
                {
                    "request_id": request_id,
                    "user_id": int(user_id),
                    "event_type": event_type,
                    "stage_index": int(state.get("stage_index", 0)),
                    "qi": int(state.get("qi", 0)),
                    "spirit_stones": int(state.get("spirit_stones", 0)),
                    "metadata": dict(metadata),
                    "created_at": rules.naive_utc(created_at),
                }
            )
        except DuplicateKeyError:
            pass
        except PyMongoError:
            logger.exception("Failed to write Tiên Lộ event %s", request_id)

    def _write_tc_transaction(
        self,
        *,
        user_id: int,
        event_type: str,
        amount: int,
        credit: bool,
        balance_after: int,
        request_id: str,
        created_at: datetime,
    ) -> None:
        try:
            self.transactions.insert_one(
                {
                    "user_id": int(user_id),
                    "type": event_type,
                    "transaction_type": "credit" if credit else "debit",
                    "amount": int(amount),
                    "balance_after": int(balance_after),
                    "request_id": request_id,
                    "timestamp": rules.naive_utc(created_at),
                }
            )
        except PyMongoError:
            logger.exception("Failed to write Tiên Lộ TC transaction %s", request_id)

    @staticmethod
    def _settlement_rng(request_id: str) -> Callable[[int], int]:
        seeded = random.Random(
            int.from_bytes(hashlib.sha256(f"settle:{request_id}".encode()).digest()[:8], "big")
        )
        return seeded.randrange

    def claim(
        self,
        user_id: int,
        request_id: str,
        now: datetime,
        *,
        expedition_only: bool = False,
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            session = state.get("session")
            if expedition_only and (
                not isinstance(session, Mapping)
                or session.get("kind") != "expedition"
            ):
                raise rules.RuleError("Bạn không có Bí Cảnh để nhận thưởng.")
            settlement = rules.settle_state(
                state,
                now,
                rng=self._settlement_rng(request_id),
            )
            if settlement.settled_seconds == 0 and not settlement.expedition_completed:
                session = settlement.state.get("session")
                if isinstance(session, Mapping) and session.get("kind") == "expedition":
                    ends_at = session.get("ends_at")
                    if isinstance(ends_at, datetime):
                        raise rules.RuleError(
                            "Bí Cảnh chưa hoàn tất; trở lại "
                            f"<t:{_naive_timestamp(ends_at)}:R>."
                        )
                raise rules.RuleError("Cần Bế Quan ít nhất 10 phút trước khi Thu Công.")
            parts = []
            if settlement.qi_gained:
                parts.append(f"+{settlement.qi_gained:,} Tu Vi")
            if settlement.stones_gained:
                parts.append(f"+{settlement.stones_gained:,} Linh Thạch")
            if settlement.materials_gained:
                material_text = ", ".join(
                    f"{rules.MATERIAL_NAMES[key]} ×{amount}"
                    for key, amount in settlement.materials_gained.items()
                )
                parts.append(material_text)
            if settlement.dropped_item:
                parts.append(f"nhặt được {rules.ITEMS[settlement.dropped_item].name}")
            if settlement.duplicate_fragments:
                parts.append(f"trùng đồ → {settlement.duplicate_fragments} Mảnh Pháp Bảo")
            if not parts:
                parts.append("đã lưu phần tiến độ lẻ; tiếp tục Bế Quan")
            return StateChange(
                settlement.state,
                "cultivation_claim",
                "Thu Công: " + " · ".join(parts),
                metadata={
                    "qi_gained": settlement.qi_gained,
                    "stones_gained": settlement.stones_gained,
                    "expedition_completed": settlement.expedition_completed,
                    "dropped_item": settlement.dropped_item,
                    "capped_seconds": settlement.capped_seconds,
                },
            )

        return self._mutate(user_id, request_id, now, mutation)

    def set_focus(
        self,
        user_id: int,
        focus: str,
        request_id: str,
        now: datetime,
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            result = rules.change_focus(state, focus, now)
            return StateChange(
                result.state,
                "cultivation_focus",
                f"Đã chọn **{rules.FOCUS_NAMES[focus]}**.",
                metadata={"focus": focus},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def breakthrough(
        self, user_id: int, request_id: str, now: datetime
    ) -> MutationResult:
        roll = _stable_roll(request_id, "breakthrough", 100)

        def mutation(state: dict, balance: int) -> StateChange:
            settled = rules.settle_state(
                state, now, rng=self._settlement_rng(request_id), force=True
            )
            result = rules.attempt_breakthrough(settled.state, now, roll=roll)
            if result.success:
                assert result.new_stage is not None
                summary = f"⚡ Đột phá thành công: **{result.new_stage.name}**!"
                event_type = "cultivation_breakthrough"
            else:
                summary = (
                    f"🌩️ Đột phá thất bại ({result.roll + 1}/100; "
                    f"tỉ lệ {result.chance}%). Mất {result.fee_paid:,} Linh Thạch; "
                    "Tu Vi và trang bị được giữ nguyên."
                )
                event_type = "cultivation_breakthrough_failed"
            return StateChange(
                result.state,
                event_type,
                summary,
                metadata={
                    "success": result.success,
                    "chance": result.chance,
                    "roll": result.roll,
                    "fee": result.fee_paid,
                },
            )

        return self._mutate(user_id, request_id, now, mutation)

    def upgrade_cave(
        self, user_id: int, request_id: str, now: datetime
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            result = rules.upgrade_cave(state, now)
            return StateChange(
                result.state,
                "cultivation_cave_upgrade",
                f"🏚️ Động Phủ đã lên cấp **{result.state['cave_level']}**.",
                metadata={"cave_level": result.state["cave_level"]},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def choose_path(
        self,
        user_id: int,
        path: str,
        request_id: str,
        now: datetime,
    ) -> MutationResult:
        aliases = {
            "kiemtu": "kiem",
            "kiem": "kiem",
            "thetu": "the",
            "the": "the",
            "dantu": "dan",
            "dan": "dan",
        }
        normalized = aliases.get(path.strip().lower())
        if normalized is None:
            raise rules.RuleError("Phái phải là kiem, the hoặc dan.")

        def mutation(state: dict, balance: int) -> StateChange:
            chosen = rules.select_path(state, normalized)
            return StateChange(
                chosen,
                "cultivation_path",
                f"Đã chọn **{rules.PATH_NAMES[normalized]}**.",
                metadata={"path": normalized},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def reset_path(
        self, user_id: int, request_id: str, now: datetime
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            settled = rules.settle_state(
                state, now, rng=self._settlement_rng(request_id), force=True
            )
            reset = rules.reset_path(settled.state, now)
            return StateChange(
                reset,
                "cultivation_path_reset",
                "Đã tẩy tủy, hoàn lại điểm Thiên Phú và xóa phái.",
                metadata={},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def add_talent(
        self,
        user_id: int,
        talent_key: str,
        points: int,
        request_id: str,
        now: datetime,
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            settled = rules.settle_state(
                state, now, rng=self._settlement_rng(request_id), force=True
            )
            updated = rules.spend_talent(settled.state, talent_key, points)
            definition = rules.TALENTS[talent_key]
            rank = updated["talents"][talent_key]
            return StateChange(
                updated,
                "cultivation_talent",
                f"Đã tăng **{definition.name}** lên bậc **{rank}/{definition.max_rank}**.",
                metadata={"talent": talent_key, "points": points, "rank": rank},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def buy(
        self, user_id: int, item_key: str, request_id: str, now: datetime
    ) -> MutationResult:
        normalized = item_key.strip().lower()

        def mutation(state: dict, balance: int) -> StateChange:
            updated = rules.buy_item(state, normalized, now)
            item = rules.ITEMS[normalized]
            return StateChange(
                updated,
                "cultivation_purchase",
                f"Đã mua **{item.name}** với {item.price:,} Linh Thạch.",
                metadata={"item_id": normalized, "stone_cost": item.price},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def equip(
        self, user_id: int, item_key: str, request_id: str, now: datetime
    ) -> MutationResult:
        normalized = item_key.strip().lower()

        def mutation(state: dict, balance: int) -> StateChange:
            settled = rules.settle_state(
                state, now, rng=self._settlement_rng(request_id), force=True
            )
            updated = rules.equip_item(settled.state, normalized)
            item = rules.ITEMS[normalized]
            return StateChange(
                updated,
                "cultivation_equip",
                f"Đã trang bị **{item.name}** vào {rules.GEAR_SLOT_NAMES[item.slot]}.",
                metadata={"item_id": normalized, "slot": item.slot},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def salvage(
        self, user_id: int, item_key: str, request_id: str, now: datetime
    ) -> MutationResult:
        normalized = item_key.strip().lower()

        def mutation(state: dict, balance: int) -> StateChange:
            settled = rules.settle_state(
                state, now, rng=self._settlement_rng(request_id), force=True
            )
            item = rules.ITEMS.get(normalized)
            updated = rules.salvage_item(settled.state, normalized)
            assert item is not None
            return StateChange(
                updated,
                "cultivation_salvage",
                f"Đã phân rã **{item.name}** thành {item.salvage_fragments} Mảnh Pháp Bảo.",
                metadata={"item_id": normalized, "fragments": item.salvage_fragments},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def craft(
        self, user_id: int, recipe_key: str, request_id: str, now: datetime
    ) -> MutationResult:
        normalized = recipe_key.strip().lower()

        def mutation(state: dict, balance: int) -> StateChange:
            updated = rules.craft_item(state, normalized)
            recipe = rules.RECIPES[normalized]
            item = rules.ITEMS[recipe.result_item]
            return StateChange(
                updated,
                "cultivation_craft",
                f"🔨 Đã luyện thành **{item.name}**.",
                metadata={"recipe_id": normalized, "item_id": item.key},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def clear_trial(
        self,
        user_id: int,
        floor: int | None,
        request_id: str,
        now: datetime,
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            updated = rules.clear_tower_floor(state, floor)
            cleared = int(updated["tower_floor"])
            definition = rules.TOWER_FLOORS[cleared - 1]
            return StateChange(
                updated,
                "cultivation_trial",
                f"⚔️ Đã vượt tầng **{cleared}** và nhận {definition.stone_reward:,} Linh Thạch.",
                metadata={"floor": cleared, "boss": definition.boss},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def start_expedition(
        self,
        user_id: int,
        zone: str,
        hours: int,
        request_id: str,
        now: datetime,
    ) -> MutationResult:
        normalized = zone.strip().lower()

        def mutation(state: dict, balance: int) -> StateChange:
            result = rules.start_expedition(state, normalized, hours, now)
            session = result.state["session"]
            ends_at = session["ends_at"]
            return StateChange(
                result.state,
                "cultivation_expedition_start",
                f"🌌 Đã vào **{rules.EXPEDITION_ZONES[normalized].name}**; trở về <t:{_naive_timestamp(ends_at)}:R>.",
                metadata={"zone": normalized, "hours": hours, "ends_at": ends_at},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def cancel_expedition(
        self, user_id: int, request_id: str, now: datetime
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            updated = rules.cancel_expedition(state, now)
            return StateChange(
                updated,
                "cultivation_expedition_cancel",
                "Đã rời Bí Cảnh không nhận thưởng và trở lại Bế Quan.",
            )

        return self._mutate(user_id, request_id, now, mutation)

    def exchange_buy(
        self, user_id: int, tc_amount: int, request_id: str, now: datetime
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            updated, balance_delta = rules.exchange_buy_stones(state, tc_amount, now)
            if balance < -balance_delta:
                raise rules.RuleError("Không đủ Trap Coin.")
            received = tc_amount * rules.TC_TO_STONE_RATE
            return StateChange(
                updated,
                "cultivation_exchange_buy",
                f"Đã đổi {tc_amount:,} TC lấy {received:,} Linh Thạch.",
                balance_delta=balance_delta,
                metadata={"tc": tc_amount, "spirit_stones": received},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def exchange_sell(
        self, user_id: int, stone_amount: int, request_id: str, now: datetime
    ) -> MutationResult:
        def mutation(state: dict, balance: int) -> StateChange:
            updated, balance_delta = rules.exchange_sell_stones(state, stone_amount, now)
            return StateChange(
                updated,
                "cultivation_exchange_sell",
                f"Đã đổi {stone_amount:,} Linh Thạch lấy {balance_delta:,} TC.",
                balance_delta=balance_delta,
                metadata={"tc": balance_delta, "spirit_stones": stone_amount},
            )

        return self._mutate(user_id, request_id, now, mutation)

    def set_privacy(
        self,
        user_id: int,
        visibility: str,
        request_id: str,
        now: datetime,
    ) -> MutationResult:
        normalized = visibility.strip().lower()
        if normalized not in {"public", "private"}:
            raise rules.RuleError("Chế độ phải là public hoặc private.")

        def mutation(state: dict, balance: int) -> StateChange:
            state["profile_public"] = normalized == "public"
            label = "công khai" if normalized == "public" else "riêng tư"
            return StateChange(
                state,
                "cultivation_privacy",
                f"Hồ sơ Tiên Lộ đã chuyển sang **{label}**.",
                metadata={"visibility": normalized},
            )

        return self._mutate(user_id, request_id, now, mutation)

    @staticmethod
    def _session_text(state: Mapping[str, object], now: datetime) -> str:
        session = state.get("session")
        if not isinstance(session, Mapping):
            return "Chưa Bế Quan"
        if session.get("kind") == "expedition":
            zone = rules.EXPEDITION_ZONES.get(str(session.get("zone")))
            ends_at = session.get("ends_at")
            if isinstance(ends_at, datetime):
                status = "sẵn sàng Thu Công" if rules.naive_utc(now) >= ends_at else f"về <t:{_naive_timestamp(ends_at)}:R>"
            else:
                status = "không rõ thời gian"
            return f"Bí Cảnh · {zone.name if zone else '?'} · {status}"
        settled_at = session.get("settled_at")
        if not isinstance(settled_at, datetime):
            return "Bế Quan"
        elapsed = max(0, int((rules.naive_utc(now) - settled_at).total_seconds()))
        stored = min(elapsed, rules.storage_cap_hours(state) * rules.SECONDS_PER_HOUR)
        hours, remainder = divmod(stored, rules.SECONDS_PER_HOUR)
        minutes = remainder // 60
        return (
            f"Bế Quan · {hours}h {minutes:02d}m / "
            f"{rules.storage_cap_hours(state)}h · {rules.FOCUS_NAMES[str(state['focus'])]}"
        )

    def profile_embed(
        self,
        member: discord.abc.User,
        state: Mapping[str, object],
        now: datetime,
        *,
        owner_view: bool,
    ) -> discord.Embed:
        normalized = rules.normalize_state(state, now)
        stage = rules.stage_for(normalized)
        next_stage = rules.next_stage_for(normalized)
        cost = stage.qi_cost
        embed = discord.Embed(
            title=f"☯️ Tiên Lộ của {member.display_name}",
            description=(
                f"**Cảnh giới:** {stage.name}\n"
                f"**Tu Vi:** {normalized['qi']:,}"
                + (f" / {cost:,}\n{rules.progress_bar(normalized['qi'], cost)}" if cost else " · đã đạt giới hạn phiên bản")
            ),
            color=0x8E44AD,
        )
        path = normalized.get("path")
        qi_rate, stone_rate = rules.production_rates(normalized)
        resource_lines = [
            f"Linh Thạch: **{normalized['spirit_stones']:,}**",
            f"Điểm Thiên Phú: **{normalized['talent_points']}**",
            f"Động Phủ: **{normalized['cave_level']}/{rules.MAX_CAVE_LEVEL}**",
            f"Sản lượng: **~{qi_rate} Tu Vi/h · ~{stone_rate} LT/h**",
        ]
        embed.add_field(name="🪨 Tài nguyên", value="\n".join(resource_lines), inline=False)
        embed.add_field(
            name="🧘 Phiên hiện tại",
            value=self._session_text(normalized, now),
            inline=False,
        )
        if owner_view:
            session = normalized.get("session")
            if isinstance(session, Mapping) and session.get("kind") == "meditation":
                preview = rules.settle_state(normalized, now, rng=lambda upper: upper - 1)
                if preview.qi_gained or preview.stones_gained:
                    embed.add_field(
                        name="📥 Đang chờ Thu Công",
                        value=f"+{preview.qi_gained:,} Tu Vi · +{preview.stones_gained:,} Linh Thạch",
                        inline=False,
                    )
        gear_lines = []
        for slot, label in rules.GEAR_SLOT_NAMES.items():
            item_key = normalized["equipped"].get(slot)
            gear_lines.append(f"{label}: **{rules.ITEMS[item_key].name if item_key else '—'}**")
        embed.add_field(name="🎒 Trang bị", value="\n".join(gear_lines), inline=False)
        chance = rules.breakthrough_chance(normalized)
        requirement = "Đã tối đa"
        if next_stage is not None and cost is not None:
            requirement = f"{next_stage.name} · {cost:,} Tu Vi"
            if stage.stone_cost:
                requirement += f" · {stage.stone_cost:,} LT · {chance}%"
            trial = rules.required_trial_floor(normalized)
            if trial:
                requirement += f" · Tháp {trial}"
        embed.add_field(
            name="⚡ Mục tiêu",
            value=(
                f"{requirement}\nPhái: **{rules.PATH_NAMES.get(path, 'Chưa chọn')}** · "
                f"Lực chiến: **{rules.combat_power(normalized):,}** · "
                f"Tháp: **{normalized['tower_floor']}/30**"
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                "Hồ sơ công khai" if normalized["profile_public"] else "Hồ sơ riêng tư"
            )
            + " · Tu Vi và trang bị không mất khi đột phá thất bại"
        )
        return embed

    def market_embed(self, state: Mapping[str, object], now: datetime) -> discord.Embed:
        embed = discord.Embed(
            title="🏪 Chợ Đen Tiên Lộ",
            description=(
                "📌 Bốn món cơ bản luôn có sẵn; 🔄 bốn món còn lại luân phiên "
                "theo ngày ICT. Không có reroll trả phí."
            ),
            color=discord.Color.dark_purple(),
        )
        owned = set(state.get("owned_items", []))
        for item in rules.daily_market(now):
            stats = []
            if item.power:
                stats.append(f"+{item.power} lực chiến")
            if item.qi_bonus_bp:
                stats.append(f"+{item.qi_bonus_bp // 100}% Tu Vi")
            if item.stone_bonus_bp:
                stats.append(f"+{item.stone_bonus_bp // 100}% LT")
            if item.storage_hours:
                stats.append(f"+{item.storage_hours}h trữ")
            stock_icon = "📌" if item.permanent_market else "🔄"
            embed.add_field(
                name=f"{stock_icon} {item.name} · {item.price:,} LT",
                value=(
                    f"ID: `{item.key}` · {rules.GEAR_SLOT_NAMES[item.slot]}\n"
                    + " · ".join(stats or ["Trang bị cơ bản"])
                    + ("\n✅ Đã sở hữu" if item.key in owned else "")
                ),
                inline=False,
            )
        return embed

    def inventory_embed(self, member: discord.abc.User, state: Mapping[str, object]) -> discord.Embed:
        owned = [rules.ITEMS[key].name + f" (`{key}`)" for key in state.get("owned_items", []) if key in rules.ITEMS]
        materials = [
            f"{name}: **{int(state.get('materials', {}).get(key, 0)):,}**"
            for key, name in rules.MATERIAL_NAMES.items()
        ]
        embed = discord.Embed(
            title=f"🎒 Kho Tiên Lộ của {member.display_name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Trang bị sở hữu",
            value="\n".join(owned) if owned else "Chưa có trang bị.",
            inline=False,
        )
        embed.add_field(name="Nguyên liệu", value="\n".join(materials), inline=False)
        return embed

    def crafting_embed(self, state: Mapping[str, object]) -> discord.Embed:
        embed = discord.Embed(
            title="🔨 Công thức Luyện Khí",
            description="Chế tạo luôn thành công; Đan Tu có thể giảm chi phí.",
            color=discord.Color.orange(),
        )
        for recipe in rules.RECIPES.values():
            item = rules.ITEMS[recipe.result_item]
            materials = " · ".join(
                f"{rules.MATERIAL_NAMES[key]} ×{amount}"
                for key, amount in recipe.materials.items()
            )
            owned = "\n✅ Đã sở hữu" if item.key in state.get("owned_items", []) else ""
            embed.add_field(
                name=item.name,
                value=(
                    f"ID: `{recipe.key}` · {recipe.stone_cost:,} LT\n"
                    f"{materials}{owned}"
                ),
                inline=False,
            )
        return embed

    def talent_embed(self, member: discord.abc.User, state: Mapping[str, object]) -> discord.Embed:
        path = state.get("path")
        embed = discord.Embed(
            title=f"🌿 Thiên Phú của {member.display_name}",
            description=(
                f"Phái: **{rules.PATH_NAMES.get(path, 'Chưa chọn')}** · "
                f"Còn **{int(state.get('talent_points', 0))}** điểm"
            ),
            color=discord.Color.green(),
        )
        if not path:
            embed.add_field(
                name="Chưa chọn phái",
                value="Dùng `tutien phai <kiem|the|dan>` sau khi đạt Luyện Khí 1.",
                inline=False,
            )
            return embed
        for definition in rules.TALENTS.values():
            if definition.path != path:
                continue
            rank = int(state.get("talents", {}).get(definition.key, 0))
            embed.add_field(
                name=f"{definition.name} · {rank}/{definition.max_rank}",
                value=f"`{definition.key}` · {definition.description}",
                inline=False,
            )
        return embed

    def expedition_embed(self, state: Mapping[str, object], now: datetime) -> discord.Embed:
        normalized = rules.normalize_state(state, now)
        embed = discord.Embed(
            title="🌌 Bí Cảnh",
            description=self._session_text(normalized, now),
            color=discord.Color.teal(),
        )
        for zone in rules.EXPEDITION_ZONES.values():
            rewards = ", ".join(
                f"{rules.MATERIAL_NAMES[key]} ×{amount}/2h"
                for key, amount in zone.materials_per_two_hours.items()
            )
            embed.add_field(
                name=zone.name,
                value=f"ID: `{zone.key}` · {zone.base_stones_per_two_hours} LT/2h · {rewards}",
                inline=False,
            )
        next_gear_run = int(normalized["boss_pity"]) + 1
        embed.set_footer(
            text=(
                "Thời lượng: 2, 4 hoặc 8 giờ · "
                f"Pity Yêu Thú Sơn: lượt gear kế tiếp {next_gear_run}/10"
            )
        )
        return embed

    def exchange_embed(self, state: Mapping[str, object], balance: int, now: datetime) -> discord.Embed:
        normalized = rules.reset_exchange_week(rules.normalize_state(state, now), now)
        return discord.Embed(
            title="🔄 Đổi Trap Coin ⇄ Linh Thạch",
            description=(
                f"Số dư: **{balance:,} TC** · **{normalized['spirit_stones']:,} Linh Thạch**\n\n"
                f"Mua: `tutien doido mua <TC>` · 1 TC = 10 LT\n"
                f"Đã dùng: **{normalized['tc_spent_week']}/{rules.TC_BUY_WEEKLY_CAP} TC/tuần**\n\n"
                f"Bán: `tutien doido ban <Linh Thạch>` · 20 LT = 1 TC\n"
                f"Đã nhận: **{normalized['tc_earned_week']}/{rules.TC_SELL_WEEKLY_CAP} TC/tuần**"
            ),
            color=discord.Color.gold(),
        )

    async def _reply(
        self,
        ctx: commands.Context,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
    ) -> discord.Message:
        return await ctx.reply(
            content,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=True,
        )

    async def _send_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, (rules.RuleError, CultivationUnavailable)):
            await self._reply(ctx, f"❌ {error}")
            return
        logger.exception("Unexpected Tiên Lộ command error")
        await self._reply(ctx, "❌ Không thể xử lý Tiên Lộ lúc này.")

    async def _send_mutation(
        self,
        ctx: commands.Context,
        action: str,
        callback: Callable[[str, datetime], MutationResult],
    ) -> None:
        request_id = self._request_id(ctx.message, action, ctx.author.id)
        try:
            result = callback(request_id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        await self._reply(ctx, result.summary)

    async def _open_dashboard(self, ctx: commands.Context) -> None:
        try:
            account, state = self._profile_account(ctx.author.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        view = CultivationView(self, ctx.author.id)
        view.message = await self._reply(
            ctx,
            embed=self.profile_embed(ctx.author, state, _utcnow(), owner_view=True),
            view=view,
        )

    async def handle_dashboard_action(
        self,
        interaction: discord.Interaction,
        view: CultivationView,
        action: str,
        value: str | None,
    ) -> None:
        request_id = self._request_id(interaction, action, interaction.user.id)
        now = _utcnow()
        try:
            if action == "claim":
                result = self.claim(interaction.user.id, request_id, now)
            elif action == "breakthrough":
                result = self.breakthrough(interaction.user.id, request_id, now)
            elif action == "cave":
                result = self.upgrade_cave(interaction.user.id, request_id, now)
            elif action == "trial":
                result = self.clear_trial(interaction.user.id, None, request_id, now)
            elif action == "focus" and value is not None:
                result = self.set_focus(interaction.user.id, value, request_id, now)
            else:
                raise rules.RuleError("Hành động bảng Tiên Lộ không hợp lệ.")
        except (rules.RuleError, CultivationUnavailable) as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        except Exception:
            logger.exception("Unexpected Tiên Lộ dashboard error")
            await interaction.response.send_message(
                "❌ Không thể xử lý Tiên Lộ lúc này.",
                ephemeral=True,
            )
            return
        embed = self.profile_embed(interaction.user, result.state, now, owner_view=True)
        embed.insert_field_at(0, name="Kết quả", value=result.summary, inline=False)
        await interaction.response.edit_message(embed=embed, view=view)

    @commands.group(
        name="tutien",
        aliases=["cultivate"],
        invoke_without_command=True,
        help="Mở bảng Tiên Lộ.",
    )
    async def tutien(self, ctx: commands.Context) -> None:
        await self._open_dashboard(ctx)

    @tutien.command(name="batdau", help="Khởi tạo hồ sơ Tiên Lộ.")
    @commands.cooldown(2, 5, commands.BucketType.user)
    async def cultivation_start(self, ctx: commands.Context) -> None:
        now = _utcnow()
        request_id = self._request_id(ctx.message, "start", ctx.author.id)
        try:
            state, created = self.create_profile(ctx.author.id, now, request_id)
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        if not created:
            await self._reply(ctx, "Bạn đã nhập Tiên Lộ. Dùng `tutien` để mở bảng.")
            return
        view = CultivationView(self, ctx.author.id)
        embed = self.profile_embed(ctx.author, state, now, owner_view=True)
        embed.insert_field_at(
            0,
            name="🌸 Nhập môn",
            value="Bạn đã bắt đầu Bế Quan theo hướng Cân Bằng.",
            inline=False,
        )
        view.message = await self._reply(ctx, embed=embed, view=view)

    @tutien.command(name="thucong", help="Nhận tài nguyên Bế Quan/Bí Cảnh.")
    @commands.cooldown(2, 5, commands.BucketType.user)
    async def cultivation_claim(self, ctx: commands.Context) -> None:
        await self._send_mutation(
            ctx,
            "claim",
            lambda request_id, now: self.claim(ctx.author.id, request_id, now),
        )

    @tutien.command(name="huong", help="Chọn hướng Bế Quan.")
    async def cultivation_focus(self, ctx: commands.Context, focus: str) -> None:
        normalized = focus.strip().lower().replace("_", "")
        await self._send_mutation(
            ctx,
            f"focus:{normalized}",
            lambda request_id, now: self.set_focus(
                ctx.author.id, normalized, request_id, now
            ),
        )

    @tutien.command(name="dotpha", help="Thử đột phá cảnh giới.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def cultivation_breakthrough(self, ctx: commands.Context) -> None:
        await self._send_mutation(
            ctx,
            "breakthrough",
            lambda request_id, now: self.breakthrough(
                ctx.author.id, request_id, now
            ),
        )

    @tutien.group(
        name="phai",
        invoke_without_command=True,
        help="Xem hoặc chọn phái tu luyện.",
    )
    async def cultivation_path(
        self, ctx: commands.Context, path: str | None = None
    ) -> None:
        if path is None:
            try:
                _, state = self._profile_account(ctx.author.id, _utcnow())
            except Exception as exc:
                await self._send_error(ctx, exc)
                return
            chosen = rules.PATH_NAMES.get(state.get("path"), "Chưa chọn")
            await self._reply(
                ctx,
                f"Phái hiện tại: **{chosen}**. Chọn: `tutien phai <kiem|the|dan>`. "
                "Mỗi phái có ba Thiên Phú, tối đa năm bậc.",
            )
            return
        await self._send_mutation(
            ctx,
            f"path:{path}",
            lambda request_id, now: self.choose_path(
                ctx.author.id, path, request_id, now
            ),
        )

    @cultivation_path.command(name="reset", help="Tẩy tủy và hoàn điểm Thiên Phú.")
    async def cultivation_path_reset(self, ctx: commands.Context) -> None:
        await self._send_mutation(
            ctx,
            "path-reset",
            lambda request_id, now: self.reset_path(ctx.author.id, request_id, now),
        )

    @tutien.group(
        name="thienphu",
        invoke_without_command=True,
        help="Xem cây Thiên Phú.",
    )
    async def cultivation_talents(self, ctx: commands.Context) -> None:
        try:
            _, state = self._profile_account(ctx.author.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        await self._reply(ctx, embed=self.talent_embed(ctx.author, state))

    @cultivation_talents.command(name="tang", help="Cộng điểm Thiên Phú.")
    async def cultivation_talent_add(
        self,
        ctx: commands.Context,
        talent_id: str,
        points: int = 1,
    ) -> None:
        await self._send_mutation(
            ctx,
            f"talent:{talent_id}:{points}",
            lambda request_id, now: self.add_talent(
                ctx.author.id,
                talent_id.strip().lower(),
                points,
                request_id,
                now,
            ),
        )

    @tutien.group(
        name="dongphu",
        invoke_without_command=True,
        help="Xem Động Phủ.",
    )
    async def cultivation_cave(self, ctx: commands.Context) -> None:
        try:
            _, state = self._profile_account(ctx.author.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        cost = rules.cave_upgrade_cost(state)
        next_text = f"{cost:,} Linh Thạch" if cost is not None else "Đã tối đa"
        qi_rate, stone_rate = rules.production_rates(state)
        cave_bonus = 5 * (int(state["cave_level"]) - 1)
        await self._reply(
            ctx,
            (
                f"Động Phủ cấp **{state['cave_level']}/{rules.MAX_CAVE_LEVEL}** · "
                f"thưởng cấp **+{cave_bonus}% sản lượng** · "
                f"trữ **{rules.storage_cap_hours(state)}h**.\n"
                f"Sản lượng hiện tại: **~{qi_rate} Tu Vi/h · ~{stone_rate} LT/h**. "
                f"Cấp kế: **{next_text}**."
            ),
        )

    @cultivation_cave.command(name="nangcap", help="Nâng cấp Động Phủ.")
    async def cultivation_cave_upgrade(self, ctx: commands.Context) -> None:
        await self._send_mutation(
            ctx,
            "cave-upgrade",
            lambda request_id, now: self.upgrade_cave(
                ctx.author.id, request_id, now
            ),
        )

    @tutien.command(name="choden", help="Xem Chợ Đen Tiên Lộ.")
    async def cultivation_market(self, ctx: commands.Context) -> None:
        try:
            _, state = self._profile_account(ctx.author.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        await self._reply(ctx, embed=self.market_embed(state, _utcnow()))

    @tutien.command(name="mua", help="Mua trang bị Chợ Đen.")
    async def cultivation_buy(self, ctx: commands.Context, item_id: str) -> None:
        await self._send_mutation(
            ctx,
            f"buy:{item_id}",
            lambda request_id, now: self.buy(
                ctx.author.id, item_id, request_id, now
            ),
        )

    @tutien.command(name="kho", help="Xem kho Tiên Lộ.")
    async def cultivation_inventory(self, ctx: commands.Context) -> None:
        try:
            _, state = self._profile_account(ctx.author.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        await self._reply(ctx, embed=self.inventory_embed(ctx.author, state))

    @tutien.command(name="trangbi", help="Trang bị vật phẩm.")
    async def cultivation_equip(self, ctx: commands.Context, item_id: str) -> None:
        await self._send_mutation(
            ctx,
            f"equip:{item_id}",
            lambda request_id, now: self.equip(
                ctx.author.id, item_id, request_id, now
            ),
        )

    @tutien.command(name="phanra", help="Phân rã trang bị.")
    async def cultivation_salvage(self, ctx: commands.Context, item_id: str) -> None:
        await self._send_mutation(
            ctx,
            f"salvage:{item_id}",
            lambda request_id, now: self.salvage(
                ctx.author.id, item_id, request_id, now
            ),
        )

    @tutien.command(name="luyen", help="Luyện trang bị theo công thức.")
    async def cultivation_craft(
        self,
        ctx: commands.Context,
        recipe_id: str | None = None,
    ) -> None:
        if recipe_id is None:
            try:
                _, state = self._profile_account(ctx.author.id, _utcnow())
            except Exception as exc:
                await self._send_error(ctx, exc)
                return
            await self._reply(ctx, embed=self.crafting_embed(state))
            return
        await self._send_mutation(
            ctx,
            f"craft:{recipe_id}",
            lambda request_id, now: self.craft(
                ctx.author.id, recipe_id, request_id, now
            ),
        )

    @tutien.command(name="thiluyen", help="Khiêu chiến Tháp Thí Luyện.")
    @commands.cooldown(2, 5, commands.BucketType.user)
    async def cultivation_trial(
        self, ctx: commands.Context, floor: int | None = None
    ) -> None:
        await self._send_mutation(
            ctx,
            f"trial:{floor or 'next'}",
            lambda request_id, now: self.clear_trial(
                ctx.author.id, floor, request_id, now
            ),
        )

    @tutien.group(
        name="bicanh",
        invoke_without_command=True,
        help="Xem Bí Cảnh.",
    )
    async def cultivation_expedition(self, ctx: commands.Context) -> None:
        try:
            _, state = self._profile_account(ctx.author.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        await self._reply(ctx, embed=self.expedition_embed(state, _utcnow()))

    @cultivation_expedition.command(name="start", help="Bắt đầu Bí Cảnh.")
    async def cultivation_expedition_start(
        self,
        ctx: commands.Context,
        zone: str,
        hours: int,
    ) -> None:
        await self._send_mutation(
            ctx,
            f"expedition-start:{zone}:{hours}",
            lambda request_id, now: self.start_expedition(
                ctx.author.id, zone, hours, request_id, now
            ),
        )

    @cultivation_expedition.command(name="claim", help="Nhận thưởng Bí Cảnh.")
    async def cultivation_expedition_claim(self, ctx: commands.Context) -> None:
        await self._send_mutation(
            ctx,
            "expedition-claim",
            lambda request_id, now: self.claim(
                ctx.author.id,
                request_id,
                now,
                expedition_only=True,
            ),
        )

    @cultivation_expedition.command(name="cancel", help="Hủy Bí Cảnh.")
    async def cultivation_expedition_cancel(self, ctx: commands.Context) -> None:
        await self._send_mutation(
            ctx,
            "expedition-cancel",
            lambda request_id, now: self.cancel_expedition(
                ctx.author.id, request_id, now
            ),
        )

    @tutien.group(
        name="doido",
        invoke_without_command=True,
        help="Xem tỷ giá TC/Linh Thạch.",
    )
    async def cultivation_exchange(self, ctx: commands.Context) -> None:
        try:
            account, state = self._profile_account(ctx.author.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        await self._reply(
            ctx,
            embed=self.exchange_embed(state, int(account.get("balance", 0)), _utcnow()),
        )

    @cultivation_exchange.command(name="mua", help="Dùng TC mua Linh Thạch.")
    async def cultivation_exchange_buy(
        self, ctx: commands.Context, tc_amount: int
    ) -> None:
        await self._send_mutation(
            ctx,
            f"exchange-buy:{tc_amount}",
            lambda request_id, now: self.exchange_buy(
                ctx.author.id, tc_amount, request_id, now
            ),
        )

    @cultivation_exchange.command(name="ban", help="Bán Linh Thạch lấy TC.")
    async def cultivation_exchange_sell(
        self, ctx: commands.Context, stone_amount: int
    ) -> None:
        await self._send_mutation(
            ctx,
            f"exchange-sell:{stone_amount}",
            lambda request_id, now: self.exchange_sell(
                ctx.author.id, stone_amount, request_id, now
            ),
        )

    @tutien.command(name="profile", help="Xem hồ sơ Tiên Lộ.")
    async def cultivation_profile(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        target = member or ctx.author
        try:
            _, state = self._profile_account(target.id, _utcnow())
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        if target.id != ctx.author.id and not state["profile_public"]:
            await self._reply(ctx, "❌ Đạo hữu này để hồ sơ Tiên Lộ ở chế độ riêng tư.")
            return
        await self._reply(
            ctx,
            embed=self.profile_embed(
                target,
                state,
                _utcnow(),
                owner_view=target.id == ctx.author.id,
            ),
        )

    @tutien.command(name="top", help="Bảng xếp hạng Tiên Lộ trong server.")
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def cultivation_top(self, ctx: commands.Context) -> None:
        try:
            self._require_enabled()
            members = {
                member.id: member for member in ctx.guild.members if not member.bot
            }
            if not members:
                await self._reply(ctx, "Chưa có đạo hữu nào để xếp hạng.")
                return
            rows = list(
                self.accounts.find(
                    {
                        "user_id": {"$in": list(members)},
                        "cultivation.profile_public": True,
                    }
                )
                .sort(
                    [
                        ("cultivation.stage_index", DESCENDING),
                        ("cultivation.lifetime_qi", DESCENDING),
                        ("user_id", ASCENDING),
                    ]
                )
                .limit(10)
            )
        except Exception as exc:
            await self._send_error(ctx, exc)
            return
        lines = []
        for rank, account in enumerate(rows, start=1):
            member = members.get(int(account.get("user_id", 0)))
            if member is None:
                continue
            state = rules.normalize_state(account.get("cultivation"), _utcnow())
            lines.append(
                f"**{rank}.** {member.mention} · {rules.stage_for(state).name} · "
                f"{int(state['lifetime_qi']):,} tổng Tu Vi"
            )
        embed = discord.Embed(
            title="🏆 Tiên Lộ trong server",
            description="\n".join(lines) if lines else "Chưa có hồ sơ công khai.",
            color=discord.Color.gold(),
        )
        await self._reply(ctx, embed=embed)

    @tutien.command(name="riengtu", help="Đổi quyền riêng tư hồ sơ.")
    async def cultivation_privacy(
        self,
        ctx: commands.Context,
        visibility: str | None = None,
    ) -> None:
        if visibility is None:
            try:
                _, state = self._profile_account(ctx.author.id, _utcnow())
            except Exception as exc:
                await self._send_error(ctx, exc)
                return
            label = "public" if state["profile_public"] else "private"
            await self._reply(
                ctx,
                f"Hồ sơ hiện để **{label}**. Dùng `tutien riengtu <public|private>`.",
            )
            return
        await self._send_mutation(
            ctx,
            f"privacy:{visibility}",
            lambda request_id, now: self.set_privacy(
                ctx.author.id, visibility, request_id, now
            ),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CultivationCog(bot))
