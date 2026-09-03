"""Command line interface: ``python -m openclaw``."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

from .config import ConfigError, load_config
from .monitor import Monitor
from .notifiers import NotifierError
from .providers import ProviderError

LOGGER = logging.getLogger("openclaw")


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
        return 2

    if args.list_watches:
        for watch in config.watches:
            print(f"{watch.label} via {watch.provider}")
        return 0

    try:
        monitor = Monitor(config)
    except (NotifierError, ProviderError) as exc:
        LOGGER.error("%s", exc)
        return 2

    cycles = 1 if args.once else args.cycles
    try:
        alerts = monitor.run_forever(max_cycles=cycles)
    except KeyboardInterrupt:
        LOGGER.info("stopped by user")
        return 0

    if not alerts:
        LOGGER.info("no new slots found")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
