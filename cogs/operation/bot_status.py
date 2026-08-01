import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

STATUS_FILE = Path(__file__).resolve().parents[2] / "data" / "bot_activity_funny_status.json"
STREAM_URL = "https://www.twitch.tv/discord"
MIN_ROTATION_SECONDS = 5 * 60
MAX_ROTATION_SECONDS = 15 * 60
VALID_ACTIVITY_TYPES = {
    "CUSTOM",
    "PLAYING",
    "WATCHING",
    "LISTENING",
    "STREAMING",
    "COMPETING",
}


def load_statuses(path: Path = STATUS_FILE) -> list[dict[str, str]]:
    """Load valid activity entries from the bot status data file."""
    with path.open("r", encoding="utf-8") as status_file:
        payload: Any = json.load(status_file)

    if not isinstance(payload, dict) or not isinstance(payload.get("bot_statuses"), list):
        raise ValueError("bot_statuses must be a JSON array")

    statuses: list[dict[str, str]] = []
    for entry in payload["bot_statuses"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("type"), str)
            or entry["type"].upper() not in VALID_ACTIVITY_TYPES
        ):
            continue

        activity_type = entry["type"].upper()
        status = {"type": activity_type}
        if activity_type == "CUSTOM":
            if (
                not isinstance(entry.get("think"), str)
                or not entry["think"].strip()
            ):
                continue
            status["think"] = entry["think"].strip()
        else:
            if not isinstance(entry.get("text"), str) or not entry["text"].strip():
                continue
            status["text"] = entry["text"].strip()
        statuses.append(status)

    if not statuses:
        raise ValueError("bot_statuses contains no valid activities")
    return statuses


def make_activity(status: dict[str, str]) -> discord.BaseActivity:
    """Convert a status data entry into a Discord activity."""
    activity_type = status["type"]
    if activity_type == "CUSTOM":
        return discord.CustomActivity(name=status["think"])

    text = status["text"]
    if activity_type == "STREAMING":
        return discord.Streaming(name=text, url=STREAM_URL)

    types = {
        "PLAYING": discord.ActivityType.playing,
        "WATCHING": discord.ActivityType.watching,
        "LISTENING": discord.ActivityType.listening,
        "COMPETING": discord.ActivityType.competing,
    }
    return discord.Activity(type=types[activity_type], name=text)


class BotStatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.statuses = load_statuses()
        self.last_status: dict[str, str] | None = None
        self.rotation_task = asyncio.create_task(self.rotate_statuses())

    def cog_unload(self) -> None:
        self.rotation_task.cancel()

    async def rotate_statuses(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            choices = [status for status in self.statuses if status != self.last_status]
            selected = random.choice(choices or self.statuses)
            try:
                await self.bot.change_presence(activity=make_activity(selected))
            except (discord.ConnectionClosed, OSError, RuntimeError):
                logger.exception("Failed to update the bot activity")
            else:
                self.last_status = selected

            delay = random.uniform(MIN_ROTATION_SECONDS, MAX_ROTATION_SECONDS)
            await asyncio.sleep(delay)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotStatusCog(bot))
