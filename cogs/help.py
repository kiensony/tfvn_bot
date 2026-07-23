import asyncio

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]

from cogs._beta_function import beta_access_denial, is_beta_function


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def custom_help(self, ctx: commands.Context, *args):
        if args:
            return  # Ignore arguments for now

        # check if user have administrator permission

        # normal command (for all users)
        title = "📖 Hướng dẫn sử dụng TFVN bot"
        embed = discord.Embed(
            title=title,
            description="Danh sách lệnh hiện có:",
            color=0xFFC0CB,
        )
        beta_commands = sorted(
            command.qualified_name
            for command in self.bot.walk_commands()
            if is_beta_function(command) and not command.hidden
        )
        if beta_commands and beta_access_denial(ctx) is None:
            command_list = ", ".join(
                f"`{self.bot.command_prefix}{name}`" for name in beta_commands
            )
            embed.add_field(
                name="Beta access:",
                value=(
                    f"{command_list}\n"
                    "Các lệnh này yêu cầu Beta role đã cấu hình."
                ),
                inline=False,
            )
        # AFK commands
        embed.add_field(
            name="AFK:",
            value=(
                f"`{self.bot.command_prefix} afk` – Đặt trạng thái AFK (sau đó làm theo hướng dẫn của bot).\n"
                f"`{self.bot.command_prefix} afk clear` – Hủy trạng thái AFK khi bạn quay lại.\n"
                f"`{self.bot.command_prefix} afk ping_check` – Xem ai nhắc đến bạn khi đang AFK.\n"
            ),
            inline=False,
        )
        
        # Content of the day commands
        embed.add_field(
            name="Nội dung trong ngày:",
            value=(
                f"`{self.bot.command_prefix} random_femboy` – Xem ngẫu nhiên 1 ảnh femboy và lấy thông tin của femboy đó.\n"
            ),
            inline=False,
        )

        # Economy commands
        embed.add_field(
            name="Trap Coin & shop:",
            value=(
                f"`{self.bot.command_prefix} daily` – Điểm danh hàng ngày và nhận 10 trap coin.\n"
                f"`{self.bot.command_prefix} user_balance` – Xem số Trap coin hiện có của bạn.\n"
                f"`{self.bot.command_prefix}user_transactions` – Xem 10 giao dịch gần nhất.\n"
                f"`{self.bot.command_prefix}shop` – Xem cửa hàng Trap Coin.\n"
                f"`{self.bot.command_prefix}shop inventory` – Xem vật phẩm đã mua.\n"
            ),
            inline=False,
        )

        # Games commands
        embed.add_field(
            name="Trò chơi:",
            value=(
                f"`{self.bot.command_prefix} sicbo` – Tài xỉu (đang phát triển cơ chế trả thưởng).\n"
                f"`{self.bot.command_prefix} slot` – Chơi máy slot (tốn 5 Trap Coins để chơi).\n"
                f"`{self.bot.command_prefix} flip_coin <head/tail> <n>` – Chơi tung đồng xu (tốn n Trap Coins để chơi).\n"
            ),
            inline=False,
        )

        

        # Funny things
        embed.add_field(
            name="Mấy thứ vui vui:",
            value=(
                f"`{self.bot.command_prefix} gay @user` – Kiểm tra độ gay của người được tag.\n"
                f"`{self.bot.command_prefix} ship @user1 @user2` – Đo mức độ hợp đôi của hai người dùng.\n"
                f"`{self.bot.command_prefix} penisize @user` – Đo kích thước peni của người được tag.\n"
            ),
            inline=False,
        )

        # Interaction commands
        embed.add_field(
            name="Tương tác chung:",
            value=(f"`{self.bot.command_prefix} hello` – Chào con bot.\n"
                   f"`{self.bot.command_prefix} cat` – Mèo.\n"
                   f"`{self.bot.command_prefix} dog` – Chó.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Tương tác với member khác:",
            value=(
                f"`{self.bot.command_prefix} kiss @user` – Hôn member khác.\n"
                f"`{self.bot.command_prefix} hug @user` – Ôm member khác.\n"
                f"`{self.bot.command_prefix} pat @user` – Xoa đầu member khác.\n"
                f"`{self.bot.command_prefix} slap @user` – Tát member khác.\n"
                f"`{self.bot.command_prefix} punch @user` – Đấm member khác.\n"
                f"`{self.bot.command_prefix} hit @user` – Đánh member khác.\n"
                f"`{self.bot.command_prefix} poke @user` – Chọc member khác.\n"
                f"`{self.bot.command_prefix} cuddle @user` – Cuddle member khác.\n"
                f"`{self.bot.command_prefix} snuggle @user` – Snuggle member khác.\n"
                f"`{self.bot.command_prefix} boop @user` – Boop mũi member khác.\n"
                f"`{self.bot.command_prefix} handhold @user` – Nắm tay member khác.\n"
                f"`{self.bot.command_prefix} bonk @user` – Bonk member khác.\n"
                f"`{self.bot.command_prefix} bite @user` – Cắn member khác.\n"
                f"`{self.bot.command_prefix} stare @user` – Nhìn chằm chằm member khác.\n"
                f"`{self.bot.command_prefix} avatar @user` – Xem avatar global của member.\n"
                f"`{self.bot.command_prefix} server_avatar @user` – Xem avatar server của member.\n"
            ),
            inline=False,
        )

        # Interaction ranking commands
        embed.add_field(
            name="BXH tương tác:",
            value=(
                f"`{self.bot.command_prefix} rank` – Xem bảng xếp hạng tương tác chung.\n"
                f"`{self.bot.command_prefix} rank r` – Xem bảng xếp hạng người bị/được tương tác chung.\n"
                f"`{self.bot.command_prefix} rank <action>` – Xem bảng xếp hạng member theo tương tác riêng.\n"
                f"`{self.bot.command_prefix} rank r <action>` – Xem bảng xếp hạng member bị/được tương tác riêng.\n"
            ),
            inline=False,
        )

        # NSFW commands
        embed.add_field(
            name="NSFW ⚠️⚠️⚠️:",
            value=(
                "Chỉ được sử dụng trong channel NSFW!\n"
                f"`{self.bot.command_prefix} nsfw` – Hướng dẫn lệnh nsfw.\n"
                f"`{self.bot.command_prefix} verify` – Hướng dẫn chứng thực độ tuổi.\n"
                f"`{self.bot.command_prefix}verified @user` – Support gán verified role cho member.\n"
                f"`{self.bot.command_prefix}unverified @user` – Support gỡ verified role khỏi member.\n"
            ),
            inline=False,
        )

        # Wordconnect commands
        embed.add_field(
            name="Nối từ:",
            value=(
                f"`{self.bot.command_prefix} noitu status` – Xem trạng thái hiện tại trò chơi nối từ.\n"
                f"`{self.bot.command_prefix} noitu end` – Dừng trò chơi nối từ.\n"
                f"`{self.bot.command_prefix} noitu hint` – Nhận gợi ý trong trò chơi nối từ.\n"
                f"`{self.bot.command_prefix} noitu analyze` – Phân tích trong trò chơi nối từ.\n"
            ),
            inline=False,
        )

        embed.set_footer(text="Prefix: " + self.bot.command_prefix)

        await ctx.send(embed=embed)

    @commands.command(name="mod")
    @commands.has_permissions(administrator=True, moderate_members=True)
    async def mod_help(self, ctx, *args):
        if args:
            return  # Ignore arguments for now
        title = "Lệnh quản trị viên"
        embed = discord.Embed(
            title=title,
            color=0xFF0000,
        )
        embed.add_field(
            name="Quản lý member:",
            value=(
                f"`{self.bot.command_prefix} kick @user [reason]` – Kick member khỏi server.\n"
                f"`{self.bot.command_prefix} ban @user [reason]` – Ban member khỏi server.\n"
                f"`{self.bot.command_prefix} mute @user [reason]` – Gán role Muted.\n"
                f"`{self.bot.command_prefix} unmute @user` – Gỡ mute cho member.\n"
                f"`{self.bot.command_prefix} nickchange @user [new_nick]` – Đổi nickname cho member.\n"
                f"`{self.bot.command_prefix} softban @user [reason]` – Softban member khỏi server.\n"
                f"`{self.bot.command_prefix} timeout @user <duration_minutes> [reason]` – Timeout member trong thời gian nhất định.\n"
                f"`{self.bot.command_prefix} untimeout @user` – Gỡ timeout cho member.\n"
                f"`{self.bot.command_prefix} warn @user [reason]` – Cảnh cáo member.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Quản lý tin nhắn:",
            value=(
                f"`{self.bot.command_prefix} purge <n>` – Xoá n tin nhắn gần nhất trong channel hiện tại.\n"
                f"`{self.bot.command_prefix} purge_user @user <n>` – Xoá n tin nhắn của user trong channel hiện tại.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Cases & cấu hình:",
            value=(
                f"`{self.bot.command_prefix}case view <số>` – Xem moderation case.\n"
                f"`{self.bot.command_prefix}case history @user` – Xem lịch sử member.\n"
                f"`{self.bot.command_prefix}case log_channel [channel]` – Đặt mod-log channel.\n"
                f"`{self.bot.command_prefix}setup check` – Kiểm tra config và permission.\n"
            ),
            inline=False,
        )
        community_admin_commands = [
            f"`{self.bot.command_prefix}shop add_role <id> <giá> @role` – Thêm role vào shop.\n",
        ]
        embed.add_field(
            name="Hệ thống cộng đồng:",
            value="".join(community_admin_commands),
            inline=False,
        )
        await ctx.send(embed=embed)
    @mod_help.error
    async def mod_help_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("⚠️ Bạn không có quyền sử dụng lệnh này.")



    @commands.command(name="nsfw")
    async def nsfw_help(self, ctx, *args):
        if args:
            return  # Ignore arguments for now
        embed = discord.Embed(
            title="Lệnh NSFW",
            color=0xFFC0CB,
        )
        embed.add_field(
            name="Tìm kiếm nội dung NSFW:",
            value=(
                f"`{self.bot.command_prefix} r34 <tags>` – Tìm kiếm ảnh/video trên Rule34.\n"
                f"`{self.bot.command_prefix} gbr <tags>` – Tìm kiếm ảnh/video trên Gelbooru.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Luật chơi các tương tác:",
            value=(
                f"`{self.bot.command_prefix} nsfwrule` – Luật chơi các tương tác.\n"
            ),
            inline=False,
        )
        # embed.add_field(
        #     name="Tự sướng:",
        #     value=(
        #         f"`{self.bot.command_prefix} solo` – Tự sướng (mông/lồn/cặc).\n"
        #         f"`{self.bot.command_prefix} blowjob` – Tự blowjob.\n"
        #         f"`{self.bot.command_prefix} rimjob` – Tự rimjob (liếm lồn).\n"
        #         f"`{self.bot.command_prefix} handjob` – Tự handjob.\n"
        #         # f"`!tf footjob` – Tự footjob.\n"
        #         # f"`!tf finger` – Tự móc lồn.\n"
        #     ),
        #     inline=False,
        # )
        embed.add_field(
            name="Chịch member khác:",
            value=(
                f"`{self.bot.command_prefix} bj @user` - Blowjob cho member khác.\n"
                f"`{self.bot.command_prefix} rj @user` - Rimjob (liếm lồn) cho member khác.\n"
                f"`{self.bot.command_prefix} hj @user` - Handjob cho member khác.\n"
                # "`!tf fj @user - Footjob cho member khác.\n"
                # "`!tf finger @user - Móc member khác.\n"
                f"`{self.bot.command_prefix} frot @user` - Frotting với member khác.\n"
                f"`{self.bot.command_prefix} fuck @user` - Làm tình với member khác.\n"
                f"`{self.bot.command_prefix} cream @user` - Creampie member khác.\n"
                f"`{self.bot.command_prefix} 3some @user1 @user2` - Chơi 3some với 2 member.\n"
                f"`{self.bot.command_prefix} orgy @user1 ... @user10` - Chơi orgy với 2-10 member.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Bảng xếp hạng - vinh danh kẻ dâm:",
            value=(
                f"`{self.bot.command_prefix} ranknsfw` - Xem bảng xếp hạng tổng thể quỷ sếch.\n"
                f"`{self.bot.command_prefix} ranknsfw r` - Xem bảng xếp hạng tổng người bị sếch.\n"
                f"`{self.bot.command_prefix} ranknsfw <action>` - Xem bảng xếp hạng quỷ sếch theo tương tác.\n"
                f"`{self.bot.command_prefix} ranknsfw r <action>` - Xem bảng xếp hạng người bị sếch theo tương tác.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Chỉ dành cho Femboy Queen",
            value=(
                f"`{self.bot.command_prefix} locknsfw @user` - Khoá lệnh NSFW cho member khác.\n"
                f"`{self.bot.command_prefix} unlocknsfw` - Mở khoá lệnh NSFW \n"
            ),
            inline=False,
        )
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        else:
            await ctx.reply(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
