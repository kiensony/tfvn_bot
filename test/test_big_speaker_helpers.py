import unittest

from cogs.utils._big_speaker_helpers import (
    ALLOWED_SIZES,
    SIZE_TO_COST,
    clean_message,
    format_big_speaker,
    format_size_guide,
    resolve_speaker_tier,
    sanitize_mentions,
    validate_text_size,
)


class TestSizeToCost(unittest.TestCase):
    def test_sizes_1_through_6(self):
        expected = {1: 1, 2: 2, 3: 5, 4: 10, 5: 20, 6: 50}
        self.assertEqual(SIZE_TO_COST, expected)
        self.assertEqual(ALLOWED_SIZES, frozenset(expected))
        for size, cost in expected.items():
            tier = resolve_speaker_tier(size)
            self.assertEqual(tier.text_size, size)
            self.assertEqual(tier.cost, cost)

    def test_invalid_sizes_rejected(self):
        for bad in (0, 7, 10, 50, -1):
            with self.assertRaises(ValueError):
                validate_text_size(bad)
            with self.assertRaises(ValueError):
                resolve_speaker_tier(bad)


class TestSanitizeMentions(unittest.TestCase):
    def test_strips_everyone_and_here(self):
        self.assertEqual(
            sanitize_mentions("hello @everyone world"),
            "hello  world",
        )
        self.assertEqual(
            sanitize_mentions("ping @HERE now"),
            "ping  now",
        )

    def test_strips_role_mentions_keeps_user_mentions(self):
        text = "hi <@123> and <@&456> and <@!789>"
        result = sanitize_mentions(text)
        self.assertIn("<@123>", result)
        self.assertIn("<@!789>", result)
        self.assertNotIn("<@&456>", result)
        self.assertNotIn("&456", result)


class TestCleanMessage(unittest.TestCase):
    def test_collapses_whitespace_and_newlines(self):
        self.assertEqual(clean_message("  a \n b\t c  "), "a b c")

    def test_rejects_empty_after_sanitize(self):
        with self.assertRaises(ValueError):
            clean_message("   ")
        with self.assertRaises(ValueError):
            clean_message("@everyone")
        with self.assertRaises(ValueError):
            clean_message("<@&999>")

    def test_rejects_too_long(self):
        with self.assertRaises(ValueError):
            clean_message("x" * 181)

    def test_accepts_max_length(self):
        self.assertEqual(len(clean_message("x" * 180)), 180)

    def test_user_mention_survives_clean(self):
        self.assertEqual(clean_message("yo <@42>"), "yo <@42>")


class TestFormatBigSpeaker(unittest.TestCase):
    def test_size_recipes(self):
        self.assertEqual(format_big_speaker("hi", 1), "### hi")
        self.assertEqual(format_big_speaker("hi", 2), "### **hi**")
        self.assertEqual(format_big_speaker("hi", 3), "## hi")
        self.assertEqual(format_big_speaker("hi", 4), "## **hi**")
        self.assertEqual(
            format_big_speaker("hi", 5),
            "────────────────\n# hi\n────────────────",
        )
        self.assertEqual(
            format_big_speaker("hi", 6),
            "━━━━━━━━━━━━━━━━\n# **hi**\n━━━━━━━━━━━━━━━━",
        )

    def test_small_sizes_have_no_separator(self):
        for size in (1, 2, 3, 4):
            rendered = format_big_speaker("hi", size)
            self.assertNotIn("─", rendered)
            self.assertNotIn("━", rendered)

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            format_big_speaker("hi", 0)
        with self.assertRaises(ValueError):
            format_big_speaker("hi", 7)

    def test_guide_lists_all_sizes_and_costs(self):
        guide = format_size_guide()
        for size, cost in SIZE_TO_COST.items():
            self.assertIn(f"cỡ {size}", guide)
            self.assertIn(f"{cost} TC", guide)


if __name__ == "__main__":
    unittest.main()
