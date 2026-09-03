import json
import unittest
from datetime import date, datetime, timezone

from openclaw.config import parse_config
from openclaw.models import Alert, Slot, Watch
from openclaw.monitor import Monitor
from openclaw.notifiers import NotifierError, TelegramNotifier, redact_telegram_token
from openclaw.providers.base import ProviderError


TOKEN = "unit-test-token"


class FakeSession:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [(200, {"ok": True})])
        self.error = error
        self.requests = []

    def request(self, url, headers=None, method="GET", body=None, expect_json=False, **kwargs):
        self.requests.append(
            {
                "url": url,
                "headers": headers,
                "method": method,
                "body": body,
                "expect_json": expect_json,
            }
        )
        if self.error:
            raise self.error
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode("utf-8")


def make_alert(slots, watch=None):
    watch = watch or Watch("IE", "FR", "Dublin")
    return Alert(
        watch=watch,
        slots=tuple(slots),
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )


class TelegramNotifierTests(unittest.TestCase):
    def test_formats_single_slot_payload(self):
        watch = Watch("IE", "FR", "Dublin", "short-stay")
        alert = make_alert(
            (
                Slot(
                    watch,
                    date(2026, 9, 14),
                    "09:20",
                    seats=2,
                    booking_url="https://example.invalid/book",
                ),
            ),
            watch,
        )
        session = FakeSession()

        TelegramNotifier(TOKEN, "chat-1", session=session).send(alert)

        self.assertEqual(len(session.requests), 1)
        request = session.requests[0]
        payload = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(request["method"], "POST")
        self.assertTrue(request["expect_json"])
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertIn("<b>1 new Schengen slot(s)</b>", payload["text"])
        self.assertIn("Centre: Dublin (IE)", payload["text"])
        self.assertIn("Destination: FR", payload["text"])
        self.assertIn("Visa category: short-stay", payload["text"])
        self.assertIn("• 2026-09-14 09:20 — 2 seat(s)", payload["text"])
        self.assertIn('<a href="https://example.invalid/book">booking link</a>', payload["text"])

    def test_formats_multiple_slots_and_escapes_html(self):
        watch = Watch("I&E", "F<R", "Dub<lin & Co", 'short "stay" & <fast>')
        alert = make_alert(
            (
                Slot(
                    watch,
                    date(2026, 9, 14),
                    "09:20 & <soon>",
                    seats=1,
                    booking_url='https://example.invalid/book?a=1&b=<x>"',
                ),
                Slot(watch, date(2026, 9, 15), seats=3),
            ),
            watch,
        )
        message = TelegramNotifier(TOKEN, "chat-1")._format_messages(alert)[0]

        self.assertIn("Centre: Dub&lt;lin &amp; Co (I&amp;E)", message)
        self.assertIn("Destination: F&lt;R", message)
        self.assertIn("Visa category: short &quot;stay&quot; &amp; &lt;fast&gt;", message)
        self.assertIn("09:20 &amp; &lt;soon&gt;", message)
        self.assertIn(
            'href="https://example.invalid/book?a=1&amp;b=&lt;x&gt;&quot;"',
            message,
        )
        self.assertEqual(message.count("• "), 2)

    def test_splits_long_messages_on_slot_boundaries(self):
        watch = Watch("IE", "FR", "Dublin")
        slots = tuple(
            Slot(
                watch,
                date(2026, 9, day),
                "09:20",
                booking_url=f"https://example.invalid/book/{'x' * 50}/{day}",
            )
            for day in range(10, 16)
        )
        notifier = TelegramNotifier(TOKEN, "chat-1", message_limit=260)
        messages = notifier._format_messages(make_alert(slots, watch))

        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 260 for message in messages))
        self.assertTrue(all("Destination: FR" in message for message in messages))
        self.assertTrue(all("• " in message for message in messages))
        combined = "\n".join(messages)
        for day in range(10, 16):
            self.assertEqual(combined.count(f"2026-09-{day}"), 1)

    def test_redacts_token_in_helper_and_delivery_errors(self):
        raw = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        self.assertEqual(
            redact_telegram_token(f"request to {raw!r} failed", TOKEN),
            "request to 'https://api.telegram.org/bot<redacted>/sendMessage' failed",
        )
        session = FakeSession(error=ProviderError(f"request to {raw!r} failed"))

        with self.assertRaises(NotifierError) as caught:
            TelegramNotifier(TOKEN, "chat-1", session=session).send(
                make_alert((Slot(Watch("IE", "FR", "Dublin"), date(2026, 9, 14)),))
            )

        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("bot<redacted>", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_monitor_logs_redacted_token_on_delivery_error(self):
        raw = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        config = parse_config(
            {
                "watches": [
                    {
                        "country_from": "IE",
                        "country_to": "FR",
                        "city": "Dublin",
                        "options": {"slots": [{"date": "2026-09-14"}]},
                    }
                ]
            }
        )
        notifier = TelegramNotifier(
            TOKEN,
            "chat-1",
            session=FakeSession(error=ProviderError(f"request to {raw!r} failed")),
        )

        with self.assertLogs("openclaw", level="ERROR") as logs:
            Monitor(config, notifiers=[notifier], sleeper=lambda _: None).run_once()

        log_text = "\n".join(logs.output)
        self.assertNotIn(TOKEN, log_text)
        self.assertIn("bot<redacted>", log_text)

    def test_non_ok_api_error_raises_notifier_error(self):
        session = FakeSession([(200, {"ok": False, "description": f"bad bot{TOKEN}"})])

        with self.assertRaises(NotifierError) as caught:
            TelegramNotifier(TOKEN, "chat-1", session=session).send(
                make_alert((Slot(Watch("IE", "FR", "Dublin"), date(2026, 9, 14)),))
            )

        self.assertIn("bad bot<redacted>", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_429_retry_after_retries_once(self):
        session = FakeSession(
            [
                (
                    429,
                    {
                        "ok": False,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 2},
                    },
                ),
                (200, {"ok": True}),
            ]
        )
        sleeps = []

        TelegramNotifier(TOKEN, "chat-1", session=session, sleeper=sleeps.append).send(
            make_alert((Slot(Watch("IE", "FR", "Dublin"), date(2026, 9, 14)),))
        )

        self.assertEqual(sleeps, [2.0])
        self.assertEqual(len(session.requests), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
