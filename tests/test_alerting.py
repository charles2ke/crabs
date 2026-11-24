"""Tests for change events, quiet hours, throttling, and health detection."""

import io
import json
import logging
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.config import parse_config
from openclaw.logging_utils import configure_logging
from openclaw.monitor import MAX_BACKOFF_SECONDS, Monitor
from openclaw.notifiers import ConsoleNotifier, NotifierError
from openclaw.providers.base import (
    ChallengeError,
    Provider,
    ProviderError,
    RateLimitError,
    register_provider,
)


#: Future slot dates for tests that run on the real clock; tests that inject
#: a clock use fixed dates relative to it instead.
SOON = (date.today() + timedelta(days=30)).isoformat()
LATER = (date.today() + timedelta(days=40)).isoformat()


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
                [{"date": LATER}],
                alert_on=["new", "disappeared", "improved"],
            ),
            [collector],
        )
        monitor.run_once()
        monitor.config = config(
            [{"date": SOON}],
            alert_on=["new", "disappeared", "improved"],
        )
        events = [alert.event_type for alert in monitor.run_once()]
        self.assertEqual(events, ["new", "disappeared", "improved"])

    def test_default_remains_new_only(self):
        monitor = Monitor(config([{"date": SOON}]), [])
        self.assertEqual([a.event_type for a in monitor.run_once()], ["new"])
        monitor.config = config([])
        self.assertEqual(monitor.run_once(), [])

    def test_past_best_date_is_replaced(self):
        current = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        monitor = Monitor(
            config([{"date": "2026-09-10"}], alert_on=["improved"]),
            [],
            clock=lambda: current,
        )
        monitor.state.meta["best_dates"] = {monitor.config.watches[0].label: "2026-09-02"}
        monitor.run_once()
        self.assertEqual(
            monitor.state.meta["best_dates"][monitor.config.watches[0].label],
            "2026-09-10",
        )


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
        history = monitor.state.meta["alert_history"][monitor.config.watches[0].label]
        self.assertEqual(len(history), 1)

    def test_retry_waits_for_quiet_hours_but_not_the_throttle(self):
        current = [datetime(2026, 9, 3, 21, 0, tzinfo=timezone.utc)]

        class Flaky(Collector):
            name = "flaky"
            fail = True

            def send(self, alert):
                if self.fail:
                    raise NotifierError("boom")
                super().send(alert)

        flaky = Flaky()
        monitor = Monitor(
            config(
                [{"date": "2026-10-20"}],
                quiet_hours={"start": "22:00", "end": "07:00", "timezone": "UTC"},
                throttle={"minimum_gap_seconds": 86400},
            ),
            [flaky],
            clock=lambda: current[0],
        )
        with self.assertLogs("openclaw", level="ERROR"):
            self.assertEqual(monitor.run_once(), [])
        flaky.fail = False
        current[0] = datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(monitor.run_once(), [])
        self.assertEqual(flaky.alerts, [])
        # Still inside the 24 h minimum gap, but a retry is not throttled twice.
        current[0] = datetime(2026, 9, 4, 7, 1, tzinfo=timezone.utc)
        self.assertEqual(len(monitor.run_once()), 1)
        self.assertEqual(len(flaky.alerts), 1)


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


class ChallengeProvider(Provider):
    """Test provider that answers with a CAPTCHA challenge or a plain failure."""

    name = "test-challenge"

    def __init__(self, retry_after=None):
        self.retry_after = retry_after

    def fetch(self, watch):
        raise ChallengeError("portal returned a CAPTCHA/anti-bot challenge", self.retry_after)


class FailingProvider(Provider):
    name = "test-failure"

    def fetch(self, watch):
        raise ProviderError("request failed: HTTP 500")


class RateLimitedProvider(Provider):
    """Test provider that answers HTTP 429 with the given Retry-After."""

    name = "test-rate-limited"

    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        self.calls = 0

    def fetch(self, watch):
        self.calls += 1
        raise RateLimitError("request was rate limited (HTTP 429)", self.retry_after)


register_provider(ChallengeProvider.name, ChallengeProvider)
register_provider(FailingProvider.name, FailingProvider)
register_provider(RateLimitedProvider.name, RateLimitedProvider)


class RetryAfterTests(unittest.TestCase):
    START = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

    def _run(self, cfg, provider, at, notifiers=()):
        clock = at if callable(at) else (lambda: at)
        monitor = Monitor(cfg, list(notifiers), clock=clock)
        monitor._providers[provider.name] = provider
        with self.assertLogs("openclaw", level="INFO") as logs:
            monitor.run_once()
        return monitor, "\n".join(logs.output)

    def test_retry_after_pauses_the_watch_across_runs(self):
        provider = RateLimitedProvider(retry_after=120)
        with TemporaryDirectory() as tmp:
            cfg = replace(
                config([], provider=RateLimitedProvider.name),
                state_file=Path(tmp) / "state.json",
            )
            watch = cfg.watches[0]
            monitor, _ = self._run(cfg, provider, self.START)
            resume = self.START + timedelta(seconds=120)
            self.assertEqual(monitor.backoff_until(watch, self.START), resume)

            # A later scheduled run (a new process) honours the stored pause.
            monitor, logs = self._run(cfg, provider, self.START + timedelta(seconds=60))
            self.assertEqual(provider.calls, 1)
            self.assertEqual(monitor.failed_watches, [watch.label])
            self.assertIn(f"the portal asked to wait until {resume.isoformat()}", logs)
            self.assertEqual(monitor.stats[watch.label]["failures"], 1)

            monitor, _ = self._run(cfg, provider, resume)
            self.assertEqual(provider.calls, 2)

    def test_retry_after_is_capped_and_needs_a_positive_delay(self):
        for retry_after, pause in (
            (10**9, timedelta(seconds=MAX_BACKOFF_SECONDS)),
            (None, None),
            (0, None),
        ):
            with self.subTest(retry_after=retry_after):
                cfg = config([], provider=RateLimitedProvider.name)
                monitor, _ = self._run(cfg, RateLimitedProvider(retry_after), self.START)
                self.assertEqual(
                    monitor.backoff_until(cfg.watches[0], self.START),
                    self.START + pause if pause else None,
                )

    def test_pause_counts_from_when_the_portal_answered(self):
        current = [self.START]

        class SlowPortal(RateLimitedProvider):
            def fetch(self, watch):
                current[0] += timedelta(seconds=90)  # e.g. a slow login first
                return super().fetch(watch)

        cfg = config([], provider=RateLimitedProvider.name)
        monitor, _ = self._run(cfg, SlowPortal(retry_after=60), lambda: current[0])
        self.assertEqual(
            monitor.backoff_until(cfg.watches[0], self.START),
            self.START + timedelta(seconds=150),
        )

    def test_challenge_with_retry_after_pauses_and_still_warns(self):
        collector = Collector()
        cfg = config([], provider=ChallengeProvider.name)
        monitor, _ = self._run(cfg, ChallengeProvider(retry_after=120), self.START, [collector])
        self.assertEqual([alert.event_type for alert in collector.alerts], ["health"])
        self.assertEqual(
            monitor.backoff_until(cfg.watches[0], self.START),
            self.START + timedelta(seconds=120),
        )


class ChallengeHealthTests(unittest.TestCase):
    def test_first_challenge_emits_health_alert(self):
        collector = Collector()
        monitor = Monitor(config([], provider=ChallengeProvider.name), [collector])
        alerts = monitor.run_once()
        self.assertEqual([alert.event_type for alert in alerts], ["health"])
        self.assertIn("CAPTCHA/anti-bot challenge", alerts[0].message)
        record = monitor.stats[next(iter(monitor.stats))]
        self.assertEqual(record["challenges"], 1)
        self.assertEqual(record["consecutive_challenges"], 1)
        self.assertIn("last_challenge", record)
        self.assertEqual(monitor.failed_watches, [monitor.config.watches[0].label])
        # The warning is not repeated while the challenge persists.
        self.assertEqual(monitor.run_once(), [])

    def test_challenge_threshold_is_configurable(self):
        collector = Collector()
        monitor = Monitor(
            config(
                [],
                provider=ChallengeProvider.name,
                health={"max_consecutive_challenges": 2},
            ),
            [collector],
        )
        self.assertEqual(monitor.run_once(), [])
        alerts = monitor.run_once()
        self.assertEqual([alert.event_type for alert in alerts], ["health"])
        self.assertIn("2 consecutive CAPTCHA/anti-bot", alerts[0].message)

    def test_ordinary_failures_are_not_challenges(self):
        collector = Collector()
        monitor = Monitor(config([], provider=FailingProvider.name), [collector])
        self.assertEqual(monitor.run_once(), [])
        record = monitor.stats[next(iter(monitor.stats))]
        self.assertEqual(record["challenges"], 0)
        self.assertEqual(record["consecutive_challenges"], 0)
        self.assertEqual(record["failures"], 1)

    def test_successful_poll_resets_challenge_counter(self):
        monitor = Monitor(config([], provider=ChallengeProvider.name), [])
        monitor.run_once()
        monitor.config = config([])
        monitor._providers.clear()
        monitor.run_once()
        record = monitor.stats[next(iter(monitor.stats))]
        self.assertEqual(record["consecutive_challenges"], 0)
        self.assertEqual(record["challenges"], 1)

    def test_empty_success_allows_warning_for_next_challenge_episode(self):
        monitor = Monitor(config([], provider=ChallengeProvider.name), [])
        self.assertEqual(len(monitor.run_once()), 1)
        monitor.config = config([], provider=FailingProvider.name)
        monitor._providers.clear()
        self.assertEqual(monitor.run_once(), [])
        monitor.config = config([])
        monitor._providers.clear()
        self.assertEqual(monitor.run_once(), [])
        monitor.config = config([], provider=ChallengeProvider.name)
        monitor._providers.clear()
        self.assertEqual(len(monitor.run_once()), 1)


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
