"""Command line interface: ``python -m openclaw``."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Sequence

from .config import ConfigError, load_config
from .locking import FileLock, LockError
from .monitor import Monitor
from .notifiers import NotifierError
from .providers import ProviderError

LOGGER = logging.getLogger("openclaw")

#: Ran successfully, no new slots were found.
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
    parser.add_argument("--config", "-c", required=True, help="path to a JSON config file")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single polling cycle instead of looping forever",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=None,
        help="stop after N polling cycles (default: run until interrupted)",
    )
    parser.add_argument(
        "--list-watches",
        action="store_true",
        help="print the configured watches and exit",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        LOGGER.error("%s", exc)
        return EXIT_CONFIG_ERROR

    if args.state:
        config = dataclasses.replace(config, state_file=Path(args.state).expanduser())

    if args.list_watches:
        for watch in config.watches:
            print(f"{watch.label} via {watch.provider}")
        return EXIT_NO_SLOTS

    if args.bootstrap and not config.state_file:
        LOGGER.error("--bootstrap needs a state file: set 'state_file' or pass --state")
        return EXIT_CONFIG_ERROR

    cycles = 1 if args.once else args.cycles
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
