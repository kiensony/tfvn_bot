import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cogs.interaction._trigger_reply_helpers import (
    normalize_message_text,
    parse_rule_spec,
    select_matching_rule,
)
from cogs.interaction.triggered_reply import TriggeredReplyCog


class FakeCollection:
    def create_index(self, *args, **kwargs) -> None:
        pass

    def find(self, *args, **kwargs) -> list[dict]:
        return []


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


class TestTriggeredReplyParsing(unittest.TestCase):
    def test_normalize_message_text_is_case_insensitive_and_collapses_spaces(self) -> None:
        self.assertEqual(
            normalize_message_text("  ĐỊT   MẸ VNPT  "),
            "địt mẹ vnpt",
        )

    def test_normalize_message_text_applies_nfkc(self) -> None:
        self.assertEqual(normalize_message_text("Ａ SECRET CODE"), "a secret code")

    def test_parse_contains_rule(self) -> None:
        rule = parse_rule_spec(
            "include",
            " dit me vnpt | vnpt nhu con cac ",
        )

        self.assertEqual(rule["mode"], "contains")
        self.assertEqual(rule["trigger"], "dit me vnpt")
        self.assertEqual(rule["normalized_trigger"], "dit me vnpt")
        self.assertEqual(rule["reply"], "vnpt nhu con cac")

    def test_reply_may_contain_separator(self) -> None:
        rule = parse_rule_spec("exact", "[A SECRET CODE] | left | right")

        self.assertEqual(rule["reply"], "left | right")

    def test_parse_rejects_missing_separator(self) -> None:
        with self.assertRaises(ValueError):
            parse_rule_spec("contains", "trigger without reply")

    def test_parse_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            parse_rule_spec("regex", "trigger | reply")

    def test_parse_rejects_blank_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_rule_spec("exact", " | reply")
        with self.assertRaises(ValueError):
            parse_rule_spec("exact", "trigger |   ")

    def test_parse_rejects_oversized_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_rule_spec("exact", f"{'a' * 201} | reply")
        with self.assertRaises(ValueError):
            parse_rule_spec("exact", f"trigger | {'b' * 2001}")


class TestTriggeredReplyMatching(unittest.TestCase):
    def test_contains_matches_inside_message(self) -> None:
        rule = {
            "rule_id": 1,
            **parse_rule_spec("contains", "dit me vnpt | vnpt nhu con cac"),
        }

        selected = select_matching_rule("nay, DIT   ME VNPT nua roi", [rule])

        self.assertIs(selected, rule)

    def test_exact_rejects_extra_message_text(self) -> None:
        rule = {
            "rule_id": 1,
            **parse_rule_spec("exact", "[A SECRET CODE] | something"),
        }

        self.assertIsNone(
            select_matching_rule("please use [A SECRET CODE]", [rule])
        )
        self.assertIs(select_matching_rule("[a secret code]", [rule]), rule)

    def test_exact_rule_wins_over_contains_rule(self) -> None:
        contains = {
            "rule_id": 1,
            **parse_rule_spec("contains", "secret | broad"),
        }
        exact = {
            "rule_id": 2,
            **parse_rule_spec("exact", "secret | exact"),
        }

        selected = select_matching_rule("secret", [contains, exact])

        self.assertIs(selected, exact)

    def test_longest_contains_rule_wins(self) -> None:
        short = {
            "rule_id": 1,
            **parse_rule_spec("contains", "vnpt | broad"),
        }
        specific = {
            "rule_id": 2,
            **parse_rule_spec("contains", "dit me vnpt | specific"),
        }

        selected = select_matching_rule("dit me vnpt", [short, specific])

        self.assertIs(selected, specific)

    def test_punctuation_and_regex_characters_are_literal(self) -> None:
        rule = {
            "rule_id": 1,
            **parse_rule_spec("contains", "[code].* | literal"),
        }

        self.assertIsNone(select_matching_rule("code anything", [rule]))
        self.assertIs(select_matching_rule("use [code].* now", [rule]), rule)


class TestTriggeredReplyListener(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = Mock()
        self.bot.db = FakeDatabase()
        self.bot.get_context = AsyncMock(
            return_value=SimpleNamespace(prefix=None)
        )
        self.cog = TriggeredReplyCog(self.bot)
        self.cog.rules_by_guild[123] = [
            {
                "guild_id": 123,
                "rule_id": 1,
                **parse_rule_spec(
                    "contains",
                    "dit me vnpt | vnpt nhu con cac",
                ),
            }
        ]

    def make_message(self, content: str) -> Mock:
        message = Mock()
        message.author.bot = False
        message.webhook_id = None
        message.guild = SimpleNamespace(id=123)
        message.content = content
        message.reply = AsyncMock()
        return message

    def test_listener_replies_once_without_mentions(self) -> None:
        message = self.make_message("lai dit me vnpt nua")

        asyncio.run(self.cog.on_message(message))

        message.reply.assert_awaited_once()
        args, kwargs = message.reply.await_args
        self.assertEqual(args[0], "vnpt nhu con cac")
        self.assertFalse(kwargs["mention_author"])
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)

    def test_listener_ignores_command_prefixed_message(self) -> None:
        self.bot.get_context.return_value = SimpleNamespace(prefix="!tf ")
        message = self.make_message("!tf unknown dit me vnpt")

        asyncio.run(self.cog.on_message(message))

        message.reply.assert_not_awaited()

    def test_listener_ignores_webhook_message(self) -> None:
        message = self.make_message("dit me vnpt")
        message.webhook_id = 456

        asyncio.run(self.cog.on_message(message))

        self.bot.get_context.assert_not_awaited()
        message.reply.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
