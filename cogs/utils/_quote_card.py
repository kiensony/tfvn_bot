"""Text preparation and PNG rendering for Discord quote cards."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re
from typing import Protocol

import emoji
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


CARD_WIDTH = 1200
CARD_HEIGHT = 675
AVATAR_SIZE = 224
TEXT_AREA_WIDTH = 720
TEXT_AREA_HEIGHT = 340
TEXT_FONT_SIZES = (50, 44, 38, 32)
DEFAULT_ACCENT = (88, 101, 242)
MAX_SOURCE_TEXT_LENGTH = 4_000
MAX_NORMALIZED_TEXT_LENGTH = 1_200

_CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_]{2,32}):\d+>")
_HORIZONTAL_WHITESPACE = re.compile(r"[\t\f\v ]+")
_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_FONT_PATH = _ROOT / "fonts" / "NotoSans-Variable.ttf"

_REGULAR_FONT_CANDIDATES = (
    BUNDLED_FONT_PATH,
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
)
_BOLD_FONT_CANDIDATES = (
    BUNDLED_FONT_PATH,
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
)


class MeasurableFont(Protocol):
    def getlength(self, text: str) -> float: ...


def normalize_quote_text(content: str) -> str:
    """Prepare Discord message text for a readable, bounded quote card."""
    content = content[:MAX_SOURCE_TEXT_LENGTH]
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = _CUSTOM_EMOJI.sub(r":\1:", content)
    content = emoji.demojize(content)

    lines: list[str] = []
    for raw_line in content.split("\n"):
        line = _HORIZONTAL_WHITESPACE.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")

    while lines and not lines[-1]:
        lines.pop()

    normalized = "\n".join(lines)
    if not normalized:
        raise ValueError("Tin nhắn không có nội dung chữ để tạo quote.")
    if len(normalized) > MAX_NORMALIZED_TEXT_LENGTH:
        normalized = (
            normalized[: MAX_NORMALIZED_TEXT_LENGTH - 1].rstrip() + "…"
        )
    return normalized


def _longest_fitting_prefix(
    text: str,
    font: MeasurableFont,
    max_width: int,
) -> int:
    low = 1
    high = len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if font.getlength(text[:middle]) <= max_width:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return max(1, best)


def wrap_quote_text(
    text: str,
    font: MeasurableFont,
    max_width: int,
) -> list[str]:
    """Wrap text by rendered width, including words wider than one line."""
    if max_width <= 0:
        raise ValueError("max_width must be positive")

    wrapped: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            if wrapped and wrapped[-1] != "":
                wrapped.append("")
            continue

        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= max_width:
                current = candidate
                continue

            if current:
                wrapped.append(current)
                current = ""

            fragments: list[str] = []
            fragment = ""
            for character in word:
                candidate = fragment + character
                if fragment and font.getlength(candidate) > max_width:
                    fragments.append(fragment)
                    fragment = character
                else:
                    fragment = candidate
            if fragment:
                fragments.append(fragment)

            if fragments:
                wrapped.extend(fragments[:-1])
                current = fragments[-1]

        if current:
            wrapped.append(current)

    return wrapped or [""]


def _truncate_to_width(
    text: str,
    font: MeasurableFont,
    max_width: int,
    suffix: str = "…",
    *,
    force_suffix: bool = False,
) -> str:
    if not force_suffix and font.getlength(text) <= max_width:
        return text
    if force_suffix and font.getlength(text + suffix) <= max_width:
        return text.rstrip() + suffix

    available = max_width - int(font.getlength(suffix))
    if available <= 0:
        return suffix
    prefix_length = _longest_fitting_prefix(text, font, available)
    return text[:prefix_length].rstrip() + suffix


@lru_cache(maxsize=2)
def _font_path(bold: bool) -> str | None:
    candidates = _BOLD_FONT_CANDIDATES if bold else _REGULAR_FONT_CANDIDATES
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _font_path(bold)
    if path is not None:
        font = ImageFont.truetype(path, size=size)
        if bold:
            try:
                font.set_variation_by_name("Bold")
            except OSError:
                # Static bold font candidates already contain the right weight.
                pass
        return font
    return ImageFont.load_default(size=size)


def _text_block_height(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    spacing: int,
) -> int:
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    box = measure.multiline_textbbox(
        (0, 0),
        "\n".join(lines),
        font=font,
        spacing=spacing,
    )
    return max(1, box[3] - box[1])


def _fit_quote_lines(text: str) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    selected_font = _load_font(TEXT_FONT_SIZES[-1])
    selected_lines: list[str] = []
    selected_spacing = 10

    for size in TEXT_FONT_SIZES:
        font = _load_font(size)
        spacing = max(8, size // 4)
        lines = wrap_quote_text(text, font, TEXT_AREA_WIDTH)
        selected_font = font
        selected_lines = lines
        selected_spacing = spacing
        if _text_block_height(lines, font, spacing) <= TEXT_AREA_HEIGHT:
            return font, lines, spacing

    low = 1
    high = len(selected_lines)
    fitting_line_count = 1
    while low <= high:
        middle = (low + high) // 2
        if (
            _text_block_height(
                selected_lines[:middle],
                selected_font,
                selected_spacing,
            )
            <= TEXT_AREA_HEIGHT
        ):
            fitting_line_count = middle
            low = middle + 1
        else:
            high = middle - 1

    was_truncated = fitting_line_count < len(selected_lines)
    selected_lines = selected_lines[:fitting_line_count]

    while len(selected_lines) > 1 and not selected_lines[-1]:
        selected_lines.pop()
        was_truncated = True
    if was_truncated:
        selected_lines[-1] = _truncate_to_width(
            selected_lines[-1],
            selected_font,
            TEXT_AREA_WIDTH,
            force_suffix=True,
        )
    return selected_font, selected_lines, selected_spacing


def _mix(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    return tuple(
        round(first[index] * (1 - ratio) + second[index] * ratio)
        for index in range(3)
    )


def _safe_accent(accent_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    accent = tuple(max(0, min(255, int(value))) for value in accent_rgb)
    if sum(accent) < 90:
        accent = DEFAULT_ACCENT
    luminance = (
        0.2126 * accent[0]
        + 0.7152 * accent[1]
        + 0.0722 * accent[2]
    )
    if luminance < 135:
        lighten_by = (135 - luminance) / (255 - luminance)
        accent = _mix(accent, (255, 255, 255), lighten_by)
    return accent


def _draw_background(
    card: Image.Image,
    accent: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(card)
    start = _mix((20, 23, 34), accent, 0.18)
    end = (8, 10, 17)
    for y in range(CARD_HEIGHT):
        ratio = y / (CARD_HEIGHT - 1)
        color = _mix(start, end, ratio)
        draw.line((0, y, CARD_WIDTH, y), fill=color)

    draw.rounded_rectangle(
        (328, 48, 1140, 627),
        radius=34,
        fill=(20, 23, 34),
        outline=_mix((65, 70, 88), accent, 0.18),
        width=2,
    )
    draw.rounded_rectangle(
        (328, 48, 338, 627),
        radius=5,
        fill=accent,
    )


def _placeholder_avatar(
    display_name: str,
    accent: tuple[int, int, int],
) -> Image.Image:
    avatar = Image.new(
        "RGB",
        (AVATAR_SIZE, AVATAR_SIZE),
        _mix((30, 33, 46), accent, 0.55),
    )
    draw = ImageDraw.Draw(avatar)
    initial = next(
        (
            character.upper()
            for character in display_name
            if character.isalnum()
        ),
        "?",
    )
    font = _load_font(88, bold=True)
    box = draw.textbbox((0, 0), initial, font=font)
    x = (AVATAR_SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (AVATAR_SIZE - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), initial, font=font, fill=(248, 249, 255))
    return avatar


def _prepare_avatar(
    avatar_bytes: bytes | None,
    display_name: str,
    accent: tuple[int, int, int],
) -> Image.Image:
    if avatar_bytes:
        try:
            with Image.open(BytesIO(avatar_bytes)) as source:
                return ImageOps.fit(
                    source.convert("RGB"),
                    (AVATAR_SIZE, AVATAR_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
        except (OSError, ValueError, UnidentifiedImageError):
            pass
    return _placeholder_avatar(display_name, accent)


def _single_line(value: str, fallback: str) -> str:
    value = emoji.demojize(value.replace("\n", " "))
    normalized = _HORIZONTAL_WHITESPACE.sub(" ", value).strip()
    return normalized or fallback


def render_quote_card(
    *,
    avatar_bytes: bytes | None,
    display_name: str,
    username: str,
    quote_text: str,
    context_label: str,
    accent_rgb: tuple[int, int, int] = DEFAULT_ACCENT,
) -> bytes:
    """Render one fixed-size PNG quote card and return its bytes."""
    text = normalize_quote_text(quote_text)
    display_name = _single_line(display_name, "Discord user")
    username = _single_line(username, display_name)
    context_label = _single_line(context_label, "Discord")
    accent = _safe_accent(accent_rgb)

    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT))
    _draw_background(card, accent)
    draw = ImageDraw.Draw(card)

    avatar = _prepare_avatar(avatar_bytes, display_name, accent)
    avatar_x = 68
    avatar_y = (CARD_HEIGHT - AVATAR_SIZE) // 2
    border = 7
    draw.ellipse(
        (
            avatar_x - border,
            avatar_y - border,
            avatar_x + AVATAR_SIZE + border,
            avatar_y + AVATAR_SIZE + border,
        ),
        fill=accent,
    )
    mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR_SIZE, AVATAR_SIZE), fill=255)
    card.paste(avatar, (avatar_x, avatar_y), mask)

    quote_font, lines, spacing = _fit_quote_lines(text)
    quote_block = "\n".join(lines)
    quote_box = draw.multiline_textbbox(
        (0, 0),
        quote_block,
        font=quote_font,
        spacing=spacing,
    )
    block_height = quote_box[3] - quote_box[1]
    quote_y = 88 + max(0, (TEXT_AREA_HEIGHT - block_height) // 2)

    mark_font = _load_font(96, bold=True)
    draw.text((356, 42), "“", font=mark_font, fill=_mix((70, 75, 94), accent, 0.45))
    draw.multiline_text(
        (382, quote_y),
        quote_block,
        font=quote_font,
        fill=(244, 245, 250),
        spacing=spacing,
    )

    name_font = _load_font(38, bold=True)
    username_font = _load_font(25)
    context_font = _load_font(22)
    safe_name = _truncate_to_width(
        display_name,
        name_font,
        TEXT_AREA_WIDTH,
    )
    safe_username = _truncate_to_width(
        f"@{username}",
        username_font,
        TEXT_AREA_WIDTH,
    )
    safe_context = _truncate_to_width(
        context_label,
        context_font,
        TEXT_AREA_WIDTH,
    )

    draw.text((382, 493), safe_name, font=name_font, fill=accent)
    draw.text((382, 540), safe_username, font=username_font, fill=(174, 179, 195))
    draw.text((382, 586), safe_context, font=context_font, fill=(126, 132, 151))

    output = BytesIO()
    card.save(output, format="PNG", optimize=True)
    return output.getvalue()
