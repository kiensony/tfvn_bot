import re
from dataclasses import dataclass

import discord


HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


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
