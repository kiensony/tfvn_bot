import asyncio
import random
import json
import os
import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]

class VietnameseKingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Load the configuration for the channel
        channel_var = self.bot.global_vars.get("VIETNAMESE_KING_GAMES_CHANNELS")
        if not channel_var:
            raise ValueError("VIETNAMESE_KING_GAMES_CHANNELS is not set in global variables.")

        if not isinstance(channel_var, list):
            channel_var = [channel_var]
        self.VIETNAMESE_KING_GAMES_CHANNELS = [str(channel_id) for channel_id in channel_var]
        self.db = bot.db
        
        # Load the vietnamese king data
        data_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'vietnamese_king_data.json')
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                self.words_data = json.load(f)
        except Exception as e:
            print(f"Failed to load vietnamese_king_data.json: {e}")
            self.words_data = []

        self.current_word = None
        self.current_standardized_word = None
        self.scrambled_letters = None
        self.revealed_indices = []
        self.round_lock = asyncio.Lock()

        # Try to restore context
        self._load_context()

    def _load_context(self):
        record = self.db["context"].find_one({"context_type": "vietnamese_king"})
        if record:
            self.current_word = record.get("current_word")
            self.current_standardized_word = (
                record.get("current_standardized_word")
                or self._normalize_old_tone((self.current_word or "").lower().strip())
            )
            self.scrambled_letters = record.get("scrambled_letters")
            self.revealed_indices = record.get("revealed_indices", [])
        else:
            self._start_new_round()

    def _save_context(self):
        doc = {
            "context_type": "vietnamese_king",
            "current_word": self.current_word,
            "current_standardized_word": self.current_standardized_word,
            "scrambled_letters": self.scrambled_letters,
            "revealed_indices": self.revealed_indices,
        }
        self.db["context"].update_one(
            {"context_type": "vietnamese_king"},
            {"$set": doc},
            upsert=True,
        )

    def _clear_context(self):
        self.db["context"].delete_many({"context_type": "vietnamese_king"})
        self.current_word = None
        self.current_standardized_word = None
        self.scrambled_letters = None
        self.revealed_indices = []

    def _is_vietnamese_king_channel(self, channel_id: int) -> bool:
        return str(channel_id) in self.VIETNAMESE_KING_GAMES_CHANNELS

    def _normalize_old_tone(self, s: str) -> str:
        replacements = {
            "oà": "òa", "oá": "óa", "oả": "ỏa", "oã": "õa", "oạ": "ọa",
            "oè": "òe", "oé": "óe", "oẻ": "ỏe", "oẽ": "õe", "oẹ": "ọe",
            "uà": "ùa", "uá": "úa", "uả": "ủa", "uã": "ũa", "uạ": "ụa",
            "ưà": "ừa", "ưá": "ứa", "ưả": "ửa", "ưã": "ữa", "ưạ": "ựa",
            "ườ": "ường", "ướ": "ướ", "ưở": "ưởng", "ưỡ": "ưỡng", "ượ": "ượng",
            "ià": "ìa", "iá": "ía", "iả": "ỉa", "iã": "ĩa", "ịa": "ịa",
            "yà": "ỳa", "yá": "ýa", "yả": "ỷa", "yã": "ỹa", "yạ": "ỵa",
            "uỳ": "ùy", "uý": "úy", "uỷ": "ủy", "uỹ": "ũy", "ụy": "ụy",
            "uồ": "uồ", "uố": "uố", "uổ": "uổ", "uỗ": "uỗ", "uộ": "uộ",
        }

        for old, new in replacements.items():
            s = s.replace(old, new)

        return s.replace("quì", "quỳ").strip()

    def _word_structure(self) -> str:
        structure = []
        for i, c in enumerate(self.current_word or ""):
            if c == " " or c == "-":
                structure.append(c)
            elif i in self.revealed_indices:
                structure.append(c.upper())
            else:
                structure.append("_")

        return "".join(structure)

    def _round_message(self, puzzle_label: str) -> str:
        return (
            "👑 **VUA TIẾNG VIỆT** 👑\n"
            f"🧩 Cấu trúc: `{self._word_structure()}`\n"
            f"🔠 {puzzle_label}: **{self.scrambled_letters}**"
        )

    async def _is_command_message(self, message: discord.Message) -> bool:
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return True

        content = message.content.strip()
        if not content:
            return False

        prefix = self.bot.command_prefix
        if isinstance(prefix, str) and content.startswith(prefix.strip()):
            return True
        if isinstance(prefix, (list, tuple)) and any(content.startswith(str(p).strip()) for p in prefix):
            return True

        command_name = content.split()[0].lower()
        return command_name in self.bot.all_commands

    def _start_new_round(self):
        if not self.words_data:
            return

        while True:
            choice = random.choice(self.words_data)
            word = choice["word"]
            
            # Use rules to find a decent word to scramble (e.g. space_count > 0 for phrases, or word_len > 4)
            if choice.get("word_len", 0) >= 3:
                self.current_word = word
                self.current_standardized_word = choice.get("standardize") or self._normalize_old_tone(word.lower().strip())
                self.revealed_indices = []
                # Scramble characters, ignoring spaces for simple shuffling?
                # Actually, in Vua Tiếng Việt they scramble the phrase's letters.
                characters = list(word.replace(" ", "").replace("-", ""))
                
                # Make sure it's actually scrambled
                scrambled = characters[:]
                attempts = 0
                while scrambled == characters and attempts < 10:
                    random.shuffle(scrambled)
                    attempts += 1
                
                self.scrambled_letters = " ".join(scrambled).upper()
                self._save_context()
                break

    @commands.group(name="vtv", invoke_without_command=True)
    async def vtv(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return
            
        embed = discord.Embed(
            title="👑 VUA TIẾNG VIỆT",
            description="Luật chơi: Hãy sắp xếp lại các chữ cái để tạo thành từ/cụm từ đúng!",
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Gõ trực tiếp từ bạn đoán vào kênh này.")
        await ctx.send(embed=embed)
        
        if self.scrambled_letters:
            await ctx.send(
                f"🧩 Cấu trúc: `{self._word_structure()}`\n"
                f"🔠 Câu đố hiện tại: **{self.scrambled_letters}**"
            )

    @vtv.command(name="status")
    async def vtv_status(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return
            
        if self.scrambled_letters:
            embed = discord.Embed(title="👑 VUA TIẾNG VIỆT - TRẠNG THÁI", color=discord.Color.blue())
            embed.add_field(name="🧩 Cấu trúc", value=f"`{self._word_structure()}`", inline=False)
            embed.add_field(name="🔠 Câu đố", value=f"**{self.scrambled_letters}**", inline=False)
            embed.set_footer(text="Hãy sắp xếp lại các chữ cái để tạo thành từ đúng!")
            await ctx.send(embed=embed)
        else:
            await ctx.send("Chưa có lượt chơi nào đang diễn ra. Dùng `!vtv next` để bắt đầu!")

    @vtv.command(name="next")
    async def vtv_next(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return
            
        self._start_new_round()
        if self.scrambled_letters:
            await ctx.send(self._round_message("Câu đố mới"))
        else:
            await ctx.send("Không thể bắt đầu câu đố mới do chưa tải được dữ liệu.")

    @vtv.command(name="hint")
    async def vtv_hint(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return
            
        if not self.current_word:
            await ctx.send("Chưa có lượt chơi nào diễn ra.")
            return

        # Hint: reveal the structure of spaces/words with one stacked letter revealed
        chars = list(self.current_word)
        valid_indices = [i for i, c in enumerate(chars) if c != " " and c != "-"]
        
        unrevealed = [i for i in valid_indices if i not in self.revealed_indices]
        
        if unrevealed:
            reveal_idx = random.choice(unrevealed)
            self.revealed_indices.append(reveal_idx)
            self._save_context()
            
        word_structure = self._word_structure()
        
        remaining_hidden = len([i for i in valid_indices if i not in self.revealed_indices])
        if remaining_hidden <= 1:
            answer = self.current_word
            await ctx.send(
                f"💡 Gợi ý: Cấu trúc từ: `{word_structure}`\n"
                f"⌛ Hết lượt gợi ý! Không ai chiến thắng. Đáp án là: **{answer}**"
            )

            self._start_new_round()
            if self.scrambled_letters:
                await ctx.send(self._round_message("Câu đố mới"))
            else:
                await ctx.send("Không thể bắt đầu câu đố mới do chưa tải được dữ liệu.")
        elif unrevealed:
            await ctx.send(f"💡 Gợi ý: Cấu trúc từ: `{word_structure}`")
        else:
            await ctx.send(f"💡 Đã lật hết các chữ cái: `{word_structure}`")

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        if not self._is_vietnamese_king_channel(message.channel.id):
            return
            
        # Ignore commands and command-like text in the game channel.
        if await self._is_command_message(message):
            return

        if not self.current_word:
            return

        guess = self._normalize_old_tone(message.content.lower().strip())

        async with self.round_lock:
            answer_key = self.current_standardized_word or self._normalize_old_tone(self.current_word.lower().strip())
            is_correct = guess == answer_key
            if is_correct:
                answer = self.current_word
                self._start_new_round()
                has_next_round = bool(self.scrambled_letters)
                next_round_message = self._round_message("Câu đố mới")

        if not is_correct:
            await message.add_reaction("❌")
            return

        await message.add_reaction("✅")
        await message.reply(f"🎉 Chúc mừng bạn đã giải đúng! Đáp án là: **{answer}**")

        if has_next_round:
            await message.channel.send(next_round_message)
        else:
            await message.channel.send("Không thể bắt đầu câu đố mới do chưa tải được dữ liệu.")

async def setup(bot):
    await bot.add_cog(VietnameseKingCog(bot))
