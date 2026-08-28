import logging

import discord
from discord.ext import commands

from cogs._hash_verification import (
    FEMBOY_CARD_KIND,
    VERIFICATION_COLLECTION,
    VerificationConfigurationError,
    VerificationStoreError,
    issue_verification_async,
    verification_keyring_from_bot,
    verification_reference_from_token,
)


logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()


class FemboyCardCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.FEMBOY_ROLE = bot.FEMBOY_ROLE
        self.verifications = bot.db[VERIFICATION_COLLECTION]
        try:
            self.verification_keyring = verification_keyring_from_bot(bot)
        except VerificationConfigurationError:
            self.verification_keyring = None

    @commands.command(
        name="femboycard",
        help="Tạo thẻ femboy cho chính bạn kèm proof có chữ ký TFVN.",
    )
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def femboy_card(self, ctx: commands.Context) -> None:
        member = ctx.author

        # find the user highest role that matches femboy roles
        femboy_roles = [
            role for role in member.roles if role.name in self.FEMBOY_ROLE
        ]

        if not femboy_roles:
            await ctx.send(
                f"{member.mention}, bạn không có vai trò femboy để tạo thẻ "
                "femboy!"
            )
            return
        femboy_role = max(femboy_roles, key=lambda role: role.position)

        if self.verification_keyring is None:
            await ctx.send(
                "Proof xác thực chưa được cấu hình an toàn. "
                "Hãy báo quản trị viên bot.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        issued_at = discord.utils.utcnow()
        try:
            verification_token = await issue_verification_async(
                self.verifications,
                self.verification_keyring,
                kind=FEMBOY_CARD_KIND,
                payload={
                    "guild_id": ctx.guild.id,
                    "member_id": member.id,
                    "member_name": getattr(member, "display_name", member.name),
                    "role_id": femboy_role.id,
                    "role_name": femboy_role.name,
                    "issued_by_id": member.id,
                },
                issued_at=issued_at,
            )
            verification_proof = verification_reference_from_token(
                verification_token,
                self.verification_keyring,
            )
        except (
            ValueError,
            VerificationConfigurationError,
            VerificationStoreError,
        ):
            logger.exception("Failed to issue femboy-card verification proof")
            await ctx.send(
                "Không thể tạo proof xác thực cho thẻ lúc này. Hãy thử lại sau.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        embed = discord.Embed(
            title="🌸 Femboy Card 🌸",
            description=(
                f"**Tên:** {member.mention}\n"
                f" **Cấp hiệu:** {femboy_role.name}\n"
                f" **ID thành viên:** {member.id}"
            ),
            color=femboy_role.color,
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        # Add more fields or customization as needed
        # embed.add_field(name="Tính cách", value="Dễ thương và quyến rũ!", inline=False)
        # embed.add_field(name="Sở thích", value="Ngắm femboy và chơi game!", inline=False)

        # embed.set_image(
        #     url=(
        #         femboy_role.icon.url
        #         if femboy_role.icon
        #         else member.display_avatar.url
        #     )
        # )

        embed.add_field(
            name="",
            value="**Được công nhận là Femboy**",
            inline=False,
        )
        embed.add_field(
            name="",
            value="Dễ thương - Tự tin - Tỏa sáng ✨",
            inline=False,
        )

        embed.add_field(
            name="",
            value=f"**Ngày tạo thẻ: ** {issued_at.strftime('%Y-%m-%d')}",
            inline=False,
        )
        embed.add_field(
            name="",
            value="**Hiệu lực đến: ** Mãi mãi dễ thương",
            inline=True,
        )

        embed.add_field(
            name="🔐 Mã proof TFVN",
            value=f"`{ctx.prefix}hash_verify {verification_proof}`",
            inline=False,
        )
        embed.set_footer(text="Dữ liệu member/role được TFVN Bot ký.")
        embed.timestamp = issued_at

        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    @femboy_card.error
    async def femboy_card_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send(
                "Lệnh femboycard chỉ dùng được trong server.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            seconds = max(1, round(error.retry_after))
            await ctx.send(
                f"Chậm thôi, hãy tạo thẻ lại sau **{seconds}** giây.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FemboyCardCog(bot))
