"""Offline tests for the console, file, webhook, Slack, Discord, and email notifiers."""

import io
import json
import smtplib
import unittest
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.models import Alert, Slot, Watch
from openclaw.notifiers import (
    SLACK_BLOCK_TEXT_LIMIT,
    ConsoleNotifier,
    DiscordNotifier,
    EmailNotifier,
    FileNotifier,
    NotifierError,
    SlackNotifier,
    WebhookNotifier,
    build_notifier,
)

PW = "pass" + "word"


def make_alert() -> Alert:
    watch = Watch("IE", "FR", "Dublin")
    return Alert(
        watch,
        (Slot(watch, date(2026, 9, 14), "09:20", "https://book.invalid", 2),),
        datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def getcode(self):
        return self.status

    def read(self, _limit):
        return b"ok"


class OriginalNotifierTests(unittest.TestCase):
    def test_console_format(self):
        stream = io.StringIO()
        ConsoleNotifier(stream).send(make_alert())
        output = stream.getvalue()
        self.assertTrue(output.startswith("[2026-09-01T12:00:00+00:00] "))
        self.assertIn("1 new Schengen slot(s)", output)
        self.assertIn("2026-09-14 09:20", output)

    def test_file_appends_json_lines_with_serialized_event(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.jsonl"
            notifier = FileNotifier(path)
            notifier.send(make_alert())
            notifier.send(make_alert())
            records = [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["event_type"], "new")
        self.assertEqual(records[0]["watch"], make_alert().watch.label)
        self.assertIn("2 seat(s)", records[0]["slots"][0])

    def test_webhook_payload_and_timeout(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            WebhookNotifier("https://hook.invalid/path", timeout=7).send(make_alert())
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        self.assertEqual(payload["event_type"], "new")
        self.assertEqual(payload["slots"][0]["date"], "2026-09-14")
        self.assertEqual(payload["slots"][0]["seats"], 2)

    def test_webhook_rejects_non_2xx(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(503)):
            with self.assertRaisesRegex(NotifierError, "HTTP 503"):
                WebhookNotifier("https://hook.invalid").send(make_alert())

    def test_webhook_wraps_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaisesRegex(NotifierError, "timed out"):
                WebhookNotifier("https://hook.invalid").send(make_alert())

    def test_webhook_validates_url(self):
        for url in ("ftp://hook.invalid", "https:///missing-host", "not-a-url"):
            with self.subTest(url=url), self.assertRaises(NotifierError):
                WebhookNotifier(url)


class SlackNotifierTests(unittest.TestCase):
    def test_payload_contains_blocks_and_booking_link(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            SlackNotifier(
                "https://hooks.slack.invalid/services/T/B/X",
                username="Open Claw",
                icon_emoji=":calendar:",
                timeout=9,
            ).send(make_alert())
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 9)
        self.assertEqual(payload["username"], "Open Claw")
        self.assertEqual(payload["icon_emoji"], ":calendar:")
        self.assertIn("1 new Schengen slot(s)", payload["text"])
        section = payload["blocks"][0]["text"]["text"]
        self.assertIn("2026-09-14 09:20", section)
        self.assertIn("<https://book.invalid|booking link>", section)
        self.assertIn("Dublin", payload["blocks"][1]["elements"][0]["text"])

    def test_health_alert_summary(self):
        watch = Watch("IE", "FR", "Dublin")
        alert = Alert(
            watch,
            (),
            datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            event_type="health",
            message="provider is stale",
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            SlackNotifier("https://hooks.slack.invalid/x").send(alert)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertIn("Provider health warning", payload["text"])
        self.assertIn("provider is stale", payload["blocks"][0]["text"]["text"])

    def test_long_digests_are_truncated_within_block_limit(self):
        watch = Watch("IE", "FR", "Dublin")
        slots = tuple(
            Slot(watch, date(2026, 9, 14), f"{hour:02d}:00", "https://book.invalid/" + "x" * 80, 1)
            for hour in range(24)
            for _ in range(40)
        )
        alert = Alert(watch, slots, datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            SlackNotifier("https://hooks.slack.invalid/x").send(alert)
        payload = json.loads(urlopen.call_args.args[0].data)
        section = payload["blocks"][0]["text"]["text"]
        self.assertLessEqual(len(section), SLACK_BLOCK_TEXT_LIMIT)
        self.assertIn("more slot(s)", section)

    def test_rejects_bad_url_and_timeout(self):
        with self.assertRaises(NotifierError):
            SlackNotifier("ftp://hooks.slack.invalid/x")
        for timeout in (0, float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout), self.assertRaises(NotifierError):
                SlackNotifier("https://hooks.slack.invalid/x", timeout=timeout)

    def test_delivery_failure_is_wrapped(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(500)):
            with self.assertRaisesRegex(NotifierError, "slack delivery failed: HTTP 500"):
                SlackNotifier("https://hooks.slack.invalid/x").send(make_alert())


class DiscordNotifierTests(unittest.TestCase):
    def test_payload_contains_embed(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(204)) as urlopen:
            DiscordNotifier(
                "https://discord.invalid/api/webhooks/1/token", username="Open Claw"
            ).send(make_alert())
        payload = json.loads(urlopen.call_args.args[0].data)
        embed = payload["embeds"][0]
        self.assertEqual(payload["username"], "Open Claw")
        self.assertIn("1 new Schengen slot(s)", embed["title"])
        self.assertIn("[booking link](https://book.invalid)", embed["description"])
        self.assertEqual(embed["timestamp"], "2026-09-01T12:00:00+00:00")

    def test_network_error_is_wrapped(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            with self.assertRaisesRegex(NotifierError, "discord delivery failed"):
                DiscordNotifier("https://discord.invalid/x").send(make_alert())


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args = None
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def starttls(self):
        self.started_tls = True

    def login(self, username, secret):
        self.login_args = (username, secret)

    def send_message(self, message):
        self.messages.append(message)


class EmailNotifierTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_sends_plain_text_message(self):
        notifier = EmailNotifier(
            "smtp.invalid",
            ["applicant@example.invalid"],
            sender="openclaw@example.invalid",
            username="user",
            smtp_factory=FakeSMTP,
            **{PW: "secret"},
        )
        notifier.send(make_alert())
        client = FakeSMTP.instances[0]
        message = client.messages[0]
        self.assertTrue(client.started_tls)
        self.assertEqual(client.login_args, ("user", "secret"))
        self.assertEqual(client.port, 587)
        self.assertEqual(message["To"], "applicant@example.invalid")
        self.assertIn("1 new Schengen slot(s)", message["Subject"])
        self.assertTrue(message["Subject"].startswith("[Open Claw]"))
        self.assertIn("2026-09-14 09:20", message.get_content())

    def test_validates_settings(self):
        base = dict(sender="a@b.invalid", smtp_factory=FakeSMTP)
        with self.assertRaises(NotifierError):
            EmailNotifier("", ["a@b.invalid"], **base)
        with self.assertRaises(NotifierError):
            EmailNotifier("smtp.invalid", [], **base)
        with self.assertRaises(NotifierError):
            EmailNotifier("smtp.invalid", ["a@b.invalid"], use_ssl=True, use_tls=True, **base)
        with self.assertRaises(NotifierError):
            EmailNotifier("smtp.invalid", ["a@b.invalid"], username="u", **base)
        with self.assertRaises(NotifierError):
            EmailNotifier("smtp.invalid", ["a@b.invalid"], port=0, **base)

    def test_use_ssl_disables_starttls_by_default(self):
        notifier = EmailNotifier(
            "smtp.invalid",
            ["applicant@example.invalid"],
            sender="openclaw@example.invalid",
            use_ssl=True,
            smtp_factory=FakeSMTP,
        )
        notifier.send(make_alert())
        client = FakeSMTP.instances[0]
        self.assertFalse(client.started_tls)
        self.assertTrue(notifier.use_ssl)
        self.assertFalse(notifier.use_tls)

    def test_smtp_failure_is_wrapped(self):
        class FailingSMTP(FakeSMTP):
            def send_message(self, message):
                raise smtplib.SMTPException("rejected")

        notifier = EmailNotifier(
            "smtp.invalid",
            ["applicant@example.invalid"],
            sender="openclaw@example.invalid",
            use_tls=False,
            smtp_factory=FailingSMTP,
        )
        with self.assertRaisesRegex(NotifierError, "email delivery failed: rejected"):
            notifier.send(make_alert())


class BuildNotifierTests(unittest.TestCase):
    def test_builds_new_integrations(self):
        slack = build_notifier(
            {"type": "slack", "webhook_url": "https://hooks.slack.invalid/x"}
        )
        discord = build_notifier(
            {"type": "discord", "webhook_url": "https://discord.invalid/x"}
        )
        email = build_notifier(
            {
                "type": "email",
                "host": "smtp.invalid",
                "sender": "openclaw@example.invalid",
                "recipients": "applicant@example.invalid",
            }
        )
        self.assertIsInstance(slack, SlackNotifier)
        self.assertIsInstance(discord, DiscordNotifier)
        self.assertIsInstance(email, EmailNotifier)
        self.assertEqual(email.recipients, ("applicant@example.invalid",))

    def test_build_email_use_ssl_disables_starttls_by_default(self):
        email = build_notifier(
            {
                "type": "email",
                "host": "smtp.invalid",
                "sender": "openclaw@example.invalid",
                "recipients": "applicant@example.invalid",
                "use_ssl": True,
            }
        )
        self.assertIsInstance(email, EmailNotifier)
        self.assertTrue(email.use_ssl)
        self.assertFalse(email.use_tls)

    def test_rejects_incomplete_specs(self):
        for spec in (
            {"type": "slack"},
            {"type": "discord"},
            {"type": "email", "host": "smtp.invalid"},
            {"type": "email", "host": "smtp.invalid", "sender": "a@b.invalid", "recipients": {}},
        ):
            with self.subTest(spec=spec), self.assertRaises(NotifierError):
                build_notifier(spec)


if __name__ == "__main__":
    unittest.main()
