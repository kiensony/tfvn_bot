"""Quote real Discord messages as embeds or shareable image cards."""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import re

import discord
from discord.ext import commands

from cogs.utils._quote_card import normalize_quote_text, render_quote_card


logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
_MESSAGE_LINK = re.compile(
    r"https?://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)/?"
)
_MESSAGE_ID = re.compile(r"\d{15,22}")
_EMBED_QUOTE_PREFIX = ">>> "
_EMBED_DESCRIPTION_LIMIT = 4_096


def parse_quote_request(
    argument: str | None,
) -> tuple[bool, str | None]:
    """Return whether image mode was requested and the optional reference."""
    if argument is None or not argument.strip():
        return False, None

    parts = argument.strip().split(maxsplit=1)
    if parts[0].casefold() == "image":
        reference = parts[1].strip() if len(parts) == 2 else ""
        return True, reference or None
    return False, argument.strip()


def prepare_embed_quote_text(content: str) -> str:
    """Validate and bound message text for a Discord embed description."""
    text = content.strip()
    if not text:
        raise ValueError("Tin nhắn không có nội dung chữ để tạo quote.")

    available = _EMBED_DESCRIPTION_LIMIT - len(_EMBED_QUOTE_PREFIX)
    if len(text) > available:
        text = text[: available - 1].rstrip() + "…"
    return text


class QuoteLookupError(Exception):
    """A message reference could not be safely resolved for the user."""


class QuoteCog(commands.Cog):
    """Create embed or image quotes from messages in the current channel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _explicit_message_id(
        ctx: commands.Context,
        message_reference: str,
    ) -> int:
        reference = message_reference.strip().strip("<>")
        if _MESSAGE_ID.fullmatch(reference):
            return int(reference)

        match = _MESSAGE_LINK.fullmatch(reference)
        if match is None:
            raise QuoteLookupError(
                "Link hoặc ID tin nhắn không hợp lệ."
            )
        if ctx.guild is None:
            raise QuoteLookupError("Lệnh quote chỉ dùng được trong server.")
        if (
            int(match.group("guild_id")) != ctx.guild.id
            or int(match.group("channel_id")) != ctx.channel.id
        ):
            raise QuoteLookupError(
                "Chỉ có thể quote tin nhắn trong kênh hiện tại."
            )
        return int(match.group("message_id"))

    @staticmethod
    async def _fetch_message(
        ctx: commands.Context,
        message_id: int,
    ) -> discord.Message:
        try:
            return await ctx.channel.fetch_message(message_id)
        except discord.NotFound as exc:
            raise QuoteLookupError(
                "Không tìm thấy tin nhắn đó trong kênh hiện tại."
            ) from exc
        except discord.Forbidden as exc:
            raise QuoteLookupError(
                "Bot không có quyền đọc lịch sử tin nhắn trong kênh này."
            ) from exc
        except discord.HTTPException as exc:
            logger.exception("Failed to fetch message for quote")
            raise QuoteLookupError(
                "Không thể tải tin nhắn lúc này. Hãy thử lại sau."
            ) from exc

    async def _resolve_message(
        self,
        ctx: commands.Context,
        message_reference: str | None,
    ) -> discord.Message:
        if message_reference:
            message_id = self._explicit_message_id(ctx, message_reference)
            message = await self._fetch_message(ctx, message_id)
        else:
            reference = ctx.message.reference
            if reference is None or reference.message_id is None:
                raise QuoteLookupError(
                    f"Hãy reply tin nhắn bằng `{ctx.prefix}quote` (embed) hoặc "
                    f"`{ctx.prefix}quote image` (ảnh). Bạn cũng có thể thêm "
                    "link/ID tin nhắn ở cuối lệnh."
                )

            resolved = reference.resolved
            if isinstance(resolved, discord.Message):
                message = resolved
            else:
                cached = reference.cached_message
                if cached is not None:
                    message = cached
                else:
                    if reference.channel_id != ctx.channel.id:
                        raise QuoteLookupError(
                            "Chỉ có thể quote tin nhắn trong kênh hiện tại."
                        )
                    message = await self._fetch_message(
                        ctx,
                        reference.message_id,
                    )

        if (
            message.guild is None
            or ctx.guild is None
            or message.guild.id != ctx.guild.id
        ):
            raise QuoteLookupError("Không thể quote tin nhắn từ server khác.")
        if message.channel.id != ctx.channel.id:
            raise QuoteLookupError(
                "Chỉ có thể quote tin nhắn trong kênh hiện tại."
            )
        return message

    @staticmethod
    def _server_avatar(author: discord.abc.User) -> discord.Asset:
        guild_avatar = getattr(author, "guild_avatar", None)
        return guild_avatar or author.display_avatar

    @staticmethod
    def _accent_color(author: discord.abc.User) -> tuple[int, int, int]:
        if isinstance(author, discord.Member) and author.color.value:
            return author.color.to_rgb()
        return (88, 101, 242)

    async def _avatar_bytes(self, author: discord.abc.User) -> bytes | None:
        try:
            return await self._server_avatar(author).with_size(256).read()
        except (discord.DiscordException, OSError):
            logger.warning(
                "Could not download avatar for quote author %s",
                getattr(author, "id", "unknown"),
                exc_info=True,
            )
            return None

    async def _send_text_quote(
        self,
        ctx: commands.Context,
        message: discord.Message,
        quote_text: str,
    ) -> None:
        author = message.author
        avatar_url = self._server_avatar(author).url
        embed = discord.Embed(
            title="💬 Trích dẫn",
            url=message.jump_url,
            description=f"{_EMBED_QUOTE_PREFIX}{quote_text}",
            color=discord.Color.from_rgb(*self._accent_color(author)),
            timestamp=message.created_at,
        )
        embed.set_author(
            name=getattr(author, "display_name", author.name),
            icon_url=avatar_url,
            url=message.jump_url,
        )
        embed.set_footer(text=f"#{getattr(message.channel, 'name', 'channel')}")
        try:
            await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            safe_name = discord.utils.escape_markdown(
                getattr(author, "display_name", author.name)
            )
            plain_quote = quote_text[:1750]
            await ctx.send(
                f"**{safe_name}:** {plain_quote}\n{message.jump_url}",
                allowed_mentions=NO_MENTIONS,
            )

    @commands.command(
        name="quote",
        aliases=["q", "quotes"],
        help="Quote dạng embed; thêm `image` để tạo ảnh PNG.",
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def quote(
        self,
        ctx: commands.Context,
        *,
        message_reference: str | None = None,
    ) -> None:
        image_mode, message_reference = parse_quote_request(message_reference)
        try:
            message = await self._resolve_message(ctx, message_reference)
        except QuoteLookupError as exc:
            await ctx.send(str(exc), allowed_mentions=NO_MENTIONS)
            return

        raw_content = message.clean_content
        try:
            embed_quote_text = prepare_embed_quote_text(raw_content)
        except ValueError as exc:
            await ctx.send(str(exc), allowed_mentions=NO_MENTIONS)
            return

        if not image_mode:
            await self._send_text_quote(ctx, message, embed_quote_text)
            return

        try:
            quote_text = normalize_quote_text(raw_content)
        except ValueError as exc:
            await ctx.send(str(exc), allowed_mentions=NO_MENTIONS)
            return
        author = message.author
        display_name = getattr(author, "display_name", author.name)
        username = getattr(author, "name", display_name)
        context_label = message.created_at.strftime(
            "%d/%m/%Y %H:%M UTC"
        )

        async with ctx.typing():
            avatar_bytes = await self._avatar_bytes(author)
            try:
                card_bytes = await asyncio.to_thread(
                    render_quote_card,
                    avatar_bytes=avatar_bytes,
                    display_name=display_name,
                    username=username,
                    quote_text=quote_text,
                    context_label=context_label,
                    accent_rgb=self._accent_color(author),
                )
            except Exception:
                logger.exception("Failed to render quote card")
                await self._send_text_quote(
                    ctx,
                    message,
                    embed_quote_text,
                )
                return

        filename = f"quote-{message.id}.png"
        source_link = f"🔗 [Xem tin nhắn gốc]({message.jump_url})"
        try:
            await ctx.send(
                source_link,
                file=discord.File(BytesIO(card_bytes), filename=filename),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.exception("Failed to upload quote card; using embed fallback")
            await self._send_text_quote(ctx, message, embed_quote_text)

    @quote.error
    async def quote_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send(
                "Lệnh quote chỉ dùng được trong server.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            seconds = max(1, round(error.retry_after))
            await ctx.send(
                f"Chậm thôi, hãy thử quote lại sau **{seconds}** giây.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuoteCog(bot))
