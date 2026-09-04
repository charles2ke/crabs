"""Command line interface: ``python -m openclaw``."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import urllib.parse
from contextlib import ExitStack
from pathlib import Path
from typing import Sequence

from . import __version__
from .auth import redact_url
from .config import Config, ConfigError, inspect_config, load_config
from .logging_utils import configure_logging
from .locking import FileLock, LockError
from .monitor import Monitor, SeenStore
from .notifiers import NotifierError, build_notifier
from .providers import ProviderError, get_provider

LOGGER = logging.getLogger("openclaw")

#: Ran successfully: no new slots found (also used by --list-watches / Ctrl-C).
EXIT_NO_SLOTS = 0
#: Configuration (or notifier/provider setup) error.
EXIT_CONFIG_ERROR = 2
#: At least one watch failed (provider unavailable) and nothing was alerted.
EXIT_PROVIDER_ERROR = 3
#: Another run holds the state lock, so this run did nothing.
EXIT_LOCKED = 4
#: Ran successfully and alerted about at least one new slot.
EXIT_ALERTS = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openclaw",
        description="Watch Schengen visa appointment slots and alert on availability.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", "-c", required=True, help="path to a JSON config file")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--once",
        action="store_true",
        help="run a single polling cycle instead of looping forever",
    )
    modes.add_argument(
        "--cycles",
        type=int,
        default=None,
        help="stop after N polling cycles (default: run until interrupted)",
    )
    modes.add_argument(
        "--list-watches",
        action="store_true",
        help="print the configured watches and exit",
    )
    modes.add_argument(
        "--validate-config",
        action="store_true",
        help="validate config and print an offline provider/notifier endpoint inventory",
    )
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="alias for --validate-config; never makes network requests",
    )
    modes.add_argument(
        "--stats",
        action="store_true",
        help="print persisted per-watch health statistics without polling",
    )
    parser.add_argument(
        "--state",
        help="path to the seen-slot state file (overrides 'state_file' in the config)",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "on a cold state store, record the currently available slots as "
            "already seen and do not alert (use for the first scheduled run)"
        ),
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=0.0,
        help="seconds to wait for the state lock held by another run (default: 0)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="enable debug logging")
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="log output format (default: text)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose, args.log_format)

    try:
        if args.validate_config or args.dry_run:
            config = inspect_config(args.config)
        else:
            config = load_config(args.config)
    except ConfigError as exc:
        LOGGER.error("%s", exc, extra={"event": "config_error"})
        return EXIT_CONFIG_ERROR

    if args.state:
        config = dataclasses.replace(config, state_file=Path(args.state).expanduser())

    if args.list_watches:
        for watch in config.watches:
            print(f"{watch.label} via {watch.provider}")
        return EXIT_NO_SLOTS

    if args.validate_config or args.dry_run:
        try:
            _print_inventory(config)
        except (NotifierError, ProviderError) as exc:
            LOGGER.error("%s", exc, extra={"event": "config_error"})
            return EXIT_CONFIG_ERROR
        return EXIT_NO_SLOTS

    if args.stats:
        _print_stats(config)
        return EXIT_NO_SLOTS

    if args.bootstrap and not config.state_file:
        LOGGER.error("--bootstrap needs a state file: set 'state_file' or pass --state")
        return EXIT_CONFIG_ERROR

    cycles = 1 if args.once else args.cycles
    if cycles is not None and cycles <= 0:
        LOGGER.error("--cycles must be greater than 0", extra={"event": "config_error"})
        return EXIT_CONFIG_ERROR
    with ExitStack() as stack:
        if config.state_file:
            lock = FileLock(
                config.state_file.with_name(config.state_file.name + ".lock"),
                timeout=args.lock_timeout,
            )
            try:
                stack.enter_context(lock)
            except LockError as exc:
                LOGGER.warning("%s", exc)
                return EXIT_LOCKED

        try:
            monitor = Monitor(config, bootstrap=args.bootstrap)
        except (NotifierError, ProviderError) as exc:
            LOGGER.error("%s", exc)
            return EXIT_CONFIG_ERROR

        try:
            alerts = monitor.run_forever(max_cycles=cycles)
        except KeyboardInterrupt:
            LOGGER.info("stopped by user")
            return EXIT_NO_SLOTS
        failed = monitor.failed_watches

    if alerts:
        return EXIT_ALERTS
    if failed:
        LOGGER.warning("%d watch(es) failed: %s", len(failed), ", ".join(failed))
        return EXIT_PROVIDER_ERROR
    LOGGER.info("no new slots found")
    return EXIT_NO_SLOTS


def _endpoint(value: object) -> str:
    """Return a diagnostic endpoint with credentials and query secrets removed."""
    url = redact_url(str(value))
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _print_inventory(config: Config) -> None:
    print("Configuration valid; no network requests made.")
    for watch in config.watches:
        get_provider(watch.provider)
        options = watch.options
        endpoint = options.get("url")
        if endpoint is None and options.get("base_url"):
            endpoint = urllib.parse.urljoin(
                str(options["base_url"]), str(options.get("availability_path", ""))
            )
        detail = f" endpoint={_endpoint(endpoint)}" if endpoint else ""
        print(f"provider {watch.provider}: {watch.label}{detail}")
    for spec in config.notifiers:
        build_notifier(spec)
        kind = str(spec.get("type", "console")).lower()
        if kind == "telegram":
            endpoint = "https://api.telegram.org/sendMessage"
        else:
            endpoint = spec.get("url") or spec.get("path")
        detail = f" endpoint={_endpoint(endpoint)}" if endpoint else ""
        print(f"notifier {kind}{detail}")


def _print_stats(config: Config) -> None:
    store = SeenStore(config.state_file)
    records = store.meta.get("health", {})
    if not isinstance(records, dict):
        records = {}
    for watch in config.watches:
        record = records.get(watch.label, {})
        print(
            f"{watch.label}: slots_seen={record.get('slots_seen', 0)} "
            f"successes={record.get('successes', 0)} "
            f"failures={record.get('failures', 0)} "
            f"last_success={record.get('last_success', 'never')}"
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
