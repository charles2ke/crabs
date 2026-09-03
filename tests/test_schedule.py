"""Tests for scheduled (cron) execution: bootstrap, locking, state hygiene."""

import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.cli import (
    EXIT_ALERTS,
    EXIT_CONFIG_ERROR,
    EXIT_LOCKED,
    EXIT_NO_SLOTS,
    EXIT_PROVIDER_ERROR,
    main,
)
from openclaw.config import parse_config
from openclaw.locking import FileLock, LockError
from openclaw.monitor import Monitor, SeenStore

FUTURE = (date.today() + timedelta(days=30)).isoformat()
PAST = (date.today() - timedelta(days=30)).isoformat()


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


def write_config(directory, slots, **overrides):
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
        "state_file": str(Path(directory) / "state.json"),
        "notifiers": [{"type": "file", "path": str(Path(directory) / "alerts.jsonl")}],
    }
    data.update(overrides)
    path = Path(directory) / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class BootstrapTests(unittest.TestCase):
    def test_cold_bootstrap_records_without_alerting(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            config = make_config([{"date": FUTURE}], state_file=str(state))
            monitor = Monitor(config, notifiers=[], sleeper=lambda _: None, bootstrap=True)
            self.assertEqual(monitor.run_once(), [])
            self.assertTrue(state.exists())

            # Second run: the store is warm, so still nothing new to report.
            warm = Monitor(config, notifiers=[], sleeper=lambda _: None, bootstrap=True)
            self.assertEqual(warm.run_once(), [])

    def test_bootstrap_still_alerts_on_later_new_slots(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            config = make_config([{"date": FUTURE}], state_file=str(state))
            Monitor(config, notifiers=[], sleeper=lambda _: None, bootstrap=True).run_once()

            later = (date.today() + timedelta(days=40)).isoformat()
            config2 = make_config(
                [{"date": FUTURE}, {"date": later}], state_file=str(state)
            )
            alerts = Monitor(
                config2, notifiers=[], sleeper=lambda _: None, bootstrap=True
            ).run_once()
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0].slots[0].slot_date.isoformat(), later)

    def test_warm_store_is_not_bootstrapped(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text("{}", encoding="utf-8")
            config = make_config([{"date": FUTURE}], state_file=str(state))
            monitor = Monitor(config, notifiers=[], sleeper=lambda _: None, bootstrap=True)
            self.assertEqual(len(monitor.run_once()), 1)

    def test_cli_bootstrap_suppresses_first_run(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp, [{"date": FUTURE}])
            self.assertEqual(main(["--config", str(path), "--once", "--bootstrap"]), EXIT_NO_SLOTS)
            self.assertFalse((Path(tmp) / "alerts.jsonl").exists())
            self.assertEqual(main(["--config", str(path), "--once"]), EXIT_NO_SLOTS)

    def test_cli_bootstrap_requires_state_file(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp, [{"date": FUTURE}], state_file=None)
            with self.assertLogs("openclaw", level="ERROR"):
                self.assertEqual(
                    main(["--config", str(path), "--once", "--bootstrap"]),
                    EXIT_CONFIG_ERROR,
                )


class LockTests(unittest.TestCase):
    def test_second_lock_is_refused(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json.lock"
            with FileLock(path):
                with self.assertRaises(LockError):
                    FileLock(path).acquire()

    def test_lock_is_reusable_after_release(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json.lock"
            lock = FileLock(path)
            lock.acquire()
            lock.release()
            with FileLock(path):
                pass

    def test_timeout_gives_up(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json.lock"
            with FileLock(path):
                with self.assertRaises(LockError):
                    FileLock(path, timeout=0.05, poll=0.01).acquire()

    def test_cli_exits_when_state_is_locked(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp, [{"date": FUTURE}])
            lock = FileLock(Path(tmp) / "state.json.lock")
            lock.acquire()
            try:
                with self.assertLogs("openclaw", level="WARNING"):
                    self.assertEqual(main(["--config", str(path), "--once"]), EXIT_LOCKED)
            finally:
                lock.release()
            self.assertFalse((Path(tmp) / "alerts.jsonl").exists())


class StateFileTests(unittest.TestCase):
    def test_writes_are_atomic(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "nested" / "state.json"
            store = SeenStore(state)
            store.add_all(["abc"], "FR consulate", date.fromisoformat(FUTURE))
            self.assertEqual(
                json.loads(state.read_text(encoding="utf-8")),
                {"abc": {"watch": "FR consulate", "date": FUTURE}},
            )
            # No temporary leftovers next to the state file.
            self.assertEqual([p.name for p in state.parent.iterdir()], ["state.json"])

    def test_past_dates_are_pruned(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "old": {"watch": "FR consulate", "date": PAST},
                        "new": {"watch": "FR consulate", "date": FUTURE},
                    }
                ),
                encoding="utf-8",
            )
            store = SeenStore(state)
            store.prune(["old", "new"])
            self.assertNotIn("old", store)
            self.assertIn("new", store)
            self.assertEqual(json.loads(state.read_text(encoding="utf-8")).keys(), {"new"})

    def test_legacy_state_layouts_are_read(self):
        with TemporaryDirectory() as tmp:
            list_state = Path(tmp) / "list.json"
            list_state.write_text(json.dumps(["abc"]), encoding="utf-8")
            self.assertIn("abc", SeenStore(list_state))

            flat_state = Path(tmp) / "flat.json"
            flat_state.write_text(json.dumps({"abc": "FR consulate"}), encoding="utf-8")
            store = SeenStore(flat_state)
            self.assertIn("abc", store)
            store.prune(["abc"])
            self.assertIn("abc", store)

    def test_state_path_can_be_overridden_on_the_cli(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp, [{"date": FUTURE}])
            other = Path(tmp) / "cache" / "restored.json"
            self.assertEqual(
                main(["--config", str(path), "--once", "--state", str(other)]),
                EXIT_ALERTS,
            )
            self.assertTrue(other.exists())
            self.assertFalse((Path(tmp) / "state.json").exists())


class ExitCodeTests(unittest.TestCase):
    def test_alerts_and_no_slots(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp, [{"date": FUTURE}])
            self.assertEqual(main(["--config", str(path), "--once"]), EXIT_ALERTS)
            self.assertEqual(main(["--config", str(path), "--once"]), EXIT_NO_SLOTS)

    def test_provider_failure(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp, [])
            data = json.loads(path.read_text(encoding="utf-8"))
            data["watches"][0]["options"] = {"file": "/no/such/file.json"}
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertLogs("openclaw", level="WARNING"):
                self.assertEqual(main(["--config", str(path), "--once"]), EXIT_PROVIDER_ERROR)

    def test_config_error(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertLogs("openclaw", level="ERROR"):
                self.assertEqual(main(["--config", str(path), "--once"]), EXIT_CONFIG_ERROR)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
