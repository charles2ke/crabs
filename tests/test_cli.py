"""Dedicated offline tests for command-line behavior."""

import io
import json
import logging
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw import __version__
from openclaw.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_NO_SLOTS,
    EXIT_PROVIDER_ERROR,
    build_parser,
    main,
)

LABEL = "FR consulate in Dublin, IE (short-stay)"


def write_config(directory, filename="config.json", city="Dublin", **updates):
    data = {
        "watches": [
            {
                "country_from": "IE",
                "country_to": "FR",
                "city": city,
                "provider": "mock",
                "options": {"slots": []},
            }
        ]
    }
    data.update(updates)
    path = Path(directory) / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class CliTests(unittest.TestCase):
    def tearDown(self):
        logging.shutdown()

    def test_argument_parsing(self):
        args = build_parser().parse_args(["--config", "config.json", "--cycles", "3"])
        self.assertEqual(args.cycles, 3)
        self.assertFalse(args.once)

    def test_once_and_cycles_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            build_parser().parse_args(
                ["--config", "config.json", "--once", "--cycles", "2"]
            )
        self.assertEqual(caught.exception.code, 2)

    def test_help_output(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            build_parser().parse_args(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--validate-config", output.getvalue())
        self.assertIn("--log-format", output.getvalue())

    def test_version_output(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(output.getvalue(), f"openclaw {__version__}\n")

    def test_version_matches_pyproject(self):
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        declared = re.search(
            r'(?m)^version = "([^"]+)"$', pyproject.read_text(encoding="utf-8")
        )
        self.assertIsNotNone(declared)
        self.assertRegex(
            declared.group(1), r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
        )
        changelog = pyproject.with_name("CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{declared.group(1)}]", changelog)
        if __version__ != "0+unknown":
            self.assertEqual(__version__, declared.group(1))

    def test_list_watches(self):
        with TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as output:
            code = main(["--config", str(write_config(tmp)), "--list-watches"])
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("via mock", output.getvalue())

    def test_once_and_cycles_select_cycle_count(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp)
            for flag, expected in (("--once", 1), ("--cycles", 4)):
                argv = ["--config", str(path), flag]
                if flag == "--cycles":
                    argv.append("4")
                with patch("openclaw.cli.Monitor") as monitor_type:
                    monitor_type.return_value.run_forever.return_value = []
                    monitor_type.return_value.failed_watches = []
                    self.assertEqual(main(argv), EXIT_NO_SLOTS)
                    monitor_type.return_value.run_forever.assert_called_once_with(
                        max_cycles=expected
                    )

    def test_dry_run_makes_no_network_request(self):
        with TemporaryDirectory() as tmp:
            path = write_config(
                tmp,
                watches=[
                    {
                        "country_from": "IE",
                        "country_to": "FR",
                        "city": "Dublin",
                        "provider": "http-json",
                        "options": {"url": "https://portal.invalid/slots?token=secret"},
                    }
                ],
                notifiers=[{"type": "webhook", "url": "https://hook.invalid/path"}],
            )
            with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
                with redirect_stdout(io.StringIO()) as output:
                    code = main(["--config", str(path), "--dry-run"])
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("provider http-json", output.getvalue())
        self.assertIn("notifier webhook", output.getvalue())
        self.assertNotIn("token=secret", output.getvalue())

    def test_validate_config_reports_only_missing_variable_names(self):
        with TemporaryDirectory() as tmp:
            path = write_config(
                tmp,
                notifiers=[
                    {
                        "type": "telegram",
                        "bot_token": "${MISSING_BOT_TOKEN}",
                        "chat_id": "${MISSING_CHAT_ID}",
                    }
                ],
            )
            os.environ.pop("MISSING_BOT_TOKEN", None)
            os.environ.pop("MISSING_CHAT_ID", None)
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(["--config", str(path), "--validate-config"])
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("MISSING_BOT_TOKEN", error.getvalue())
        self.assertIn("MISSING_CHAT_ID", error.getvalue())

    def test_config_is_discovered_in_working_directory(self):
        with TemporaryDirectory() as tmp:
            write_config(tmp)  # writes config.json, one of the default paths
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()) as output:
                    code = main(["--list-watches"])
            finally:
                os.chdir(cwd)
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("via mock", output.getvalue())

    def test_discovery_prefers_openclaw_json_over_config_json(self):
        with TemporaryDirectory() as tmp:
            write_config(tmp, city="Cork")
            write_config(tmp, filename="openclaw.json", city="Dublin")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()) as output:
                    code = main(["--list-watches"])
            finally:
                os.chdir(cwd)
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("Dublin", output.getvalue())
        self.assertNotIn("Cork", output.getvalue())

    def test_discovery_falls_back_to_nested_config(self):
        with TemporaryDirectory() as tmp:
            nested = Path(tmp) / ".openclaw"
            nested.mkdir()
            write_config(nested, city="Galway")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()) as output:
                    code = main(["--list-watches"])
            finally:
                os.chdir(cwd)
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("Galway", output.getvalue())

    def test_missing_config_reports_searched_paths(self):
        with TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    code = main(["--list-watches"])
            finally:
                os.chdir(cwd)
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("openclaw.json", error.getvalue())
        self.assertIn("--config", error.getvalue())

    def test_stats_without_state_file_explains_next_step(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp, state_file=str(Path(tmp) / "state.json"))
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--config", str(path), "--stats"])
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("No state yet", output.getvalue())

    def test_stats_without_configured_state_file(self):
        with TemporaryDirectory() as tmp:
            path = write_config(tmp)
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--config", str(path), "--stats"])
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("No state file configured", output.getvalue())

    def test_help_lists_examples_and_exit_codes(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            build_parser().parse_args(["--help"])
        self.assertIn("examples:", output.getvalue())
        self.assertIn("exit codes:", output.getvalue())

    def test_test_notifiers_sends_one_test_alert_per_notifier(self):
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.jsonl"
            state = Path(tmp) / "state.json"
            path = write_config(
                tmp,
                state_file=str(state),
                notifiers=[{"type": "console"}, {"type": "file", "path": str(log)}],
            )
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--config", str(path), "--test-notifiers"])
            records = [json.loads(line) for line in log.read_text().splitlines()]
            state_written = state.exists()
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("Test alert for FR consulate in Dublin, IE", output.getvalue())
        self.assertIn("notifier console: ok", output.getvalue())
        self.assertIn("notifier file: ok", output.getvalue())
        self.assertEqual([record["event_type"] for record in records], ["test"])
        self.assertFalse(state_written)

    def test_test_notifiers_reports_each_failure_and_exits_nonzero(self):
        with TemporaryDirectory() as tmp:
            path = write_config(
                tmp,
                notifiers=[
                    {"type": "webhook", "url": "https://hook.invalid/path"},
                    {"type": "console"},
                ],
            )
            with patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
                with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                    code = main(["--config", str(path), "--test-notifiers"])
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("notifier webhook: FAILED: webhook delivery failed", output.getvalue())
        self.assertIn("notifier console: ok", output.getvalue())

    def test_undelivered_alert_is_reported_instead_of_no_new_slots(self):
        with TemporaryDirectory() as tmp:
            path = write_config(
                tmp,
                state_file=str(Path(tmp) / "state.json"),
                notifiers=[{"type": "webhook", "url": "https://hook.invalid/path"}],
                watches=[
                    {
                        "country_from": "IE",
                        "country_to": "FR",
                        "city": "Dublin",
                        "provider": "mock",
                        "options": {"slots": [{"in_days": 30}]},
                    }
                ],
            )
            with patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
                with redirect_stderr(io.StringIO()) as error:
                    code = main(["--config", str(path), "--once"])
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("1 alert(s) not delivered yet", error.getvalue())
        self.assertNotIn("no new slots found", error.getvalue())

    def test_inventory_hides_ntfy_topic_and_shows_pushover_api(self):
        env = {"OPENCLAW_PUSHOVER_TOKEN": "a" * 30}
        with TemporaryDirectory() as tmp, patch.dict(os.environ, env):
            path = write_config(
                tmp,
                notifiers=[
                    {
                        "type": "ntfy",
                        "server": "https://ntfy.example.invalid",
                        "topic": "secret-topic-123",
                    },
                    {
                        "type": "pushover",
                        "token": "${OPENCLAW_PUSHOVER_TOKEN}",
                        "user": "u" * 30,
                    },
                ],
            )
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--config", str(path), "--validate-config"])
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("notifier ntfy endpoint=https://ntfy.example.invalid", output.getvalue())
        self.assertIn(
            "notifier pushover endpoint=https://api.pushover.net/1/messages.json",
            output.getvalue(),
        )
        self.assertNotIn("secret-topic-123", output.getvalue())
        self.assertNotIn("a" * 30, output.getvalue())

    def test_inventory_shows_only_the_heartbeat_host(self):
        env = {"OPENCLAW_HEARTBEAT_URL": "https://hc-ping.example.invalid/ping/0f3c-token"}
        with TemporaryDirectory() as tmp, patch.dict(os.environ, env):
            path = write_config(tmp, heartbeat={"url": "${OPENCLAW_HEARTBEAT_URL}"})
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--config", str(path), "--validate-config"])
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertIn("heartbeat endpoint=https://hc-ping.example.invalid\n", output.getvalue())
        self.assertNotIn("0f3c-token", output.getvalue())

    def test_list_slots_prints_matches_without_alerting_or_saving(self):
        with TemporaryDirectory() as tmp:
            path = write_config(
                tmp,
                state_file=str(Path(tmp) / "state.json"),
                min_seats=2,
                notifiers=[{"type": "file", "path": str(Path(tmp) / "alerts.jsonl")}],
                watches=[
                    {
                        "country_from": "IE",
                        "country_to": "FR",
                        "city": "Dublin",
                        "provider": "mock",
                        "options": {
                            "booking_url": "https://book.example.invalid/slots",
                            "slots": [
                                {"date": "2099-03-03"},
                                {"date": "2099-03-02", "time": "09:30", "seats": 2},
                            ],
                        },
                    }
                ],
            )
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--config", str(path), "--list-slots"])
            created = sorted(item.name for item in Path(tmp).iterdir() if item != path)
        self.assertEqual(code, EXIT_NO_SLOTS)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                f"{LABEL}: 1 of 2 offered slot(s) match the filters",
                "  * 2099-03-02 09:30 - 2 seat(s) - https://book.example.invalid/slots",
            ],
        )
        self.assertEqual(created, [])

    def test_list_slots_reports_failures_and_portal_pauses(self):
        until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps({"_openclaw": {"backoff_until": {LABEL: until.isoformat()}}}),
                encoding="utf-8",
            )
            before = state.read_text(encoding="utf-8")
            broken = {
                "country_from": "IE",
                "country_to": "DE",
                "city": "Dublin",
                "provider": "mock",
                "options": {"slots": "not-a-list"},
            }
            path = write_config(tmp, state_file=str(state))
            config = json.loads(path.read_text(encoding="utf-8"))
            config["watches"].append(broken)
            path.write_text(json.dumps(config), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as listed:
                code = main(["--config", str(path), "--list-slots"])
            with redirect_stdout(io.StringIO()) as stats:
                main(["--config", str(path), "--stats"])
            after = state.read_text(encoding="utf-8")
        self.assertEqual(code, EXIT_PROVIDER_ERROR)
        self.assertEqual(
            listed.getvalue().splitlines(),
            [
                f"{LABEL}: skipped, portal asked to wait until {until.isoformat()}",
                "DE consulate in Dublin, IE (short-stay): FAILED: "
                "mock provider expects a list of slot entries",
            ],
        )
        self.assertIn(f"{LABEL}: slots_seen=0", stats.getvalue())
        self.assertIn(f"paused_until={until.isoformat()}", stats.getvalue())
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
