import json
import tempfile
import unittest
from pathlib import Path

import discord

from cogs.operation.bot_status import (
    STATUS_FILE,
    load_statuses,
    make_activity,
)


class TestBotStatusData(unittest.TestCase):
    def test_shipped_status_data_separates_custom_and_action_schema(self) -> None:
        payload = json.loads(STATUS_FILE.read_text(encoding="utf-8"))

        for entry in payload["bot_statuses"]:
            if entry["type"] == "CUSTOM":
                self.assertIsInstance(entry.get("think"), str)
                self.assertTrue(entry["think"].strip())
                self.assertNotIn("text", entry)
            else:
                self.assertIsInstance(entry.get("text"), str)
                self.assertTrue(entry["text"].strip())
                self.assertNotIn("think", entry)

        self.assertEqual(len(load_statuses()), len(payload["bot_statuses"]))

    def test_load_statuses_uses_field_for_activity_type(self) -> None:
        payload = {
            "bot_statuses": [
                {
                    "type": "playing",
                    "text": "  trốn tìm  ",
                },
                {"type": "custom", "think": "  Đang suy nghĩ...  "},
                {"type": "watching", "text": "  một bộ phim  "},
                {"type": "invalid", "text": "skip me"},
            ]
        }
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "statuses.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            statuses = load_statuses(path)

        self.assertEqual(
            statuses,
            [
                {
                    "type": "PLAYING",
                    "text": "trốn tìm",
                },
                {"type": "CUSTOM", "think": "Đang suy nghĩ..."},
                {"type": "WATCHING", "text": "một bộ phim"},
            ],
        )

    def test_make_activity_uses_only_the_selected_display(self) -> None:
        action = make_activity({"type": "PLAYING", "text": "trốn tìm"})
        thought = make_activity({"type": "CUSTOM", "think": "Đang suy nghĩ..."})

        self.assertEqual(action.type, discord.ActivityType.playing)
        self.assertEqual(action.name, "trốn tìm")
        self.assertIsInstance(thought, discord.CustomActivity)
        self.assertEqual(thought.name, "Đang suy nghĩ...")


if __name__ == "__main__":
    unittest.main()
