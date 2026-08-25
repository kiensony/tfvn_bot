"""Compatibility entry point for loading the Crocodile Dentist package."""

from __future__ import annotations

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    """Load the cog when a profile names this package instead of its module."""
    from cogs.minigames.crocodile_dentist.crocodile import setup as setup_cog

    await setup_cog(bot)
