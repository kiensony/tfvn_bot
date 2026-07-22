from typing import TypeVar

from discord.ext import commands


BETA_FUNCTION_MARKER = "beta_function"
BETA_ROLE_IDS_SETTING = "BETA_ROLE_IDS"

T = TypeVar("T")


class BetaFunctionError(commands.CheckFailure):
    """Expected access denial raised by a beta-function command check."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def get_beta_role_ids(bot: object) -> set[int]:
    """Return Beta role IDs configured in MongoDB global variables."""
    global_vars = getattr(bot, "global_vars", None)
    raw_values = (
        global_vars.get(BETA_ROLE_IDS_SETTING)
        if isinstance(global_vars, dict)
        else None
    )
    values = (
        raw_values
        if isinstance(raw_values, (list, tuple, set))
        else (raw_values,)
    )
    role_ids = set()
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        for token in str(value).replace(",", " ").split():
            try:
                role_id = int(token)
            except (TypeError, ValueError):
                continue
            if role_id > 0:
                role_ids.add(role_id)
    return role_ids


def beta_access_denial(ctx: commands.Context) -> str | None:
    """Return a safe user-facing denial reason, or None when access is allowed."""
    if ctx.guild is None:
        return "🧪 Lệnh Beta chỉ có thể dùng trong server."

    role_ids = get_beta_role_ids(ctx.bot)
    if not role_ids:
        return (
            "🧪 Beta role chưa được cấu hình. Moderator cần đặt "
            "`BETA_ROLE_IDS`."
        )

    get_role = getattr(ctx.guild, "get_role", None)
    valid_role_ids = (
        {role_id for role_id in role_ids if get_role(role_id) is not None}
        if callable(get_role)
        else role_ids
    )
    if not valid_role_ids:
        return "🧪 `BETA_ROLE_IDS` không chứa role hợp lệ trong server này."

    author_role_ids = {
        getattr(role, "id", None) for role in getattr(ctx.author, "roles", ())
    }
    if valid_role_ids & author_role_ids:
        return None
    return "🧪 Bạn cần một trong các Beta role để sử dụng lệnh này."


async def beta_function_check(ctx: commands.Context) -> bool:
    """Discord command predicate used by BetaFunction."""
    denial = beta_access_denial(ctx)
    if denial is not None:
        raise BetaFunctionError(denial)
    return True


def BetaFunction(target: T) -> T:
    """Mark a command as Beta and require any configured Beta role."""
    decorated = commands.check(beta_function_check)(target)
    if isinstance(decorated, commands.Command):
        decorated.extras[BETA_FUNCTION_MARKER] = True
        setattr(decorated.callback, "__beta_function__", True)
    else:
        setattr(decorated, "__beta_function__", True)
    return decorated


def is_beta_function(target: object) -> bool:
    """Return whether a callback or Discord command has BetaFunction metadata."""
    if isinstance(target, commands.Command):
        return bool(target.extras.get(BETA_FUNCTION_MARKER)) or bool(
            getattr(target.callback, "__beta_function__", False)
        )
    return bool(getattr(target, "__beta_function__", False))
