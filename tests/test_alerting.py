"""Tests for change events, quiet hours, throttling, and health detection."""

import io
import json
import logging
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.config import parse_config
from openclaw.logging_utils import configure_logging
from openclaw.monitor import Monitor
from openclaw.notifiers import ConsoleNotifier


class Collector(ConsoleNotifier):
    def __init__(self):
        super().__init__(io.StringIO())
        self.alerts = []

    def send(self, alert):
        self.alerts.append(alert)


def config(slots, **watch_options):
    watch = {
        "country_from": "IE",
        "country_to": "FR",
        "city": "Dublin",
        "provider": "mock",
        "options": {"slots": slots},
    }
    watch.update(watch_options)
    return parse_config({"watches": [watch], "poll_interval": 1})


class ChangeEventTests(unittest.TestCase):
    def test_disappeared_and_improved_are_independent_events(self):
        collector = Collector()
        monitor = Monitor(
            config(
                [{"date": "2026-10-20"}],
                alert_on=["new", "disappeared", "improved"],
            ),
            [collector],
        )
        monitor.run_once()
        monitor.config = config(
            [{"date": "2026-10-10"}],
            alert_on=["new", "disappeared", "improved"],
        )
        events = [alert.event_type for alert in monitor.run_once()]
        self.assertEqual(events, ["new", "disappeared", "improved"])

    def test_default_remains_new_only(self):
        monitor = Monitor(config([{"date": "2026-10-20"}]), [])
        self.assertEqual([a.event_type for a in monitor.run_once()], ["new"])
        monitor.config = config([])
        self.assertEqual(monitor.run_once(), [])


class DeliveryPolicyTests(unittest.TestCase):
    def test_quiet_alert_is_held_then_delivered(self):
        current = [datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc)]
        collector = Collector()
        monitor = Monitor(
            config(
                [{"date": "2026-10-20"}],
                quiet_hours={"start": "22:00", "end": "07:00", "timezone": "UTC"},
            ),
            [collector],
            clock=lambda: current[0],
        )
        self.assertEqual(monitor.run_once(), [])
        self.assertEqual(collector.alerts, [])
        current[0] = datetime(2026, 9, 4, 7, 1, tzinfo=timezone.utc)
        self.assertEqual(len(monitor.run_once()), 1)
        self.assertEqual(len(collector.alerts), 1)

    def test_minimum_gap_holds_burst(self):
        current = [datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)]
        collector = Collector()
        monitor = Monitor(
            config(
                [{"date": "2026-10-20"}],
                throttle={"minimum_gap_seconds": 60},
            ),
            [collector],
            clock=lambda: current[0],
        )
        monitor.run_once()
        monitor.config = config(
            [{"date": "2026-10-20"}, {"date": "2026-10-21"}],
            throttle={"minimum_gap_seconds": 60},
        )
        self.assertEqual(monitor.run_once(), [])
        current[0] = datetime(2026, 9, 3, 12, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(len(monitor.run_once()), 1)


class HealthTests(unittest.TestCase):
    def test_consecutive_empty_results_emit_one_health_warning(self):
        collector = Collector()
        monitor = Monitor(
            config([], health={"max_consecutive_empty": 2}),
            [collector],
        )
        self.assertEqual(monitor.run_once(), [])
        alerts = monitor.run_once()
        self.assertEqual([alert.event_type for alert in alerts], ["health"])
        self.assertIn("2 consecutive empty", alerts[0].message)
        self.assertEqual(monitor.run_once(), [])
        self.assertEqual(monitor.stats[next(iter(monitor.stats))]["successes"], 3)

    def test_health_stats_persist_in_state(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            cfg = parse_config(
                {
                    "watches": [
                        {
                            "country_from": "IE",
                            "country_to": "FR",
                            "city": "Dublin",
                            "options": {"slots": []},
                        }
                    ],
                    "state_file": str(path),
                }
            )
            Monitor(cfg, []).run_once()
            state = json.loads(path.read_text(encoding="utf-8"))
        health = next(iter(state["_openclaw"]["health"].values()))
        self.assertEqual(health["successes"], 1)
        self.assertIn("last_success", health)


class JsonLoggingTests(unittest.TestCase):
    def test_json_logs_are_structured_and_redacted(self):
        output = io.StringIO()
        token = "123456:super-secret"
        with redirect_stderr(output):
            configure_logging(False, "json")
            logging.getLogger("openclaw").error(
                "failed https://api.telegram.org/bot%s/send?email=person@example.com",
                token,
                extra={"event": "delivery_error", "watch": "Dublin"},
            )
        record = json.loads(output.getvalue())
        self.assertEqual(record["event"], "delivery_error")
        self.assertEqual(record["watch"], "Dublin")
        self.assertNotIn(token, output.getvalue())
        self.assertNotIn("person@example.com", output.getvalue())


if __name__ == "__main__":
    unittest.main()
