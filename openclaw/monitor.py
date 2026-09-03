"""The polling engine: check watches, de-duplicate and alert on new slots."""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
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

    Persisted as JSON so restarts — and scheduled (cron) runs that only ever
    execute a single cycle — do not re-alert on the same appointment. Each
    entry records the watch it belongs to and the appointment date, so stale
    entries for dates in the past can be pruned away.

    Writes are atomic (temp file + :func:`os.replace`), so an interrupted run
    cannot leave a half-written state file behind.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._seen: dict[str, dict[str, str | None]] = {}
        #: True when no usable state file existed, i.e. this is a cold start.
        self.cold = True
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                LOGGER.warning("ignoring unreadable state file %s", path)
            else:
                self._seen = _parse_state(data)
                self.cold = False

    def __contains__(self, key: str) -> bool:
        return key in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def add_all(
        self,
        keys: Iterable[str],
        watch_label: str | None = None,
        slot_date: date | None = None,
    ) -> None:
        """Record ``keys`` as already alerted on."""
        entry_date = slot_date.isoformat() if slot_date else None
        self._seen.update(
            (key, {"watch": watch_label, "date": entry_date}) for key in keys
        )
        self.save()

    def add_slots(self, slots: Iterable[Slot], watch_label: str | None = None) -> None:
        """Record ``slots`` as already alerted on, keeping their dates."""
        self._seen.update(
            (
                slot.key,
                {"watch": watch_label, "date": slot.slot_date.isoformat()},
            )
            for slot in slots
        )
        self.save()

    def prune(
        self,
        valid_keys: Iterable[str],
        failed_watch_labels: Iterable[str] = (),
        today: date | None = None,
    ) -> None:
        """Drop keys that are no longer offered so they can alert again later.

        Entries whose appointment date has already passed are dropped
        unconditionally, so months of scheduled runs cannot grow the state
        file without bound.
        """
        valid = set(valid_keys)
        failed = set(failed_watch_labels)
        today = today or date.today()
        kept: dict[str, dict[str, str | None]] = {}
        for key, entry in self._seen.items():
            if _is_expired(entry.get("date"), today):
                continue
            watch_label = entry.get("watch")
            if key in valid or (failed and (watch_label is None or watch_label in failed)):
                kept[key] = entry
        self._seen = kept
        self.save()

    def save(self) -> None:
        """Write the state file atomically."""
        if not self.path:
            return
        payload = json.dumps({key: self._seen[key] for key in sorted(self._seen)})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                tmp_path = Path(handle.name)
            os.replace(tmp_path, self.path)
        except OSError:
            LOGGER.warning("cannot persist state file %s", self.path)


def _parse_state(data: object) -> dict[str, dict[str, str | None]]:
    """Read any of the historical state file layouts into the current one."""
    entries: dict[str, dict[str, str | None]] = {}
    if isinstance(data, list):
        return {str(item): {"watch": None, "date": None} for item in data}
    if not isinstance(data, dict):
        return entries
    for key, value in data.items():
        if isinstance(value, dict):
            watch_label = value.get("watch")
            slot_date = value.get("date")
            entries[str(key)] = {
                "watch": str(watch_label) if watch_label is not None else None,
                "date": str(slot_date) if slot_date is not None else None,
            }
        else:
            entries[str(key)] = {
                "watch": str(value) if value is not None else None,
                "date": None,
            }
    return entries


def _is_expired(raw_date: str | None, today: date) -> bool:
    """True when ``raw_date`` is a valid date strictly before ``today``."""
    if not raw_date:
        return False
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").date() < today
    except ValueError:
        return False


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
        bootstrap: bool = False,
    ) -> None:
        self.config = config
        self.notifiers = list(
            notifiers if notifiers is not None else (build_notifier(spec) for spec in config.notifiers)
        )
        self.sleeper = sleeper
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = SeenStore(config.state_file)
        #: Suppress alerts on a cold state store (see :meth:`run_once`).
        self.bootstrap = bootstrap
        #: Labels of watches whose provider failed during the last cycle.
        self.failed_watches: list[str] = []
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
        """Poll every watch once and dispatch alerts for new slots.

        In bootstrap mode, a cold (missing) state store is filled with the
        slots that are currently on offer and no alerts are sent, so a first
        scheduled run does not dump the whole existing backlog at the
        operator.
        """
        alerts: list[Alert] = []
        available_keys: list[str] = []
        failed_watch_labels: list[str] = []
        bootstrapping = self.bootstrap and self.state.cold
        if bootstrapping:
            LOGGER.info("bootstrap run: recording current slots without alerting")
        for watch in self.config.watches:
            try:
                slots = self.check_watch(watch)
            except ProviderError as exc:
                LOGGER.warning("watch %s failed: %s", watch.label, exc)
                failed_watch_labels.append(watch.label)
                continue

            available_keys.extend(slot.key for slot in slots)
            fresh = [slot for slot in slots if slot.key not in self.state]
            if bootstrapping:
                self.state.add_slots(fresh, watch.label)
                LOGGER.info("bootstrapped %d slot(s) for %s", len(fresh), watch.label)
                continue
            if not fresh:
                LOGGER.info("no new slots for %s", watch.label)
                continue

            alert = Alert(watch=watch, slots=tuple(fresh), created_at=self.clock())
            self._dispatch(alert)
            self.state.add_slots(fresh, watch.label)
            alerts.append(alert)

        self.state.prune(available_keys, failed_watch_labels)
        self.state.cold = False
        self.failed_watches = failed_watch_labels
        return alerts

    def run_forever(self, max_cycles: int | None = None) -> list[Alert]:
        """Poll in a loop, sleeping ``poll_interval`` (plus jitter) between cycles."""
        all_alerts: list[Alert] = []
        failed: list[str] = []
        cycle = 0
        while max_cycles is None or cycle < max_cycles:
            all_alerts.extend(self.run_once())
            failed.extend(label for label in self.failed_watches if label not in failed)
            cycle += 1
            if max_cycles is not None and cycle >= max_cycles:
                break
            delay = self.config.poll_interval
            if self.config.jitter:
                delay += random.uniform(0, self.config.jitter)
            self.sleeper(delay)
        self.failed_watches = failed
        return all_alerts

    def _dispatch(self, alert: Alert) -> None:
        for notifier in self.notifiers:
            try:
                notifier.send(alert)
            except NotifierError as exc:
                LOGGER.error("notifier %s failed: %s", notifier.name, exc)
