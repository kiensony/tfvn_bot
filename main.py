import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

import db
from cogs._beta_function import BetaFunctionError
from cogs._feature_flags import cog_disabled
from cogs.operation._lifecycle import BotLifecycleRecorder
from dataloader import DataLoader

load_dotenv()
environment = os.getenv("ENVIRONMENT", "production").strip().lower()
token = os.getenv("DISCORD_TOKEN")

COG_PROFILE_FILES = {
    "development": "dev_cogs.txt",
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=os.getenv("COMMAND_PREFIX", "!tf "),
    intents=intents,
    help_command=None,
)

bot.db = db.db
bot.environment = environment
bot.lifecycle_recorder = BotLifecycleRecorder(bot.db, environment)
bot.CONTENT_VERIFICATION_KEYS_JSON = os.getenv(
    "CONTENT_VERIFICATION_KEYS_JSON"
)
bot.CONTENT_VERIFICATION_ACTIVE_KEY_ID = os.getenv(
    "CONTENT_VERIFICATION_ACTIVE_KEY_ID"
)

# inject environment variables to all class
# TODO: inject environment variables to all class for better practice
bot.WORD_CONNECT_GAMES_CHANNELS = os.getenv(
    "WORD_CONNECT_GAMES_CHANNELS", ""
).split(",")

# Load static data files once during startup.
# Load banned words globally
loader = DataLoader(base_path="data")

bot.BANNED_WORDS = loader.load_lines("banned_word_list.txt")
bot.WORD_CONNECT_WORDS = loader.load_lines("word_connect_valid_list.txt")
bot.FAKE_LOADING_SENTENCES = loader.load_lines("fake_loading_sentences.txt")
bot.FEMBOY_ROLE = loader.load_lines("femboy_role.txt")


def configure_logging() -> None:
    """Configure file logging when writable and always retain console output."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.insert(
            0,
            logging.FileHandler(
                filename="bot.log",
                encoding="utf-8",
                mode="a",
            ),
        )
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers)


@bot.event
async def on_ready() -> None:
    bot.lifecycle_recorder.capture_ready(len(bot.guilds))
    print(f"✅ Bot is ready! Environment: {environment}")


@bot.event
async def on_resumed() -> None:
    bot.lifecycle_recorder.capture_resumed(len(bot.guilds))


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    await bot.process_commands(message)


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(error, BetaFunctionError):
        await ctx.send(
            error.user_message,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    await commands.Bot.on_command_error(bot, ctx, error)


def get_cogs_from_path(base_path: str) -> list[str]:
    """Helper to get all cog modules from a path (supports wildcards)."""
    cogs: list[str] = []
    if os.path.isdir(base_path):
        for root, _, files in os.walk(base_path):
            for file in files:
                if file.endswith(".py") and not file.startswith("_"):
                    path = os.path.join(root, file)
                    module = (
                        path.replace("\\", ".")
                        .replace("/", ".")
                        .removesuffix(".py")
                    )
                    cogs.append(module)
    return cogs


def get_cogs_from_profile(profile_path: str) -> list[str]:
    """Load explicit modules and wildcard directories from a profile file."""
    cogs: list[str] = []
    with open(profile_path, "r", encoding="utf-8") as profile:
        for line in profile:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if entry.endswith(".*"):
                base_path = entry[:-2].replace(".", "/")
                cogs.extend(get_cogs_from_path(base_path))
            else:
                cogs.append(entry)
    return list(dict.fromkeys(cogs))


async def load_cogs() -> None:
    profile_path = COG_PROFILE_FILES.get(environment)
    if profile_path:
        if os.path.exists(profile_path):
            cogs_to_load = get_cogs_from_profile(profile_path)
        else:
            print(
                f"❌ {profile_path} not found. No cogs loaded in "
                f"{environment} mode."
            )
            return
    else:
        # Production: Load all cogs from cogs directory
        cogs_to_load = get_cogs_from_path("cogs")

    disabled = [module for module in cogs_to_load if cog_disabled(module)]
    cogs_to_load = [
        module for module in cogs_to_load if not cog_disabled(module)
    ]
    for module in disabled:
        print(f"⏭️ Disabled cog: {module}")

    settings_cog = "cogs.settings.variable_setting"

    # Prioritize loading the settings cog first
    if settings_cog in cogs_to_load:
        cogs_to_load.remove(settings_cog)
        cogs_to_load.insert(0, settings_cog)  # Insert at the beginning

    for module in cogs_to_load:
        try:
            await bot.load_extension(module)
            print(f"✅ Loaded cog: {module}")
        except Exception as exc:
            print(f"❌ Failed to load cog {module}: {exc}")


async def main() -> None:
    configure_logging()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")
    await bot.lifecycle_recorder.start()
    try:
        async with bot:
            await load_cogs()
            await bot.start(token)
    finally:
        await bot.lifecycle_recorder.close()


if __name__ == "__main__":
    asyncio.run(main())
