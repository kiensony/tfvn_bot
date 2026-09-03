"""Verify signed provenance proofs emitted by TFVN cards and quotes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs._hash_verification import (
    FEMBOY_CARD_KIND,
    QUOTE_KIND,
    VERIFICATION_COLLECTION,
    VERIFICATION_REFERENCE_PREFIX,
    VerificationConfigurationError,
    VerificationReferenceError,
    VerificationTokenError,
    VerifiedClaims,
    normalize_verification_reference,
    verification_document_is_valid,
    verification_keyring_from_bot,
    verification_token_id_from_reference,
    verification_token_fingerprint,
    verify_verification_token,
)


logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
_FIELD_LIMIT = 1_024
_QUOTE_CHUNK_LIMIT = 1_000
_QUOTE_SNAPSHOT_LIMIT = 4_000


def _bounded(value: object, limit: int = _FIELD_LIMIT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _safe_text(value: object, limit: int = _FIELD_LIMIT) -> str:
    return _bounded(discord.utils.escape_markdown(str(value)), limit)


def _snowflake(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _issued_at_label(value: object) -> str:
    if not isinstance(value, str):
        return "Không rõ"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return "Không rõ"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _signed_time_label(timestamp: int) -> str:
    try:
        parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return "Không rõ"
    return parsed.strftime("%d/%m/%Y %H:%M UTC")


def _quote_snapshot_chunks(value: object) -> tuple[str, ...]:
    text = str(value).strip() or "(không có nội dung chữ)"
    if len(text) > _QUOTE_SNAPSHOT_LIMIT:
        text = text[: _QUOTE_SNAPSHOT_LIMIT - 1].rstrip() + "…"
    return tuple(
        text[index : index + _QUOTE_CHUNK_LIMIT]
        for index in range(0, len(text), _QUOTE_CHUNK_LIMIT)
    )


class HashVerifyCog(commands.Cog):
    """Authenticate a signed manifest and its database-bound snapshot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.verifications = bot.db[VERIFICATION_COLLECTION]
        try:
            self.verification_keyring = verification_keyring_from_bot(bot)
        except VerificationConfigurationError:
            self.verification_keyring = None

    @staticmethod
    def _member_label(
        payload: Mapping[str, Any],
        *,
        id_key: str,
        name_key: str,
    ) -> str:
        member_id = _snowflake(payload.get(id_key))
        name = payload.get(name_key)
        parts: list[str] = []
        if member_id is not None:
            parts.append(f"<@{member_id}> (`{member_id}`)")
        if isinstance(name, str) and name.strip():
            parts.append(_safe_text(name.strip(), 200))
        return " • ".join(parts) or "Không rõ"

    @staticmethod
    def _can_show_quote_details(
        ctx: commands.Context,
        payload: Mapping[str, Any],
    ) -> bool:
        channel_id = _snowflake(payload.get("channel_id"))
        if channel_id is None or getattr(ctx.channel, "id", None) != channel_id:
            return False
        permissions_for = getattr(ctx.channel, "permissions_for", None)
        if not callable(permissions_for):
            return False
        try:
            permissions = permissions_for(ctx.author)
        except (AttributeError, TypeError):
            return False
        return bool(
            getattr(permissions, "view_channel", False)
            and getattr(permissions, "read_message_history", False)
        )

    @staticmethod
    def _proof_footer(claims: VerifiedClaims) -> str:
        return f"Key: {claims.kid} • ID: {claims.token_id[:12]}…"

    @classmethod
    def _femboy_card_embed(
        cls,
        payload: Mapping[str, Any],
        claims: VerifiedClaims,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="✅ TFVN proof hợp lệ — Dữ liệu Femboy Card",
            description=(
                "Chữ ký TFVN hợp lệ; snapshot member, role và thời điểm "
                "phát hành khớp dữ liệu đã ký. Proof này không xác thực pixel "
                "của ảnh chụp màn hình."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Chủ thẻ trong dữ liệu đã ký",
            value=cls._member_label(
                payload,
                id_key="member_id",
                name_key="member_name",
            ),
            inline=False,
        )
        embed.add_field(
            name="Cấp hiệu tại lúc phát hành",
            value=_safe_text(payload.get("role_name", "Không rõ")),
            inline=True,
        )
        embed.add_field(
            name="Phát hành lúc",
            value=_issued_at_label(payload.get("issued_at")),
            inline=True,
        )
        embed.set_footer(text=cls._proof_footer(claims))
        return embed

    @classmethod
    def _quote_embed(
        cls,
        ctx: commands.Context,
        payload: Mapping[str, Any],
        claims: VerifiedClaims,
    ) -> discord.Embed:
        can_show_details = cls._can_show_quote_details(ctx, payload)
        embed = discord.Embed(
            title="✅ TFVN proof hợp lệ — Nội dung quote",
            description=(
                "Chữ ký TFVN hợp lệ; snapshot phần chữ mà bot đã dùng để tạo "
                "quote khớp dữ liệu đã ký. Proof không xác thực avatar, "
                "attachment hoặc pixel PNG."
            ),
            color=discord.Color.green(),
        )
        if can_show_details:
            embed.add_field(
                name="Tác giả nguồn",
                value=cls._member_label(
                    payload,
                    id_key="author_id",
                    name_key="author_name",
                ),
                inline=False,
            )
            for index, content_chunk in enumerate(
                _quote_snapshot_chunks(payload.get("content", "")),
                start=1,
            ):
                field_name = "Nội dung chữ TFVN đã dùng"
                if index > 1:
                    field_name += f" (phần {index})"
                embed.add_field(
                    name=field_name,
                    value=content_chunk,
                    inline=False,
                )
            jump_url = payload.get("message_url")
            if isinstance(jump_url, str) and jump_url.startswith(
                "https://discord.com/channels/"
            ):
                embed.add_field(
                    name="Tin nhắn nguồn",
                    value=f"[Mở tin nhắn]({jump_url})",
                    inline=False,
                )
            embed.add_field(
                name="Người tạo quote",
                value=cls._member_label(
                    payload,
                    id_key="issued_by_id",
                    name_key="issued_by_name",
                ),
                inline=True,
            )
        else:
            embed.add_field(
                name="Chi tiết quote",
                value=(
                    "Snapshot đã khớp nhưng nội dung được ẩn để không đưa quote "
                    "ra khỏi kênh nguồn. Hãy chạy lệnh trong đúng kênh/thread "
                    "nguồn và bảo đảm bạn có quyền đọc lịch sử tại đó."
                ),
                inline=False,
            )
        embed.add_field(
            name="Phát hành lúc",
            value=_issued_at_label(payload.get("issued_at")),
            inline=True,
        )
        embed.set_footer(text=cls._proof_footer(claims))
        return embed

    @classmethod
    def _snapshot_unavailable_embed(
        cls,
        claims: VerifiedClaims,
    ) -> discord.Embed:
        kind_label = (
            "Femboy Card" if claims.kind == FEMBOY_CARD_KIND else "Quote"
        )
        embed = discord.Embed(
            title="⚠️ Chữ ký manifest hợp lệ — Snapshot chưa xác minh",
            description=(
                f"Chữ ký {kind_label} khớp một key TFVN đã cấu hình, nhưng "
                "snapshot riêng tư không còn trong cơ sở dữ liệu. Không thể "
                "xác nhận nội dung card/quote hoặc quy trình đã phát hành nó."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Server đã ký",
            value=f"`{claims.guild_id}`",
            inline=True,
        )
        embed.add_field(
            name="Phát hành lúc",
            value=_signed_time_label(claims.issued_at),
            inline=True,
        )
        if claims.kind == FEMBOY_CARD_KIND:
            signed_ids = (
                f"Member `{claims.raw['member_id']}` • "
                f"Role `{claims.raw['role_id']}`"
            )
        else:
            signed_ids = (
                f"Channel `{claims.raw['channel_id']}` • "
                f"Message `{claims.raw['message_id']}` • "
                f"Author `{claims.raw['author_id']}`"
            )
        embed.add_field(name="ID trong manifest", value=signed_ids, inline=False)
        embed.set_footer(text=cls._proof_footer(claims))
        return embed

    @commands.command(
        name="hash_verify",
        help="Xác minh proof có chữ ký từ Femboy Card hoặc quote TFVN.",
        ignore_extra=False,
    )
    @commands.guild_only()
    @commands.cooldown(3, 10, commands.BucketType.user)
    async def hash_verify(
        self,
        ctx: commands.Context,
        proof_value: str,
    ) -> None:
        if self.verification_keyring is None:
            await ctx.send(
                "Tính năng proof chưa được cấu hình an toàn. "
                "Hãy báo quản trị viên bot.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        copied_value = proof_value.strip()
        if (
            len(copied_value) >= 2
            and copied_value.startswith("`")
            and copied_value.endswith("`")
        ):
            copied_value = copied_value[1:-1].strip()
        is_short_reference = copied_value.casefold().startswith(
            VERIFICATION_REFERENCE_PREFIX
        )

        if is_short_reference:
            try:
                reference = normalize_verification_reference(copied_value)
                requested_token_id = verification_token_id_from_reference(
                    reference
                )
            except VerificationReferenceError as exc:
                await ctx.send(f"❌ {exc}", allowed_mentions=NO_MENTIONS)
                return
            try:
                document = await asyncio.to_thread(
                    self.verifications.find_one,
                    {"_id": requested_token_id},
                )
            except PyMongoError:
                logger.exception("Failed to look up content verification proof")
                await ctx.send(
                    "Không thể tải snapshot proof lúc này. Hãy thử lại sau.",
                    allowed_mentions=NO_MENTIONS,
                )
                return
            if not isinstance(document, Mapping):
                await ctx.send(
                    "❌ Không tìm thấy mã proof hợp lệ.",
                    allowed_mentions=NO_MENTIONS,
                )
                return
            stored_token = document.get("token")
            if not isinstance(stored_token, str):
                await ctx.send(
                    "❌ Mã proof cũ không chứa chữ ký ẩn. "
                    "Hãy dùng token tfv1 đầy đủ ban đầu.",
                    allowed_mentions=NO_MENTIONS,
                )
                return
            try:
                claims = verify_verification_token(
                    stored_token,
                    self.verification_keyring,
                )
            except VerificationTokenError:
                logger.warning(
                    "Rejected unsigned or malformed short proof record"
                )
                await ctx.send(
                    "❌ Không tìm thấy mã proof hợp lệ.",
                    allowed_mentions=NO_MENTIONS,
                )
                return
            if (
                claims.token_id != requested_token_id
                or document.get("_id") != claims.token_id
            ):
                logger.warning("Rejected redirected short proof record")
                await ctx.send(
                    "❌ Không tìm thấy mã proof hợp lệ.",
                    allowed_mentions=NO_MENTIONS,
                )
                return
        else:
            try:
                claims = verify_verification_token(
                    proof_value,
                    self.verification_keyring,
                )
            except VerificationTokenError as exc:
                await ctx.send(f"❌ {exc}", allowed_mentions=NO_MENTIONS)
                return

            if claims.guild_id != ctx.guild.id:
                await ctx.send(
                    "❌ Proof này không được phát hành cho server hiện tại.",
                    allowed_mentions=NO_MENTIONS,
                )
                return

            try:
                document = await asyncio.to_thread(
                    self.verifications.find_one,
                    {"_id": claims.token_id},
                )
                if document is None:
                    document = await asyncio.to_thread(
                        self.verifications.find_one,
                        {"_id": verification_token_fingerprint(claims.token)},
                    )
            except PyMongoError:
                logger.exception("Failed to look up content verification proof")
                await ctx.send(
                    "Không thể tải snapshot proof lúc này. Hãy thử lại sau.",
                    allowed_mentions=NO_MENTIONS,
                )
                return

        if claims.guild_id != ctx.guild.id:
            await ctx.send(
                "❌ Proof này không được phát hành cho server hiện tại.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        if document is None:
            await ctx.send(
                embed=self._snapshot_unavailable_embed(claims),
                allowed_mentions=NO_MENTIONS,
            )
            return
        if not verification_document_is_valid(claims, document):
            logger.warning(
                "Signed proof %s has a missing or mismatched snapshot",
                claims.token_id,
            )
            await ctx.send(
                "⚠️ Chữ ký TFVN hợp lệ, nhưng snapshot đã lưu không khớp. "
                "Không thể xác nhận nội dung của card/quote này.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        payload = document.get("payload")
        if not isinstance(payload, Mapping):
            await ctx.send(
                "❌ Snapshot proof không hợp lệ.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        if claims.kind == FEMBOY_CARD_KIND:
            embed = self._femboy_card_embed(payload, claims)
        elif claims.kind == QUOTE_KIND:
            embed = self._quote_embed(ctx, payload, claims)
        else:
            await ctx.send(
                "❌ Loại proof này không được hỗ trợ.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    @hash_verify.error
    async def hash_verify_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(
            error,
            (commands.MissingRequiredArgument, commands.TooManyArguments),
        ):
            await ctx.send(
                f"Cách dùng: `{ctx.prefix}hash_verify <proof_code>`",
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send(
                "Lệnh hash_verify chỉ dùng được trong server.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            seconds = max(1, round(error.retry_after))
            await ctx.send(
                f"Chậm thôi, hãy thử kiểm tra lại sau **{seconds}** giây.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HashVerifyCog(bot))
