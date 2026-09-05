"""Dedicated offline tests for command-line behavior."""

import io
import json
import logging
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw import __version__
from openclaw.cli import EXIT_CONFIG_ERROR, EXIT_NO_SLOTS, build_parser, main


def write_config(directory, **updates):
    data = {
        "watches": [
            {
                "country_from": "IE",
                "country_to": "FR",
                "city": "Dublin",
                "provider": "mock",
                "options": {"slots": []},
            }
        ]
    }
    data.update(updates)
    path = Path(directory) / "config.json"
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
        self.assertEqual(declared.group(1), "1.0.0")
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


if __name__ == "__main__":
    unittest.main()
