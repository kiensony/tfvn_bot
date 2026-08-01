import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from cogs.utils._quote_card import (
    BUNDLED_FONT_PATH,
    CARD_HEIGHT,
    CARD_WIDTH,
    MAX_NORMALIZED_TEXT_LENGTH,
    TEXT_AREA_HEIGHT,
    _truncate_to_width,
    _fit_quote_lines,
    _font_path,
    _load_font,
    _safe_accent,
    _text_block_height,
    normalize_quote_text,
    render_quote_card,
    wrap_quote_text,
)
from cogs.utils.quote import QuoteCog, QuoteLookupError


class FixedWidthFont:
    def getlength(self, text: str) -> float:
        return float(len(text))


class TestQuoteText(unittest.TestCase):
    def test_normalizes_whitespace_and_custom_emoji(self):
        self.assertEqual(
            normalize_quote_text(
                "  Xin   chào ✨  \n\n<a:waving:123456>  bạn "
            ),
            "Xin chào :sparkles:\n\n:waving: bạn",
        )

    def test_rejects_empty_text(self):
        for content in ("", "   ", "\n\t\n"):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    normalize_quote_text(content)

    def test_wraps_words_and_long_tokens(self):
        font = FixedWidthFont()
        self.assertEqual(
            wrap_quote_text("one two three", font, 7),
            ["one two", "three"],
        )
        self.assertEqual(
            wrap_quote_text("abcdefgh", font, 3),
            ["abc", "def", "gh"],
        )

    def test_preserves_one_blank_line_between_paragraphs(self):
        lines = wrap_quote_text("first\n\nsecond", FixedWidthFont(), 20)
        self.assertEqual(lines, ["first", "", "second"])

    def test_width_truncation_only_adds_ellipsis_when_needed(self):
        font = FixedWidthFont()
        self.assertEqual(_truncate_to_width("short", font, 10), "short")
        self.assertEqual(_truncate_to_width("too long", font, 5), "too…")
        self.assertEqual(
            _truncate_to_width("short", font, 10, force_suffix=True),
            "short…",
        )

    def test_bundled_font_is_present(self):
        self.assertTrue(BUNDLED_FONT_PATH.is_file())
        self.assertEqual(_font_path(False), str(BUNDLED_FONT_PATH))
        self.assertEqual(_font_path(True), str(BUNDLED_FONT_PATH))
        self.assertEqual(_load_font(24, bold=True).getname()[1], "Bold")

    def test_normalized_text_is_bounded(self):
        normalized = normalize_quote_text("😀" * 4_000)
        self.assertLessEqual(len(normalized), MAX_NORMALIZED_TEXT_LENGTH)
        self.assertTrue(normalized.endswith("…"))

    def test_long_quote_fits_text_area(self):
        text = normalize_quote_text("từ " * 2_000)
        font, lines, spacing = _fit_quote_lines(text)
        self.assertLessEqual(
            _text_block_height(lines, font, spacing),
            TEXT_AREA_HEIGHT,
        )
        self.assertTrue(lines[-1].endswith("…"))

    def test_dark_role_color_is_lightened_for_contrast(self):
        red, green, blue = _safe_accent((40, 50, 70))
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        self.assertGreaterEqual(luminance, 134)


class TestQuoteCardRendering(unittest.TestCase):
    def test_renders_png_with_expected_dimensions(self):
        avatar_buffer = BytesIO()
        Image.new("RGB", (64, 64), (220, 80, 120)).save(
            avatar_buffer,
            format="PNG",
        )

        output = render_quote_card(
            avatar_bytes=avatar_buffer.getvalue(),
            display_name="Người dùng thử nghiệm",
            username="tester",
            quote_text="Xin chào mọi người! Đây là một câu quote tiếng Việt.",
            context_label="#general • 01/08/2026 10:00 UTC",
            accent_rgb=(120, 90, 220),
        )

        with Image.open(BytesIO(output)) as card:
            self.assertEqual(card.format, "PNG")
            self.assertEqual(card.size, (CARD_WIDTH, CARD_HEIGHT))

    def test_missing_avatar_uses_placeholder(self):
        output = render_quote_card(
            avatar_bytes=None,
            display_name="Kien",
            username="kien",
            quote_text="A short quote.",
            context_label="#general",
        )
        self.assertTrue(output.startswith(b"\x89PNG\r\n\x1a\n"))


class TestQuoteAvatarSelection(unittest.TestCase):
    def test_prefers_server_avatar(self):
        server_avatar = object()
        display_avatar = object()
        author = SimpleNamespace(
            guild_avatar=server_avatar,
            display_avatar=display_avatar,
        )
        self.assertIs(QuoteCog._server_avatar(author), server_avatar)

    def test_falls_back_to_display_avatar(self):
        display_avatar = object()
        author = SimpleNamespace(
            guild_avatar=None,
            display_avatar=display_avatar,
        )
        self.assertIs(QuoteCog._server_avatar(author), display_avatar)


class TestQuoteMessageResolution(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = QuoteCog(SimpleNamespace())
        self.guild = SimpleNamespace(id=10)
        self.channel = SimpleNamespace(
            id=20,
            fetch_message=AsyncMock(),
        )

    def _context(self, reference=None):
        return SimpleNamespace(
            message=SimpleNamespace(reference=reference),
            guild=self.guild,
            channel=self.channel,
            prefix="!tf ",
        )

    def _message(self, *, guild=None, channel=None):
        return SimpleNamespace(
            guild=guild or self.guild,
            channel=channel or self.channel,
        )

    async def test_resolves_cached_replied_message(self):
        message = self._message()
        reference = SimpleNamespace(
            message_id=30,
            channel_id=self.channel.id,
            resolved=None,
            cached_message=message,
        )
        result = await self.cog._resolve_message(
            self._context(reference),
            None,
        )
        self.assertIs(result, message)

    async def test_resolves_explicit_message_reference(self):
        message = self._message()
        self.channel.fetch_message.return_value = message
        result = await self.cog._resolve_message(
            self._context(),
            "123456789012345678",
        )
        self.assertIs(result, message)
        self.channel.fetch_message.assert_awaited_once_with(
            123456789012345678
        )

    async def test_rejects_cross_channel_link_before_fetch(self):
        reference = (
            "https://discord.com/channels/10/21/123456789012345678"
        )
        with self.assertRaisesRegex(QuoteLookupError, "kênh hiện tại"):
            await self.cog._resolve_message(self._context(), reference)
        self.channel.fetch_message.assert_not_awaited()

    async def test_requires_reply_or_explicit_reference(self):
        with self.assertRaisesRegex(QuoteLookupError, "reply"):
            await self.cog._resolve_message(self._context(), None)

    async def test_rejects_message_from_another_channel(self):
        other_channel = SimpleNamespace(id=21)
        message = self._message(channel=other_channel)
        reference = SimpleNamespace(
            message_id=30,
            channel_id=self.channel.id,
            resolved=None,
            cached_message=message,
        )
        with self.assertRaisesRegex(QuoteLookupError, "kênh hiện tại"):
            await self.cog._resolve_message(
                self._context(reference),
                None,
            )


if __name__ == "__main__":
    unittest.main()
