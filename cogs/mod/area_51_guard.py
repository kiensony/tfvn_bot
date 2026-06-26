import logging
import unicodedata
from datetime import time, timezone

import discord
from discord.ext import commands, tasks


NATO_PHONETIC_ALPHABET = {
    "A": "Alpha",
    "B": "Bravo",
    "C": "Charlie",
    "D": "Delta",
    "E": "Echo",
    "F": "Foxtrot",
    "G": "Golf",
    "H": "Hotel",
    "I": "India",
    "J": "Juliett",
    "K": "Kilo",
    "L": "Lima",
    "M": "Mike",
    "N": "November",
    "O": "Oscar",
    "P": "Papa",
    "Q": "Quebec",
    "R": "Romeo",
    "S": "Sierra",
    "T": "Tango",
    "U": "Uniform",
    "V": "Victor",
    "W": "Whiskey",
    "X": "X-ray",
    "Y": "Yankee",
    "Z": "Zulu",
}
NATO_DIGITS = {
    "0": "Zero",
    "1": "One",
    "2": "Two",
    "3": "Three",
    "4": "Four",
    "5": "Five",
    "6": "Six",
    "7": "Seven",
    "8": "Eight",
    "9": "Nine",
}
RADIO_SYMBOLS = {
    "-": "Dash",
    "_": "Underscore",
    ".": "Dot",
}
MAX_NATO_CALLSIGN_WORDS = 16
AREA_51_CHANNEL_ID_VARIABLE = "AREA_51_CHANNEL_ID"
AREA_51_PRUNE_HOURS_VARIABLE = "AREA_51_PRUNE_HOURS"
DEFAULT_AREA_51_PRUNE_HOURS = 1
AREA_51_CANCEL_SECONDS = 30
MAX_DISCORD_BAN_DELETE_SECONDS = 7 * 24 * 60 * 60
AREA_51_WEEKLY_REMINDER_TIME = time(hour=0, minute=0, tzinfo=timezone.utc)
AREA_51_WEEKLY_REMINDER_WEEKDAY = 0


def _nato_callsign(name: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
    )

    words = []
    for char in ascii_name:
        word = (
            NATO_PHONETIC_ALPHABET.get(char)
            or NATO_DIGITS.get(char)
            or RADIO_SYMBOLS.get(char)
        )
        if not word:
            continue

        words.append(word)
        if len(words) >= MAX_NATO_CALLSIGN_WORDS:
            break

    return " ".join(words) if words else "Khong xac dinh"


def _user_nato_callsign(user: discord.abc.User) -> str:
    return _nato_callsign(user.name)


class Area51CancelBanView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=AREA_51_CANCEL_SECONDS)
        self.member = member
        self.cancelled_by: discord.abc.User | None = None

    def _disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Hủy ban", style=discord.ButtonStyle.success)
    async def cancel_ban(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.cancelled_by = interaction.user
        member_callsign = _user_nato_callsign(self.member)
        operator_callsign = _user_nato_callsign(interaction.user)
        self._disable_buttons()
        await interaction.response.edit_message(
            content=(
                "**BÁO ĐỘNG AREA 51 ĐÃ HỦY**\n"
                f"Lệnh ban đối với {member_callsign} ({self.member.mention}) "
                f"đã được hủy bởi {operator_callsign} ({interaction.user.mention})."
            ),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.stop()


class Area51GuardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        self._pending_bans: set[int] = set()
        self.weekly_area_51_reminder.start()

    def cog_unload(self):
        self.weekly_area_51_reminder.cancel()

    def _global_var(self, name: str):
        if not hasattr(self.bot, "global_vars"):
            return None

        return self.bot.global_vars.get(name)

    def _area_51_channel_id(self) -> int | None:
        channel_id = self._global_var(AREA_51_CHANNEL_ID_VARIABLE)
        if not channel_id:
            return None

        try:
            return int(channel_id)
        except (TypeError, ValueError):
            self.logger.warning("%s phải là ID kênh hợp lệ.", AREA_51_CHANNEL_ID_VARIABLE)
            return None

    def _prune_seconds(self) -> int:
        prune_hours = self._global_var(AREA_51_PRUNE_HOURS_VARIABLE)
        if prune_hours in (None, ""):
            prune_hours = DEFAULT_AREA_51_PRUNE_HOURS

        try:
            hours = float(prune_hours)
        except (TypeError, ValueError):
            self.logger.warning(
                "%s phải là số giờ. Dùng mặc định %s giờ.",
                AREA_51_PRUNE_HOURS_VARIABLE,
                DEFAULT_AREA_51_PRUNE_HOURS,
            )
            hours = DEFAULT_AREA_51_PRUNE_HOURS

        seconds = max(0, int(hours * 60 * 60))
        return min(seconds, MAX_DISCORD_BAN_DELETE_SECONDS)

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if not self.bot.user:
            return None

        return guild.get_member(self.bot.user.id)

    def _can_guard_ban(self, member: discord.Member) -> bool:
        guild = member.guild
        bot_member = self._bot_member(guild)
        if not bot_member or not bot_member.guild_permissions.ban_members:
            return False

        if member.id == guild.owner_id:
            return False

        return bot_member.top_role > member.top_role

    def _weekly_reminder_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="CẢNH BÁO ĐỊNH KỲ AREA 51",
            description="KHU QUÂN SỰ CẤM - KHÔNG PHẬN SỰ MIỄN VÀO",
            color=discord.Color.dark_red(),
        )
        embed.add_field(
            name="Trạng thái khu vực",
            value="Khu vực này không cho phép truy cập công khai.",
            inline=False,
        )
        embed.add_field(
            name="Giao thức an ninh",
            value=(
                "Mọi tín hiệu, tin nhắn, tệp đính kèm hoặc hoạt động gửi vào đây "
                "sẽ kích hoạt giao thức an ninh Area 51."
            ),
            inline=False,
        )
        embed.set_footer(
            text="Không phận thông tin đang được giám sát."
        )
        return embed

    async def _send_area_51_reminder_now(self) -> bool:
        channel_id = self._area_51_channel_id()
        if channel_id is None:
            return False

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                self.logger.exception(
                    "Bảo vệ Area 51 không thể lấy kênh nhắc nhở %s.",
                    channel_id,
                )
                return False

        try:
            await channel.send(
                embed=self._weekly_reminder_embed(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except AttributeError:
            self.logger.warning(
                "Kênh Area 51 %s không hỗ trợ gửi tin nhắn nhắc nhở.",
                channel_id,
            )
        except discord.Forbidden:
            self.logger.exception(
                "Bảo vệ Area 51 không có quyền gửi nhắc nhở vào kênh %s.",
                channel_id,
            )
        except discord.HTTPException:
            self.logger.exception(
                "Bảo vệ Area 51 gửi nhắc nhở thất bại vào kênh %s.",
                channel_id,
            )

        return False

    @tasks.loop(time=AREA_51_WEEKLY_REMINDER_TIME)
    async def weekly_area_51_reminder(self) -> None:
        now = discord.utils.utcnow()
        if now.weekday() != AREA_51_WEEKLY_REMINDER_WEEKDAY:
            return

        channel_id = self._area_51_channel_id()
        if channel_id is None:
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                self.logger.exception(
                    "Bảo vệ Area 51 không thể lấy kênh nhắc nhở %s.",
                    channel_id,
                )
                return

        try:
            await channel.send(
                embed=self._weekly_reminder_embed(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except AttributeError:
            self.logger.warning(
                "Kênh Area 51 %s không hỗ trợ gửi tin nhắn nhắc nhở.",
                channel_id,
            )
        except discord.Forbidden:
            self.logger.exception(
                "Bảo vệ Area 51 không có quyền gửi nhắc nhở vào kênh %s.",
                channel_id,
            )
        except discord.HTTPException:
            self.logger.exception(
                "Bảo vệ Area 51 gửi nhắc nhở thất bại vào kênh %s.",
                channel_id,
            )

    @weekly_area_51_reminder.before_loop
    async def before_weekly_area_51_reminder(self) -> None:
        await self.bot.wait_until_ready()

    @commands.command(
        name="area51_fire",
        aliases=["area51_bump_now", "area51_remind_now"],
        help="Gửi cảnh báo Area 51 ngay lập tức.",
    )
    @commands.has_permissions(administrator=True)
    async def area_51_fire(self, ctx: commands.Context):
        sent = await self._send_area_51_reminder_now()
        if sent:
            await ctx.send("Đã kích hoạt cảnh báo Area 51.", delete_after=10)
            return

        await ctx.send(
            "Không thể kích hoạt cảnh báo Area 51. Kiểm tra AREA_51_CHANNEL_ID và quyền gửi tin nhắn.",
            delete_after=10,
        )

    async def _delete_trigger_message(self, message: discord.Message) -> None:
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            return
        except discord.HTTPException:
            self.logger.exception(
                "Bảo vệ Area 51 không thể xóa tin nhắn kích hoạt %s.",
                message.id,
            )

    async def _send_cancel_prompt(
        self,
        member: discord.Member,
        channel: discord.abc.Messageable,
        view: Area51CancelBanView,
    ) -> discord.Message | None:
        member_callsign = _user_nato_callsign(member)
        try:
            return await channel.send(
                (
                    "**CẢNH BÁO AREA 51 - KHU QUÂN SỰ CẤM**\n"
                    f"Phát hiện truy cập trái phép: {member_callsign} ({member.mention}).\n"
                    f"Mã định danh: `{member.id}`.\n"
                    f"Giao thức an ninh sẽ ban sau {AREA_51_CANCEL_SECONDS} giây.\n"
                    "Nếu đây là báo động nhầm, bấm **Hủy ban** để dừng lệnh."
                ),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
                delete_after=AREA_51_CANCEL_SECONDS,
            )
        except discord.Forbidden:
            self.logger.exception(
                "Bảo vệ Area 51 không thể gửi nút hủy ban cho %s (%s).",
                member,
                member.id,
            )
        except discord.HTTPException:
            self.logger.exception(
                "Bảo vệ Area 51 gửi nút hủy ban thất bại cho %s (%s).",
                member,
                member.id,
            )

        return None

    async def _finish_cancel_prompt(
        self,
        prompt: discord.Message | None,
        content: str,
        view: Area51CancelBanView,
    ) -> None:
        if prompt is None:
            return

        view._disable_buttons()

        try:
            await prompt.edit(
                content=content,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound):
            return
        except discord.HTTPException:
            self.logger.exception(
                "Bảo vệ Area 51 cập nhật thông báo hủy ban thất bại %s.",
                prompt.id,
            )

    @commands.Cog.listener("on_message")
    async def guard_area_51(self, message: discord.Message):
        if not message.guild:
            return

        if self.bot.user and message.author.id == self.bot.user.id:
            return

        area_51_channel_id = self._area_51_channel_id()
        if message.channel.id != area_51_channel_id:
            return

        await self._delete_trigger_message(message)

        if not isinstance(message.author, discord.Member):
            member = message.guild.get_member(message.author.id)
            if member is None:
                return
        else:
            member = message.author

        if member.id in self._pending_bans:
            return

        if not self._can_guard_ban(member):
            self.logger.warning(
                "Bảo vệ Area 51 không thể ban %s (%s) trong server %s.",
                member,
                member.id,
                message.guild.id,
            )
            return

        self._pending_bans.add(member.id)
        reason = (
            f"Giao thức Area 51: truy cập trái phép khu quân sự cấm "
            f"{message.channel} ({message.channel.id})"
        )
        view = Area51CancelBanView(member)
        prompt = await self._send_cancel_prompt(member, message.channel, view)
        member_callsign = _user_nato_callsign(member)
        action_finished = False

        try:
            await view.wait()
            if view.cancelled_by is not None:
                return

            await member.ban(
                reason=reason,
                delete_message_seconds=self._prune_seconds(),
            )
            action_finished = True
            await message.channel.send(
                (
                    "**ĐOÀNG ĐOÀNG ĐOÀNG**\n"
                    f"Đã thủ tiêu {member_callsign} ({member.mention}).\n"
                    f"Mã định danh: `{member.id}`."
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            self.logger.exception(
                "Bảo vệ Area 51 bị từ chối quyền ban %s (%s).",
                member,
                member.id,
            )
        except discord.HTTPException:
            self.logger.exception(
                "Bảo vệ Area 51 ban thất bại %s (%s).",
                member,
                member.id,
            )
        finally:
            if view.cancelled_by is None and not action_finished:
                await self._finish_cancel_prompt(
                    prompt,
                    (
                        "**THỰC THI AREA 51 THẤT BẠI**\n"
                        f"Không thể ban {member_callsign} ({member.mention}).\n"
                        f"Mã định danh: `{member.id}`."
                    ),
                    view,
                )
            self._pending_bans.discard(member.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Area51GuardCog(bot))
