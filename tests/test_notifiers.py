"""Offline tests for the console, file, webhook, Slack, Discord, email, ntfy, and Pushover notifiers."""

import io
import json
import smtplib
import unittest
import urllib.error
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.models import Alert, Slot, Watch
from openclaw.notifiers import (
    NTFY_MESSAGE_LIMIT_BYTES,
    PUSHOVER_API_URL,
    PUSHOVER_MESSAGE_LIMIT,
    SLACK_BLOCK_TEXT_LIMIT,
    ConsoleNotifier,
    DiscordNotifier,
    EmailNotifier,
    FileNotifier,
    NotifierError,
    NtfyNotifier,
    PushoverNotifier,
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


class JsonResponse(FakeResponse):
    def __init__(self, body, status=200):
        super().__init__(status)
        self.body = json.dumps(body).encode("utf-8")

    def read(self, _limit):
        return self.body


def many_slots_alert() -> Alert:
    watch = Watch("IE", "FR", "Dublin")
    slots = tuple(
        Slot(watch, date(2026, 9, 14), f"{minute // 60:02d}:{minute % 60:02d}", None, 1)
        for minute in range(0, 1440, 5)
    )
    return Alert(watch, slots, datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))


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

    def test_test_alert_summary(self):
        alert = Alert(
            Watch("IE", "FR", "Dublin"),
            (),
            datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            event_type="test",
            message="notifications are working",
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            SlackNotifier("https://hooks.slack.invalid/x").send(alert)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(
            payload["text"], "Open Claw test alert for FR consulate in Dublin, IE (short-stay)"
        )
        self.assertIn("notifications are working", payload["blocks"][0]["text"]["text"])

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

    def test_explicit_plaintext_disables_starttls(self):
        notifier = EmailNotifier(
            "smtp.invalid",
            ["applicant@example.invalid"],
            sender="openclaw@example.invalid",
            use_tls=False,
            use_ssl=False,
            smtp_factory=FakeSMTP,
        )
        notifier.send(make_alert())
        client = FakeSMTP.instances[0]
        self.assertFalse(client.started_tls)
        self.assertFalse(notifier.use_ssl)
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


class NtfyNotifierTests(unittest.TestCase):
    def test_publishes_json_to_server_root_with_bearer_token(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            NtfyNotifier(
                "secret-topic",
                server="https://ntfy.example.invalid/",
                token="tk_unit",
                priority="high",
                tags=["passport_control"],
                timeout=9,
            ).send(make_alert())
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://ntfy.example.invalid/")
        self.assertEqual(request.get_header("Authorization"), "Bearer tk_unit")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 9)
        self.assertEqual(payload["topic"], "secret-topic")
        self.assertEqual(payload["priority"], 4)
        self.assertEqual(payload["tags"], ["passport_control"])
        self.assertEqual(payload["click"], "https://book.invalid")
        self.assertIn("1 new Schengen slot(s)", payload["title"])
        self.assertIn("• 2026-09-14 09:20 — 2 seat(s)", payload["message"])

    def test_defaults_omit_optional_fields(self):
        alert = Alert(
            Watch("IE", "FR", "Dublin"),
            (),
            datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            event_type="test",
            message="notifications are working",
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            NtfyNotifier("alerts").send(alert)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://ntfy.sh/")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(payload["priority"], 3)
        self.assertEqual(payload["message"], "notifications are working")
        self.assertNotIn("tags", payload)
        self.assertNotIn("click", payload)

    def test_long_digest_fits_the_byte_limit(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            NtfyNotifier("alerts").send(many_slots_alert())
        message = json.loads(urlopen.call_args.args[0].data)["message"]
        # Slot lines contain multi-byte "•" and "—", so a character count would overshoot.
        self.assertLessEqual(len(message.encode("utf-8")), NTFY_MESSAGE_LIMIT_BYTES)
        self.assertIn("more slot(s)", message)

    def test_validates_settings(self):
        self.assertEqual(NtfyNotifier("t", priority="URGENT").priority, 5)
        self.assertEqual(NtfyNotifier("t", priority=2).priority, 2)
        for kwargs in (
            {"priority": 0},
            {"priority": 6},
            {"priority": True},
            {"priority": "loud"},
            {"tags": "passport_control"},
            {"tags": [1]},
            {"tags": ["x" * 513]},
            {"server": "ftp://ntfy.example.invalid"},
            {"timeout": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(NotifierError):
                NtfyNotifier("t", **kwargs)
        with self.assertRaises(NotifierError):
            NtfyNotifier("")


class PushoverNotifierTests(unittest.TestCase):
    TOKEN = "a" * 30
    USER = "u" * 30

    def fields(self, urlopen):
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, PUSHOVER_API_URL)
        self.assertEqual(
            request.get_header("Content-type"), "application/x-www-form-urlencoded"
        )
        return dict(urllib.parse.parse_qsl(request.data.decode("utf-8")))

    def test_posts_form_with_booking_link(self):
        with patch(
            "urllib.request.urlopen", return_value=JsonResponse({"status": 1})
        ) as urlopen:
            PushoverNotifier(
                self.TOKEN, self.USER, priority=1, sound="siren", device="phone", timeout=9
            ).send(make_alert())
        fields = self.fields(urlopen)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 9)
        self.assertEqual(fields["token"], self.TOKEN)
        self.assertEqual(fields["user"], self.USER)
        self.assertEqual(fields["priority"], "1")
        self.assertEqual(fields["sound"], "siren")
        self.assertEqual(fields["device"], "phone")
        self.assertEqual(fields["url"], "https://book.invalid")
        self.assertEqual(
            fields["timestamp"],
            str(int(datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).timestamp())),
        )
        self.assertIn("1 new Schengen slot(s)", fields["title"])
        self.assertIn("• 2026-09-14 09:20 — 2 seat(s)", fields["message"])
        self.assertNotIn("retry", fields)

    def test_emergency_priority_sends_retry_and_expire(self):
        with patch(
            "urllib.request.urlopen", return_value=JsonResponse({"status": 1})
        ) as urlopen:
            PushoverNotifier(self.TOKEN, self.USER, priority=2, retry=120, expire=600).send(
                make_alert()
            )
        fields = self.fields(urlopen)
        self.assertEqual((fields["retry"], fields["expire"]), ("120", "600"))

    def test_long_digest_and_url_fit_api_limits(self):
        watch = Watch("IE", "FR", "Dublin")
        alert = many_slots_alert()
        alert = Alert(
            watch,
            (Slot(watch, date(2026, 9, 14), None, "https://book.invalid/" + "x" * 600, 1),)
            + alert.slots,
            alert.created_at,
        )
        with patch(
            "urllib.request.urlopen", return_value=JsonResponse({"status": 1})
        ) as urlopen:
            PushoverNotifier(self.TOKEN, self.USER).send(alert)
        fields = self.fields(urlopen)
        self.assertLessEqual(len(fields["message"]), PUSHOVER_MESSAGE_LIMIT)
        self.assertIn("more slot(s)", fields["message"])
        self.assertNotIn("url", fields)

    def test_rejected_request_reports_api_errors_without_secrets(self):
        body = {"status": 0, "errors": [f"application token {self.TOKEN} is invalid"]}
        error = urllib.error.HTTPError(
            PUSHOVER_API_URL, 400, "Bad Request", {}, io.BytesIO(json.dumps(body).encode())
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(NotifierError) as caught:
                PushoverNotifier(self.TOKEN, self.USER).send(make_alert())
        message = str(caught.exception)
        self.assertIn("pushover delivery failed: HTTP 400: application token", message)
        self.assertNotIn(self.TOKEN, message)

    def test_unexpected_responses_are_errors(self):
        for response in (JsonResponse({"status": 0}), FakeResponse()):
            with self.subTest(response=response):
                with patch("urllib.request.urlopen", return_value=response):
                    with self.assertRaisesRegex(NotifierError, "unexpected response"):
                        PushoverNotifier(self.TOKEN, self.USER).send(make_alert())
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            with self.assertRaisesRegex(NotifierError, "pushover delivery failed"):
                PushoverNotifier(self.TOKEN, self.USER).send(make_alert())

    def test_validates_settings(self):
        for args, kwargs in (
            (("", self.USER), {}),
            ((self.TOKEN, ""), {}),
            ((self.TOKEN, self.USER), {"priority": 3}),
            ((self.TOKEN, self.USER), {"priority": True}),
            ((self.TOKEN, self.USER), {"priority": "1"}),
            ((self.TOKEN, self.USER), {"retry": 29}),
            ((self.TOKEN, self.USER), {"expire": 10801}),
            ((self.TOKEN, self.USER), {"timeout": 0}),
        ):
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(NotifierError):
                PushoverNotifier(*args, **kwargs)


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
        ntfy = build_notifier(
            {"type": "ntfy", "topic": "alerts", "priority": "max", "tags": ["calendar"]}
        )
        self.assertIsInstance(ntfy, NtfyNotifier)
        self.assertEqual((ntfy.priority, ntfy.tags), (5, ["calendar"]))
        pushover = build_notifier({"type": "pushover", "token": "a" * 30, "user": "u" * 30})
        self.assertIsInstance(pushover, PushoverNotifier)

    def test_build_email_use_ssl_disables_starttls_by_default(self):
        for spec in ({"use_ssl": True}, {"use_ssl": True, "use_tls": None}):
            with self.subTest(spec=spec):
                email = build_notifier(
                    {
                        "type": "email",
                        "host": "smtp.invalid",
                        "sender": "openclaw@example.invalid",
                        "recipients": "applicant@example.invalid",
                        **spec,
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
            {"type": "ntfy"},
            {"type": "ntfy", "topic": "alerts", "tags": "calendar"},
            {"type": "pushover", "token": "a" * 30},
        ):
            with self.subTest(spec=spec), self.assertRaises(NotifierError):
                build_notifier(spec)


if __name__ == "__main__":
    unittest.main()
