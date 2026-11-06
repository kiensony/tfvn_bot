"""Guild-scoped settings. Other features keep using shared global_vars."""

from typing import Any


ANNOUNCEMENT_VARIABLES = frozenset({
    "JOIN_CHANNEL", "RULE_CHANNEL", "ROLE_CHANNEL", "BYE_CHANNEL",
    "WELCOME_GIF_URL", "GOODBYE_GIF_URL", "BANNED_GIF_URL",
})
MRBEAST_SCAM_ALERT_CHANNEL = "MRBEAST_SCAM_ALERT_CHANNEL"
# Keys an administrator can read and write for the current guild.
GUILD_SETTING_VARIABLES = ANNOUNCEMENT_VARIABLES | frozenset({
    MRBEAST_SCAM_ALERT_CHANNEL,
})


def valid_guild_id(guild_id: object) -> bool:
    return isinstance(guild_id, int) and not isinstance(guild_id, bool) and guild_id > 0


def normalize_guild_setting_value(name: str, value: object) -> str:
    """Return the string stored for one supported guild setting."""
    if name not in GUILD_SETTING_VARIABLES:
        raise ValueError("Only supported guild settings can be saved.")
    if not isinstance(value, str):
        raise ValueError("Guild settings require a nonempty string.")
    stripped = value.strip()
    if not stripped:
        raise ValueError("Guild settings require a nonempty string.")
    if name != MRBEAST_SCAM_ALERT_CHANNEL:
        return stripped
    channel_text = stripped
    if channel_text.startswith("<#") and channel_text.endswith(">"):
        channel_text = channel_text[2:-1]
    if not channel_text.isdigit():
        raise ValueError("MRBEAST_SCAM_ALERT_CHANNEL requires a channel ID.")
    channel_id = int(channel_text)
    if channel_id <= 0:
        raise ValueError("MRBEAST_SCAM_ALERT_CHANNEL requires a channel ID.")
    return str(channel_id)


def get_guild_variables(bot: object, guild_id: int) -> dict[str, str]:
    """Read this guild's settings without falling back to shared values."""
    if not valid_guild_id(guild_id):
        return {}
    variables = getattr(bot, "guild_vars", None)
    if not isinstance(variables, dict):
        return {}
    guild_variables = variables.get(guild_id)
    if not isinstance(guild_variables, dict):
        return {}
    return {name: value for name, value in guild_variables.items()
            if name in GUILD_SETTING_VARIABLES}


def get_guild_variable(
    bot: object, guild_id: int, name: str, default: Any = None
) -> Any:
    return get_guild_variables(bot, guild_id).get(name, default)


def ensure_guild_variable_index(collection: Any) -> None:
    """Keep new settings unique while leaving unassigned legacy rows intact."""
    collection.create_index(
        [("guild_id", 1), ("name", 1)],
        name="guild_variable_name_unique",
        unique=True,
        partialFilterExpression={"guild_id": {"$type": "number"}},
    )
