import logging

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)


class ReplyTargetError(ValueError):
    """Raised when a replied message cannot safely identify a member."""


async def fetch_same_channel_reply(ctx: commands.Context) -> discord.Message:
    """Return the referenced message after guild/channel/webhook validation."""
    reference = getattr(ctx.message, "reference", None)
    if reference is None:
        raise ReplyTargetError("Hãy mention thành viên hoặc reply tin nhắn của họ.")
    if reference.message_id is None:
        raise ReplyTargetError("Tin nhắn được reply không còn khả dụng.")
    if reference.channel_id != ctx.channel.id:
        raise ReplyTargetError(
            "Chỉ có thể chọn mục tiêu từ tin nhắn được reply trong kênh hiện tại."
        )

    resolved = reference.resolved
    if isinstance(resolved, discord.DeletedReferencedMessage):
        raise ReplyTargetError(
            "Tin nhắn được reply đã bị xóa nên không thể xác định thành viên."
        )
    if isinstance(resolved, discord.Message):
        message = resolved
    else:
        message = reference.cached_message
        if message is None:
            try:
                message = await ctx.channel.fetch_message(reference.message_id)
            except discord.NotFound as exc:
                raise ReplyTargetError(
                    "Không tìm thấy tin nhắn được reply. Tin nhắn có thể đã bị xóa."
                ) from exc
            except discord.Forbidden as exc:
                raise ReplyTargetError(
                    "Bot không có quyền đọc lịch sử tin nhắn trong kênh này."
                ) from exc
            except discord.HTTPException as exc:
                logger.exception(
                    "Could not fetch moderation reply message=%s",
                    reference.message_id,
                )
                raise ReplyTargetError(
                    "Không thể tải tin nhắn được reply lúc này. Hãy thử lại sau."
                ) from exc

    if ctx.guild is None or message.guild is None or message.guild.id != ctx.guild.id:
        raise ReplyTargetError("Không thể chọn thành viên từ tin nhắn ở server khác.")
    if getattr(message, "id", None) != reference.message_id:
        raise ReplyTargetError(
            "Dữ liệu tin nhắn được reply không khớp; hãy reply lại tin nhắn mục tiêu."
        )
    if message.channel.id != ctx.channel.id:
        raise ReplyTargetError(
            "Chỉ có thể chọn mục tiêu từ tin nhắn được reply trong kênh hiện tại."
        )
    if getattr(message, "webhook_id", None) is not None:
        raise ReplyTargetError("Không thể chọn tác giả của tin nhắn webhook.")
    return message


async def resolve_same_channel_reply_member(
    ctx: commands.Context,
    *,
    allow_bots: bool = True,
) -> discord.Member:
    """Resolve the author of a same-channel reply to a current guild member."""
    message = await fetch_same_channel_reply(ctx)
    member = ctx.guild.get_member(message.author.id)
    if member is None:
        try:
            member = await ctx.guild.fetch_member(message.author.id)
        except discord.NotFound as exc:
            raise ReplyTargetError(
                "Tác giả tin nhắn không còn ở trong server."
            ) from exc
        except discord.Forbidden as exc:
            raise ReplyTargetError(
                "Bot không có quyền tải thông tin tác giả tin nhắn."
            ) from exc
        except discord.HTTPException as exc:
            logger.exception(
                "Could not fetch moderation reply member=%s",
                message.author.id,
            )
            raise ReplyTargetError(
                "Không thể tải thông tin thành viên lúc này. Hãy thử lại sau."
            ) from exc
    if not allow_bots and member.bot:
        raise ReplyTargetError("Không thể chọn tài khoản bot cho thao tác này.")
    return member
