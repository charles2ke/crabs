import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.config import ConfigError, load_config, parse_config
from openclaw.models import Slot, Watch


BASE = {
    "watches": [
        {
            "country_from": "IE",
            "country_to": "FR",
            "city": "Dublin",
            "provider": "mock",
            "options": {"slots": [{"date": "2026-09-14"}]},
        }
    ]
}


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = parse_config(BASE)
        self.assertEqual(len(config.watches), 1)
        self.assertEqual(config.poll_interval, 300.0)
        self.assertEqual(config.notifiers, ({"type": "console"},))
        self.assertIsNone(config.earliest)

    def test_date_window(self):
        config = parse_config({**BASE, "earliest": "2026-09-01", "latest": "2026-12-31"})
        self.assertEqual(config.earliest, date(2026, 9, 1))
        self.assertEqual(config.latest, date(2026, 12, 31))

    def test_rejects_inverted_window(self):
        with self.assertRaises(ConfigError):
            parse_config({**BASE, "earliest": "2026-12-31", "latest": "2026-09-01"})

    def test_rejects_missing_watches(self):
        with self.assertRaises(ConfigError):
            parse_config({"watches": []})

    def test_rejects_missing_watch_keys(self):
        with self.assertRaises(ConfigError):
            parse_config({"watches": [{"country_from": "IE"}]})

    def test_rejects_non_object_watch_options(self):
        for options in ([], "invalid", None):
            with self.subTest(options=options), self.assertRaisesRegex(
                ConfigError, "watch 'options' must be an object"
            ):
                parse_config(
                    {
                        "watches": [
                            {
                                "country_from": "IE",
                                "country_to": "FR",
                                "city": "Dublin",
                                "options": options,
                            }
                        ]
                    }
                )

    def test_rejects_bad_poll_interval(self):
        with self.assertRaises(ConfigError):
            parse_config({**BASE, "poll_interval": 0})

    def test_env_expansion(self):
        import os

        os.environ["OPENCLAW_TEST_HOOK"] = "https://hooks.example.invalid/abc"
        try:
            config = parse_config(
                {**BASE, "notifiers": [{"type": "webhook", "url": "${OPENCLAW_TEST_HOOK}"}]}
            )
        finally:
            del os.environ["OPENCLAW_TEST_HOOK"]
        self.assertEqual(config.notifiers[0]["url"], "https://hooks.example.invalid/abc")

    def test_telegram_notifier_env_expansion(self):
        import os

        os.environ["OPENCLAW_TELEGRAM_BOT_TOKEN"] = "env-token"
        os.environ["OPENCLAW_TELEGRAM_CHAT_ID"] = "987654"
        try:
            config = parse_config(
                {
                    **BASE,
                    "notifiers": [
                        {
                            "type": "telegram",
                            "bot_token": "${OPENCLAW_TELEGRAM_BOT_TOKEN}",
                            "chat_id": "${OPENCLAW_TELEGRAM_CHAT_ID}",
                        }
                    ],
                }
            )
        finally:
            del os.environ["OPENCLAW_TELEGRAM_BOT_TOKEN"]
            del os.environ["OPENCLAW_TELEGRAM_CHAT_ID"]
        self.assertEqual(config.notifiers[0]["bot_token"], "env-token")
        self.assertEqual(config.notifiers[0]["chat_id"], "987654")

    def test_telegram_bot_token_must_use_env_placeholder(self):
        with self.assertRaisesRegex(ConfigError, "telegram bot_token"):
            parse_config(
                {
                    **BASE,
                    "notifiers": [
                        {
                            "type": "telegram",
                            "bot_token": "123456:literal-token",
                            "chat_id": "${OPENCLAW_TELEGRAM_CHAT_ID}",
                        }
                    ],
                }
            )

    def test_load_dublin_telegram_example(self):
        import os

        os.environ["OPENCLAW_TELEGRAM_BOT_TOKEN"] = "env-token"
        os.environ["OPENCLAW_TELEGRAM_CHAT_ID"] = "987654"
        try:
            config = load_config(
                Path(__file__).resolve().parents[1] / "examples" / "dublin_telegram.json"
            )
        finally:
            del os.environ["OPENCLAW_TELEGRAM_BOT_TOKEN"]
            del os.environ["OPENCLAW_TELEGRAM_CHAT_ID"]
        self.assertEqual(config.notifiers[0]["type"], "telegram")

    def test_load_dublin_example(self):
        config = load_config(Path(__file__).resolve().parents[1] / "examples" / "dublin.json")
        self.assertEqual(len(config.watches), 2)
        self.assertTrue(all(watch.city == "Dublin" for watch in config.watches))

    def test_load_dublin_auth_example(self):
        config = load_config(Path(__file__).resolve().parents[1] / "examples" / "dublin_auth.json")
        self.assertEqual(len(config.watches), 1)
        self.assertEqual(config.watches[0].options["auth"]["type"], "form")

    def test_load_invalid_json(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)


class ModelTests(unittest.TestCase):
    def test_slot_key_is_stable_and_unique(self):
        watch = Watch("IE", "FR", "Dublin")
        first = Slot(watch, date(2026, 9, 14), "09:20")
        same = Slot(watch, date(2026, 9, 14), "09:20", booking_url="https://x.invalid")
        other = Slot(watch, date(2026, 9, 14), "10:20")
        self.assertEqual(first.key, same.key)
        self.assertNotEqual(first.key, other.key)

    def test_describe_contains_details(self):
        slot = Slot(Watch("IE", "FR", "Dublin"), date(2026, 9, 14), "09:20", seats=2)
        text = slot.describe()
        self.assertIn("2026-09-14 09:20", text)
        self.assertIn("Dublin", text)
        self.assertIn("2 seat(s)", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
