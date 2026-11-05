import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import discord


HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
GRADIENT_ROLE_FEATURE = "ENHANCED_ROLE_COLORS"
GRADIENT_FALLBACK_WARNING = (
    "Server chưa mở màu gradient. Đã dùng màu chính."
)
logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class RoleColorSpec:
    primary: discord.Color
    secondary: discord.Color | None = None

    @property
    def is_gradient(self) -> bool:
        return self.secondary is not None

    def create_kwargs(self) -> dict[str, discord.Color | None]:
        kwargs: dict[str, discord.Color | None] = {"colour": self.primary}
        if self.secondary is not None:
            kwargs["secondary_color"] = self.secondary
            kwargs["tertiary_color"] = None
        return kwargs

    def edit_kwargs(self) -> dict[str, discord.Color | None]:
        return {
            "colour": self.primary,
            "secondary_color": self.secondary,
            "tertiary_color": None,
        }

    def record_fields(self) -> dict[str, int | None]:
        return {
            "primary_color": self.primary.value,
            "secondary_color": (
                self.secondary.value if self.secondary is not None else None
            ),
        }


def gradient_colors_enabled(guild: object) -> bool:
    """Return whether the guild can store a gradient role color.

    A missing ``features`` list means the cache is unknown, so the caller
    still attempts the gradient and falls back if Discord rejects it.
    """
    features = getattr(guild, "features", None)
    if not isinstance(features, (list, tuple, set, frozenset)):
        return True
    return GRADIENT_ROLE_FEATURE in features


def role_color_payload(
    color_spec: RoleColorSpec,
    *,
    allow_gradient: bool,
    edit: bool,
) -> dict[str, discord.Color | None]:
    if not allow_gradient:
        return {"colour": color_spec.primary}
    if edit:
        return color_spec.edit_kwargs()
    return color_spec.create_kwargs()


async def apply_role_colors(
    color_spec: RoleColorSpec,
    guild: object,
    operation: Callable[..., Awaitable[T]],
) -> tuple[T, RoleColorSpec, str | None]:
    """Create or edit a role color, using the primary color when a gradient is unavailable.

    ``operation`` is awaited as ``operation(spec, allow_gradient=...)``.
    """
    spec = color_spec
    warning: str | None = None
    allow_gradient = spec.is_gradient or gradient_colors_enabled(guild)
    if spec.is_gradient and not gradient_colors_enabled(guild):
        spec = RoleColorSpec(primary=spec.primary)
        allow_gradient = False
        warning = GRADIENT_FALLBACK_WARNING
    try:
        result = await operation(spec, allow_gradient=allow_gradient)
    except discord.Forbidden:
        if not spec.is_gradient:
            raise
        logger.info(
            "Discord rejected a gradient role color; retrying with the primary color."
        )
        spec = RoleColorSpec(primary=spec.primary)
        result = await operation(spec, allow_gradient=False)
        warning = GRADIENT_FALLBACK_WARNING
    return result, spec, warning


def parse_hex_color(color_text: str) -> discord.Color | None:
    match = HEX_COLOR_RE.fullmatch(color_text.strip())
    if not match:
        return None

    return discord.Color(int(match.group(1), 16))


def _split_inline_color_token(color_text: str) -> list[str]:
    value = color_text.strip()
    for separator in ("->", ",", "/", "|", "-"):
        value = value.replace(separator, " ")
    return value.split()


def parse_role_color_args(
    first_color: str, role_name_text: str
) -> tuple[RoleColorSpec | None, str]:
    color_parts = _split_inline_color_token(first_color)
    if not 1 <= len(color_parts) <= 2:
        return None, role_name_text.strip()

    parsed_colors = []
    for color_part in color_parts:
        color = parse_hex_color(color_part)
        if color is None:
            return None, role_name_text.strip()
        parsed_colors.append(color)

    role_name = role_name_text.strip()
    if len(parsed_colors) == 1:
        role_tokens = role_name.split(maxsplit=1)
        if role_tokens:
            secondary_color = parse_hex_color(role_tokens[0])
            if secondary_color is not None:
                parsed_colors.append(secondary_color)
                role_name = role_tokens[1].strip() if len(role_tokens) > 1 else ""

    return RoleColorSpec(
        primary=parsed_colors[0],
        secondary=parsed_colors[1] if len(parsed_colors) == 2 else None,
    ), role_name
