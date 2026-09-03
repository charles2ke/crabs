import io
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.cli import main
from openclaw.config import parse_config
from openclaw.models import Alert, Slot, Watch
from openclaw.monitor import Monitor, SeenStore, in_window
from openclaw.notifiers import ConsoleNotifier, FileNotifier, NotifierError, build_notifier


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
        config = make_config([{"date": "2026-09-14", "time": "09:20"}])
        notifier = CollectingNotifier()
        monitor = Monitor(config, notifiers=[notifier], sleeper=lambda _: None)

        self.assertEqual(len(monitor.run_once()), 1)
        self.assertEqual(monitor.run_once(), [])
        self.assertEqual(len(notifier.alerts), 1)
        self.assertIn("2026-09-14 09:20", notifier.stream.getvalue())

    def test_new_slot_triggers_second_alert(self):
        config = make_config([{"date": "2026-09-14"}])
        notifier = CollectingNotifier()
        monitor = Monitor(config, notifiers=[notifier], sleeper=lambda _: None)
        monitor.run_once()

        config2 = make_config([{"date": "2026-09-14"}, {"date": "2026-09-20"}])
        monitor.config = config2
        alerts = monitor.run_once()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].slots[0].slot_date, date(2026, 9, 20))

    def test_slot_disappearing_then_returning_alerts_again(self):
        notifier = CollectingNotifier()
        monitor = Monitor(make_config([{"date": "2026-09-14"}]), notifiers=[notifier], sleeper=lambda _: None)
        monitor.run_once()
        monitor.config = make_config([])
        monitor.run_once()
        monitor.config = make_config([{"date": "2026-09-14"}])
        self.assertEqual(len(monitor.run_once()), 1)
        self.assertEqual(len(notifier.alerts), 2)

    def test_date_window_filtering(self):
        config = make_config(
            [{"date": "2026-08-01"}, {"date": "2026-09-14"}, {"date": "2027-01-01"}],
            earliest="2026-09-01",
            latest="2026-12-31",
        )
        monitor = Monitor(config, notifiers=[], sleeper=lambda _: None)
        slots = monitor.check_watch(config.watches[0])
        self.assertEqual([slot.slot_date for slot in slots], [date(2026, 9, 14)])

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
                        "options": {"slots": [{"date": "2026-09-14"}]},
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
        config = make_config([{"date": "2026-09-14"}])
        monitor = Monitor(config, notifiers=[], sleeper=lambda _: None)
        self.assertEqual(len(monitor.run_once()), 1)

        monitor.config = make_config([], watches=[
            {
                "country_from": "IE",
                "country_to": "FR",
                "city": "Dublin",
                "provider": "mock",
                "options": {"file": "/no/such/file.json"},
            }
        ])
        with self.assertLogs("openclaw", level="WARNING"):
            monitor.run_once()

        monitor.config = config
        self.assertEqual(monitor.run_once(), [])

    def test_notifier_failure_is_logged(self):
        class Broken(ConsoleNotifier):
            name = "broken"

            def send(self, alert):
                raise NotifierError("boom")

        monitor = Monitor(make_config([{"date": "2026-09-14"}]), notifiers=[Broken()], sleeper=lambda _: None)
        with self.assertLogs("openclaw", level="ERROR"):
            self.assertEqual(len(monitor.run_once()), 1)

    def test_run_forever_respects_max_cycles(self):
        sleeps = []
        monitor = Monitor(
            make_config([{"date": "2026-09-14"}]),
            notifiers=[],
            sleeper=sleeps.append,
        )
        alerts = monitor.run_forever(max_cycles=3)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(sleeps, [1.0, 1.0])

    def test_state_file_survives_restart(self):
        with TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "state.json")
            config = make_config([{"date": "2026-09-14"}], state_file=state)
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

    def test_build_notifier_validation(self):
        for spec in ({"type": "nope"}, {"type": "file"}, {"type": "webhook"}, {"type": "webhook", "url": "ftp://x"}):
            with self.assertRaises(NotifierError):
                build_notifier(spec)

    def test_rejects_invalid_webhook_timeout(self):
        for timeout in ("invalid", None):
            with self.subTest(timeout=timeout), self.assertRaises(NotifierError):
                build_notifier(
                    {"type": "webhook", "url": "https://hook.invalid", "timeout": timeout}
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

            self.assertEqual(main(["--config", str(path), "--once"]), 0)
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
