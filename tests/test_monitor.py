import io
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.cli import EXIT_ALERTS, main
from openclaw.config import parse_config
from openclaw.models import Alert, Slot, Watch
from openclaw.monitor import MAX_DELIVERY_ATTEMPTS, Monitor, SeenStore, in_window
from openclaw.notifiers import ConsoleNotifier, FileNotifier, NotifierError, build_notifier


#: Slot dates are kept in the future so pruning of past appointments (which
#: is keyed off today's date) cannot make these tests fail as time passes.
SOON = date.today() + timedelta(days=30)
LATER = SOON + timedelta(days=6)


def make_config(slots, **overrides):
    data = {
        "watches": [
            {
                "country_from": "IE",
                "country_to": "FR",
                "city": "Dublin",
                "provider": "mock",
                "options": {"slots": slots},
            }
        ],
        "poll_interval": 1,
    }
    data.update(overrides)
    return parse_config(data)


class CollectingNotifier(ConsoleNotifier):
    def __init__(self):
        super().__init__(stream=io.StringIO())
        self.alerts = []

    def send(self, alert):
        super().send(alert)
        self.alerts.append(alert)


class MonitorTests(unittest.TestCase):
    def test_alerts_only_once_per_slot(self):
        config = make_config([{"date": SOON.isoformat(), "time": "09:20"}])
        notifier = CollectingNotifier()
        monitor = Monitor(config, notifiers=[notifier], sleeper=lambda _: None)

        self.assertEqual(len(monitor.run_once()), 1)
        self.assertEqual(monitor.run_once(), [])
        self.assertEqual(len(notifier.alerts), 1)
        self.assertIn(f"{SOON.isoformat()} 09:20", notifier.stream.getvalue())

    def test_new_slot_triggers_second_alert(self):
        config = make_config([{"date": SOON.isoformat()}])
        notifier = CollectingNotifier()
        monitor = Monitor(config, notifiers=[notifier], sleeper=lambda _: None)
        monitor.run_once()

        config2 = make_config([{"date": SOON.isoformat()}, {"date": LATER.isoformat()}])
        monitor.config = config2
        alerts = monitor.run_once()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].slots[0].slot_date, LATER)

    def test_slot_disappearing_then_returning_alerts_again(self):
        notifier = CollectingNotifier()
        monitor = Monitor(make_config([{"date": SOON.isoformat()}]), notifiers=[notifier], sleeper=lambda _: None)
        monitor.run_once()
        monitor.config = make_config([])
        monitor.run_once()
        monitor.config = make_config([{"date": SOON.isoformat()}])
        self.assertEqual(len(monitor.run_once()), 1)
        self.assertEqual(len(notifier.alerts), 2)

    def test_date_window_filtering(self):
        config = make_config(
            [
                {"date": (SOON - timedelta(days=10)).isoformat()},
                {"date": SOON.isoformat()},
                {"date": (LATER + timedelta(days=10)).isoformat()},
            ],
            earliest=(SOON - timedelta(days=1)).isoformat(),
            latest=LATER.isoformat(),
        )
        monitor = Monitor(config, notifiers=[], sleeper=lambda _: None)
        slots = monitor.check_watch(config.watches[0])
        self.assertEqual([slot.slot_date for slot in slots], [SOON])

    def test_provider_errors_do_not_stop_the_loop(self):
        config = parse_config(
            {
                "watches": [
                    {
                        "country_from": "IE",
                        "country_to": "FR",
                        "city": "Dublin",
                        "provider": "mock",
                        "options": {"file": "/no/such/file.json"},
                    },
                    {
                        "country_from": "IE",
                        "country_to": "ES",
                        "city": "Dublin",
                        "provider": "mock",
                        "options": {"slots": [{"date": SOON.isoformat()}]},
                    },
                ],
                "poll_interval": 1,
            }
        )
        monitor = Monitor(config, notifiers=[], sleeper=lambda _: None)
        with self.assertLogs("openclaw", level="WARNING"):
            alerts = monitor.run_once()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].watch.country_to, "ES")

    def test_provider_error_does_not_prune_seen_slots(self):
        config = parse_config(
            {
                "watches": [
                    {
                        "country_from": "IE",
                        "country_to": country,
                        "city": "Dublin",
                        "options": {"slots": [{"date": SOON.isoformat()}]},
                    }
                    for country in ("FR", "ES")
                ]
            }
        )
        monitor = Monitor(config, notifiers=[], sleeper=lambda _: None)
        self.assertEqual(len(monitor.run_once()), 2)

        monitor.config = parse_config(
            {
                "watches": [
                    {
                        "country_from": "IE",
                        "country_to": "FR",
                        "city": "Dublin",
                        "options": {"file": "/no/such/file.json"},
                    },
                    {
                        "country_from": "IE",
                        "country_to": "ES",
                        "city": "Dublin",
                        "options": {"slots": []},
                    },
                ]
            }
        )
        with self.assertLogs("openclaw", level="WARNING"):
            monitor.run_once()

        monitor.config = config
        alerts = monitor.run_once()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].watch.country_to, "ES")

    def test_failed_delivery_is_retried_on_the_next_run(self):
        class Flaky(ConsoleNotifier):
            name = "flaky"

            def __init__(self, failures):
                super().__init__(stream=io.StringIO())
                self.failures = failures
                self.alerts = []

            def send(self, alert):
                if self.failures:
                    self.failures -= 1
                    raise NotifierError("boom")
                self.alerts.append(alert)

        with TemporaryDirectory() as tmp:
            config = make_config(
                [{"date": SOON.isoformat()}], state_file=str(Path(tmp) / "state.json")
            )
            flaky = Flaky(failures=1)
            with self.assertLogs("openclaw", level="ERROR") as logs:
                self.assertEqual(Monitor(config, notifiers=[flaky]).run_once(), [])
            self.assertIn("notifier flaky failed: boom", "\n".join(logs.output))
            # A fresh process (scheduled run) picks the retry up from state.
            with self.assertLogs("openclaw", level="INFO") as logs:
                alerts = Monitor(config, notifiers=[flaky]).run_once()
            self.assertIn("delivered new alert for", "\n".join(logs.output))
            self.assertEqual([alert.event_type for alert in alerts], ["new"])
            self.assertEqual([alert.slots[0].slot_date for alert in flaky.alerts], [SOON])
            state = json.loads(Path(config.state_file).read_text(encoding="utf-8"))
        self.assertEqual(state["_openclaw"]["pending_alerts"], [])

    def test_retry_goes_only_to_the_failed_notifier(self):
        class Flaky(CollectingNotifier):
            name = "flaky"
            fail = True

            def send(self, alert):
                if self.fail:
                    raise NotifierError("boom")
                super().send(alert)

        good, flaky = CollectingNotifier(), Flaky()
        monitor = Monitor(make_config([{"date": SOON.isoformat()}]), notifiers=[good, flaky])
        with self.assertLogs("openclaw", level="ERROR"):
            # Delivered: at least one notifier accepted the alert.
            self.assertEqual(len(monitor.run_once()), 1)
        flaky.fail = False
        self.assertEqual(len(monitor.run_once()), 1)
        self.assertEqual(len(good.alerts), 1)
        self.assertEqual(len(flaky.alerts), 1)
        self.assertEqual(monitor.run_once(), [])

    def test_undeliverable_alert_is_dropped_after_max_attempts(self):
        calls = []

        class Broken(ConsoleNotifier):
            name = "broken"

            def send(self, alert):
                calls.append(alert)
                raise NotifierError("boom")

        monitor = Monitor(make_config([{"date": SOON.isoformat()}]), notifiers=[Broken()])
        with self.assertLogs("openclaw", level="ERROR") as logs:
            for _ in range(MAX_DELIVERY_ATTEMPTS + 2):
                self.assertEqual(monitor.run_once(), [])
        self.assertEqual(len(calls), MAX_DELIVERY_ATTEMPTS)
        self.assertEqual(monitor.state.meta["pending_alerts"], [])
        self.assertIn("giving up on new alert", "\n".join(logs.output))

    def test_retry_is_dropped_when_its_notifier_is_removed(self):
        class Broken(ConsoleNotifier):
            name = "broken"

            def send(self, alert):
                raise NotifierError("boom")

        monitor = Monitor(make_config([{"date": SOON.isoformat()}]), notifiers=[Broken()])
        with self.assertLogs("openclaw", level="ERROR"):
            monitor.run_once()
        replacement = CollectingNotifier()
        monitor.notifiers = [replacement]
        with self.assertLogs("openclaw", level="WARNING") as logs:
            self.assertEqual(monitor.run_once(), [])
        self.assertIn("no longer configured", "\n".join(logs.output))
        self.assertEqual(replacement.alerts, [])
        self.assertEqual(monitor.state.meta["pending_alerts"], [])

    def test_retry_is_dropped_when_its_watch_is_renamed(self):
        class Flaky(CollectingNotifier):
            name = "flaky"
            fail = True

            def send(self, alert):
                if self.fail:
                    raise NotifierError("boom")
                super().send(alert)

        flaky = Flaky()
        monitor = Monitor(make_config([{"date": SOON.isoformat()}]), notifiers=[flaky])
        with self.assertLogs("openclaw", level="ERROR"):
            monitor.run_once()
        self.assertEqual(monitor.pending_retries, 1)

        flaky.fail = False
        renamed = replace(monitor.config.watches[0], visa_category="long-stay")
        monitor.config = replace(monitor.config, watches=(renamed,))
        with self.assertLogs("openclaw", level="WARNING") as logs:
            monitor.run_once()
        self.assertIn("its watch is no longer configured", "\n".join(logs.output))
        # Only the renamed watch's own alert is sent, never the orphaned retry.
        self.assertEqual(
            [alert.watch.label for alert in flaky.alerts],
            ["FR consulate in Dublin, IE (long-stay)"],
        )
        self.assertEqual(monitor.pending_retries, 0)

    def test_past_slots_are_ignored_and_never_realerted(self):
        def clock():
            return datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)

        with TemporaryDirectory() as tmp:
            config = make_config(
                [{"date": "2026-09-05"}, {"date": "2026-09-20"}],
                state_file=str(Path(tmp) / "state.json"),
            )
            alerts = Monitor(config, notifiers=[], clock=clock).run_once()
            self.assertEqual(
                [slot.slot_date for slot in alerts[0].slots], [date(2026, 9, 20)]
            )
            for _ in range(2):
                self.assertEqual(Monitor(config, notifiers=[], clock=clock).run_once(), [])

    def test_expired_slot_is_not_reported_as_disappeared(self):
        current = [datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)]
        config = parse_config(
            {
                "watches": [
                    {
                        "country_from": "IE",
                        "country_to": "FR",
                        "city": "Dublin",
                        "alert_on": ["new", "disappeared"],
                        "options": {"slots": [{"date": "2026-09-11"}]},
                    }
                ]
            }
        )
        monitor = Monitor(config, notifiers=[], clock=lambda: current[0])
        self.assertEqual(len(monitor.run_once()), 1)
        current[0] = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(monitor.run_once(), [])

    def test_watch_window_and_min_seats_override_global_settings(self):
        config = parse_config(
            {
                "earliest": SOON.isoformat(),
                "min_seats": 2,
                "watches": [
                    {
                        "country_from": "IE",
                        "country_to": country,
                        "city": "Dublin",
                        "options": {
                            "slots": [
                                {"date": SOON.isoformat(), "seats": 1},
                                {"date": SOON.isoformat(), "time": "10:00", "seats": 3},
                                {"date": LATER.isoformat(), "seats": 2},
                            ]
                        },
                        **overrides,
                    }
                    for country, overrides in (
                        ("FR", {}),
                        ("ES", {"latest": SOON.isoformat(), "min_seats": 1}),
                    )
                ],
            }
        )
        monitor = Monitor(config, notifiers=[])
        inherited, overridden = (monitor.check_watch(watch) for watch in config.watches)
        self.assertEqual(
            [(slot.slot_date, slot.seats) for slot in inherited], [(SOON, 3), (LATER, 2)]
        )
        self.assertEqual(
            [(slot.slot_date, slot.seats) for slot in overridden], [(SOON, 1), (SOON, 3)]
        )

    def test_run_forever_respects_max_cycles(self):
        sleeps = []
        monitor = Monitor(
            make_config([{"date": SOON.isoformat()}]),
            notifiers=[],
            sleeper=sleeps.append,
        )
        alerts = monitor.run_forever(max_cycles=3)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(sleeps, [1.0, 1.0])

    def test_state_file_survives_restart(self):
        with TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "state.json")
            config = make_config([{"date": SOON.isoformat()}], state_file=state)
            self.assertEqual(len(Monitor(config, notifiers=[], sleeper=lambda _: None).run_once()), 1)
            self.assertEqual(Monitor(config, notifiers=[], sleeper=lambda _: None).run_once(), [])

    def test_seen_store_ignores_corrupt_state(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertLogs("openclaw", level="WARNING"):
                store = SeenStore(path)
            self.assertNotIn("anything", store)

    def test_in_window_bounds(self):
        slot = Slot(Watch("IE", "FR", "Dublin"), date(2026, 9, 14))
        self.assertTrue(in_window(slot, None, None))
        self.assertFalse(in_window(slot, date(2026, 10, 1), None))
        self.assertFalse(in_window(slot, None, date(2026, 9, 1)))


class NotifierTests(unittest.TestCase):
    def _alert(self):
        watch = Watch("IE", "FR", "Dublin")
        return Alert(
            watch=watch,
            slots=(Slot(watch, date(2026, 9, 14), "09:20"),),
            created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )

    def test_file_notifier_writes_jsonl(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "alerts.jsonl"
            FileNotifier(path).send(self._alert())
            record = json.loads(path.read_text(encoding="utf-8").strip())
        self.assertIn("Dublin", record["watch"])
        self.assertEqual(len(record["slots"]), 1)

    def test_build_notifier_types(self):
        self.assertEqual(build_notifier({"type": "console"}).name, "console")
        self.assertEqual(build_notifier({"type": "file", "path": "a.jsonl"}).name, "file")
        self.assertEqual(
            build_notifier({"type": "webhook", "url": "https://hook.invalid"}).name, "webhook"
        )
        self.assertEqual(
            build_notifier(
                {"type": "telegram", "bot_token": "unit-test-token", "chat_id": "987654"}
            ).name,
            "telegram",
        )

    def test_build_notifier_validation(self):
        for spec in (
            {"type": "nope"},
            {"type": "file"},
            {"type": "webhook"},
            {"type": "webhook", "url": "ftp://x"},
            {"type": "telegram"},
            {"type": "telegram", "bot_token": "unit-test-token"},
            {"type": "telegram", "chat_id": "987654"},
        ):
            with self.assertRaises(NotifierError):
                build_notifier(spec)

    def test_rejects_invalid_webhook_timeout(self):
        for timeout in ("invalid", None):
            with self.subTest(timeout=timeout), self.assertRaises(NotifierError):
                build_notifier(
                    {"type": "webhook", "url": "https://hook.invalid", "timeout": timeout}
                )

    def test_rejects_invalid_telegram_timeout(self):
        for timeout in ("invalid", None):
            with self.subTest(timeout=timeout), self.assertRaises(NotifierError):
                build_notifier(
                    {
                        "type": "telegram",
                        "bot_token": "unit-test-token",
                        "chat_id": "987654",
                        "timeout": timeout,
                    }
                )


class CliTests(unittest.TestCase):
    def test_once_run_on_dublin_example(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "dublin.json"
        with TemporaryDirectory() as tmp:
            config = json.loads(example.read_text(encoding="utf-8"))
            config["state_file"] = str(Path(tmp) / "state.json")
            config["notifiers"] = [{"type": "file", "path": str(Path(tmp) / "alerts.jsonl")}]
            path = Path(tmp) / "dublin.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            self.assertEqual(main(["--config", str(path), "--once"]), EXIT_ALERTS)
            lines = (Path(tmp) / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_list_watches(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "dublin.json"
        self.assertEqual(main(["--config", str(example), "--list-watches"]), 0)

    def test_invalid_config_returns_error_code(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertLogs("openclaw", level="ERROR"):
                self.assertEqual(main(["--config", str(path), "--once"]), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
