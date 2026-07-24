import unittest

from cogs.utils._big_speaker_helpers import (
    AMOUNT_TO_SIZE,
    ALLOWED_AMOUNTS,
    amount_to_text_size,
    clean_message,
    format_amount_guide,
    format_big_speaker,
    resolve_speaker_tier,
    sanitize_mentions,
    validate_amount,
)


class TestAmountToSize(unittest.TestCase):
    def test_all_fixed_amounts_map_to_sizes_1_through_6(self):
        expected = {1: 1, 2: 2, 5: 3, 10: 4, 20: 5, 50: 6}
        self.assertEqual(AMOUNT_TO_SIZE, expected)
        self.assertEqual(ALLOWED_AMOUNTS, frozenset(expected))
        for amount, size in expected.items():
            self.assertEqual(amount_to_text_size(amount), size)
            tier = resolve_speaker_tier(amount)
            self.assertEqual(tier.amount, amount)
            self.assertEqual(tier.text_size, size)

    def test_invalid_amounts_rejected(self):
        for bad in (0, 3, 4, 15, 25, 100, -1):
            with self.assertRaises(ValueError):
                validate_amount(bad)
            with self.assertRaises(ValueError):
                amount_to_text_size(bad)


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
        self.assertEqual(format_big_speaker("hi", 5), "# hi")
        self.assertEqual(format_big_speaker("hi", 6), "# **📢 hi**")

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            format_big_speaker("hi", 0)
        with self.assertRaises(ValueError):
            format_big_speaker("hi", 7)

    def test_guide_lists_all_prices(self):
        guide = format_amount_guide()
        for amount in (1, 2, 5, 10, 20, 50):
            self.assertIn(f"{amount} TC", guide)


if __name__ == "__main__":
    unittest.main()
