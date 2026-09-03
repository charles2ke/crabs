"""The polling engine: check watches, de-duplicate and alert on new slots."""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .config import Config
from .models import Alert, Slot, Watch
from .notifiers import Notifier, NotifierError, build_notifier
from .providers import ProviderError, get_provider

LOGGER = logging.getLogger("openclaw")


class SeenStore:
    """Remembers which slots have already been alerted on.

    Persisted as JSON so restarts do not re-alert on the same appointment.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._seen: dict[str, str | None] = {}
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._seen = {str(item): None for item in data}
                elif isinstance(data, dict):
                    self._seen = {
                        str(key): str(value) if value is not None else None
                        for key, value in data.items()
                    }
            except (OSError, json.JSONDecodeError):
                LOGGER.warning("ignoring unreadable state file %s", path)

    def __contains__(self, key: str) -> bool:
        return key in self._seen

    def add_all(self, keys: Iterable[str], watch_label: str | None = None) -> None:
        self._seen.update((key, watch_label) for key in keys)
        self.save()

    def prune(
        self, valid_keys: Iterable[str], failed_watch_labels: Iterable[str] = ()
    ) -> None:
        """Drop keys that are no longer offered so they can alert again later."""
        valid = set(valid_keys)
        failed = set(failed_watch_labels)
        self._seen = {
            key: watch_label
            for key, watch_label in self._seen.items()
            if key in valid or (failed and (watch_label is None or watch_label in failed))
        }
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(dict(sorted(self._seen.items()))), encoding="utf-8")
        except OSError:
            LOGGER.warning("cannot persist state file %s", self.path)


def in_window(slot: Slot, earliest: date | None, latest: date | None) -> bool:
    """True when ``slot`` falls inside the configured date window."""
    if earliest and slot.slot_date < earliest:
        return False
    if latest and slot.slot_date > latest:
        return False
    return True


class Monitor:
    """Polls every configured watch and alerts on newly available slots."""

    def __init__(
        self,
        config: Config,
        notifiers: Sequence[Notifier] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.notifiers = list(
            notifiers if notifiers is not None else (build_notifier(spec) for spec in config.notifiers)
        )
        self.sleeper = sleeper
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = SeenStore(config.state_file)
        self._providers: dict[str, object] = {}

    def check_watch(self, watch: Watch) -> list[Slot]:
        """Return the in-window slots currently offered for ``watch``."""
        provider = self._providers.get(watch.provider)
        if provider is None:
            provider = get_provider(watch.provider)
            self._providers[watch.provider] = provider
        slots = provider.fetch(watch)
        return sorted(
            (slot for slot in slots if in_window(slot, self.config.earliest, self.config.latest)),
            key=lambda slot: (slot.slot_date, slot.slot_time or ""),
        )

    def run_once(self) -> list[Alert]:
        """Poll every watch once and dispatch alerts for new slots."""
        alerts: list[Alert] = []
        available_keys: list[str] = []
        failed_watch_labels: list[str] = []
        for watch in self.config.watches:
            try:
                slots = self.check_watch(watch)
            except ProviderError as exc:
                LOGGER.warning("watch %s failed: %s", watch.label, exc)
                failed_watch_labels.append(watch.label)
                continue

            available_keys.extend(slot.key for slot in slots)
            fresh = [slot for slot in slots if slot.key not in self.state]
            if not fresh:
                LOGGER.info("no new slots for %s", watch.label)
                continue

            alert = Alert(watch=watch, slots=tuple(fresh), created_at=self.clock())
            self._dispatch(alert)
            self.state.add_all((slot.key for slot in fresh), watch.label)
            alerts.append(alert)

        self.state.prune(available_keys, failed_watch_labels)
        return alerts

    def run_forever(self, max_cycles: int | None = None) -> list[Alert]:
        """Poll in a loop, sleeping ``poll_interval`` (plus jitter) between cycles."""
        all_alerts: list[Alert] = []
        cycle = 0
        while max_cycles is None or cycle < max_cycles:
            all_alerts.extend(self.run_once())
            cycle += 1
            if max_cycles is not None and cycle >= max_cycles:
                break
            delay = self.config.poll_interval
            if self.config.jitter:
                delay += random.uniform(0, self.config.jitter)
            self.sleeper(delay)
        return all_alerts

    def _dispatch(self, alert: Alert) -> None:
        for notifier in self.notifiers:
            try:
                notifier.send(alert)
            except NotifierError as exc:
                LOGGER.error("notifier %s failed: %s", notifier.name, exc)
