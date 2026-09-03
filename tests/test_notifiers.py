"""Offline tests for the console, file, and webhook notifiers."""

import io
import json
import unittest
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.models import Alert, Slot, Watch
from openclaw.notifiers import (
    ConsoleNotifier,
    FileNotifier,
    NotifierError,
    WebhookNotifier,
)


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


if __name__ == "__main__":
    unittest.main()
