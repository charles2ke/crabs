"""The polling engine: check watches, de-duplicate and alert on new slots."""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .config import Config
from .models import Alert, Slot, Watch
from .notifiers import Notifier, NotifierError, build_notifier
from .providers import ProviderError, get_provider
from .providers.base import Provider

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
        self.meta: dict[str, Any] = {}
        #: True when no usable state file existed, i.e. this is a cold start.
        self.cold = True
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                LOGGER.warning("ignoring unreadable state file %s", path)
            else:
                self._seen = _parse_state(data)
                if isinstance(data, dict) and isinstance(data.get("_openclaw"), dict):
                    self.meta = dict(data["_openclaw"])
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
        data: dict[str, Any] = {key: self._seen[key] for key in sorted(self._seen)}
        if self.meta:
            data["_openclaw"] = self.meta
        payload = json.dumps(data, sort_keys=True)
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
        if key == "_openclaw":
            continue
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
        self._providers: dict[str, Provider] = {}
        self.stats: dict[str, dict[str, Any]] = {}

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
        now = self.clock()
        self._flush_pending(now, alerts)
        if bootstrapping:
            LOGGER.info("bootstrap run: recording current slots without alerting")
        for watch in self.config.watches:
            try:
                slots = self.check_watch(watch)
            except ProviderError as exc:
                LOGGER.warning(
                    "watch %s failed: %s",
                    watch.label,
                    exc,
                    extra={"event": "provider_error", "watch": watch.label},
                )
                failed_watch_labels.append(watch.label)
                warning = self._record_health(watch, now, error=True)
                if warning:
                    self._deliver_or_hold(warning, alerts)
                continue

            available_keys.extend(slot.key for slot in slots)
            warning = self._record_health(watch, now, slots=slots)
            if warning:
                self._deliver_or_hold(warning, alerts)
            fresh = [slot for slot in slots if slot.key not in self.state]
            previous = self._previous_slots(watch)
            current_keys = {slot.key for slot in slots}
            disappeared = [slot for slot in previous if slot.key not in current_keys]
            previous_best = self._best_date(watch)
            improved = (
                [slot for slot in fresh if slot.slot_date < previous_best]
                if previous_best
                else []
            )
            self._store_current(watch, slots)
            if bootstrapping:
                self.state.add_slots(fresh, watch.label)
                LOGGER.info("bootstrapped %d slot(s) for %s", len(fresh), watch.label)
                continue
            event_slots = {
                "new": fresh,
                "disappeared": disappeared,
                "improved": improved,
            }
            generated = False
            for event_type in watch.alert_on:
                changed = event_slots[event_type]
                if not changed:
                    continue
                alert = Alert(
                    watch=watch,
                    slots=tuple(changed),
                    created_at=now,
                    event_type=event_type,
                )
                self._deliver_or_hold(alert, alerts)
                generated = True
            if not generated:
                LOGGER.info("no new slots for %s", watch.label)
            # Record fresh slots even when "new" alerts are disabled or held.
            self.state.add_slots(fresh, watch.label)

        self.state.prune(available_keys, failed_watch_labels)
        self.state.cold = False
        self.failed_watches = failed_watch_labels
        self.stats = {
            watch.label: dict(self._health_records().get(watch.label, {}))
            for watch in self.config.watches
        }
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

    def _health_records(self) -> dict[str, dict[str, Any]]:
        records = self.state.meta.setdefault("health", {})
        return records if isinstance(records, dict) else {}

    def _record_health(
        self,
        watch: Watch,
        now: datetime,
        *,
        slots: Sequence[Slot] = (),
        error: bool = False,
    ) -> Alert | None:
        records = self._health_records()
        record = records.setdefault(
            watch.label,
            {
                "consecutive_empty": 0,
                "consecutive_errors": 0,
                "successes": 0,
                "failures": 0,
                "slots_seen": 0,
                "first_observed": now.isoformat(),
                "warning_active": False,
            },
        )
        if error:
            record["consecutive_errors"] = int(record.get("consecutive_errors", 0)) + 1
            record["failures"] = int(record.get("failures", 0)) + 1
        else:
            record["consecutive_errors"] = 0
            record["successes"] = int(record.get("successes", 0)) + 1
            record["last_success"] = now.isoformat()
            record["slots_seen"] = int(record.get("slots_seen", 0)) + len(slots)
            if slots:
                record["consecutive_empty"] = 0
                record["last_slots_seen"] = now.isoformat()
                record["warning_active"] = False
            else:
                record["consecutive_empty"] = int(record.get("consecutive_empty", 0)) + 1

        settings = dict(self.config.health)
        settings.update(watch.health)
        reasons: list[str] = []
        if settings.get("max_consecutive_empty") and int(
            record.get("consecutive_empty", 0)
        ) >= int(settings["max_consecutive_empty"]):
            reasons.append(f"{record['consecutive_empty']} consecutive empty results")
        if settings.get("max_consecutive_errors") and int(
            record.get("consecutive_errors", 0)
        ) >= int(settings["max_consecutive_errors"]):
            reasons.append(f"{record['consecutive_errors']} consecutive errors")
        if settings.get("max_stale_hours"):
            baseline = record.get("last_slots_seen") or record.get("first_observed")
            try:
                stale_for = now - datetime.fromisoformat(str(baseline))
            except (TypeError, ValueError):
                stale_for = timedelta()
            if stale_for >= timedelta(hours=float(settings["max_stale_hours"])):
                reasons.append(f"no slots seen for {settings['max_stale_hours']} hour(s)")

        self.state.save()
        if not reasons or record.get("warning_active"):
            return None
        record["warning_active"] = True
        self.state.save()
        return Alert(
            watch=watch,
            slots=(),
            created_at=now,
            event_type="health",
            message="; ".join(reasons),
        )

    def _current_records(self) -> dict[str, list[dict[str, Any]]]:
        records = self.state.meta.setdefault("current_slots", {})
        return records if isinstance(records, dict) else {}

    def _previous_slots(self, watch: Watch) -> list[Slot]:
        slots: list[Slot] = []
        for item in self._current_records().get(watch.label, []):
            try:
                slots.append(
                    Slot(
                        watch=watch,
                        slot_date=date.fromisoformat(str(item["date"])),
                        slot_time=item.get("time"),
                        booking_url=item.get("booking_url"),
                        seats=int(item.get("seats", 1)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return slots

    def _store_current(self, watch: Watch, slots: Sequence[Slot]) -> None:
        self._current_records()[watch.label] = [
            {
                "date": slot.slot_date.isoformat(),
                "time": slot.slot_time,
                "booking_url": slot.booking_url,
                "seats": slot.seats,
            }
            for slot in slots
        ]
        best = self.state.meta.setdefault("best_dates", {})
        if slots and isinstance(best, dict):
            candidate = min(slot.slot_date for slot in slots)
            old = best.get(watch.label)
            if old is None or candidate < date.fromisoformat(str(old)):
                best[watch.label] = candidate.isoformat()
        self.state.save()

    def _best_date(self, watch: Watch) -> date | None:
        best = self.state.meta.get("best_dates", {})
        if not isinstance(best, dict) or not best.get(watch.label):
            return None
        try:
            result = date.fromisoformat(str(best[watch.label]))
        except ValueError:
            return None
        if result < self.clock().date():
            del best[watch.label]
            return None
        return result

    def _quiet_settings(self, watch: Watch) -> Mapping[str, Any] | None:
        return watch.quiet_hours if watch.quiet_hours is not None else self.config.quiet_hours

    def _is_quiet(self, watch: Watch, now: datetime) -> bool:
        settings = self._quiet_settings(watch)
        if not settings:
            return False
        local = now.astimezone(ZoneInfo(str(settings["timezone"]))).time()
        start = datetime_time.fromisoformat(str(settings["start"]))
        end = datetime_time.fromisoformat(str(settings["end"]))
        if start < end:
            return start <= local < end
        return local >= start or local < end

    def _throttle_settings(self, watch: Watch) -> dict[str, Any]:
        settings = dict(self.config.throttle)
        settings.update(watch.throttle)
        return settings

    def _can_dispatch(self, watch: Watch, now: datetime) -> bool:
        settings = self._throttle_settings(watch)
        history = self.state.meta.setdefault("alert_history", {})
        if not isinstance(history, dict):
            return True
        raw_times = history.setdefault(watch.label, [])
        times: list[datetime] = []
        for raw in raw_times:
            try:
                times.append(datetime.fromisoformat(str(raw)))
            except ValueError:
                continue
        interval = float(settings.get("interval_seconds", 0))
        if interval:
            cutoff = now - timedelta(seconds=interval)
            times = [sent for sent in times if sent >= cutoff]
        elif times:
            times = times[-1:]
        history[watch.label] = [sent.isoformat() for sent in times]
        gap = float(settings.get("minimum_gap_seconds", 0))
        if gap and times and now - times[-1] < timedelta(seconds=gap):
            return False
        maximum = int(settings.get("max_alerts", 0))
        if maximum and interval:
            if len(times) >= maximum:
                return False
        return True

    def _mark_dispatched(self, watch: Watch, now: datetime) -> None:
        history = self.state.meta.setdefault("alert_history", {})
        if isinstance(history, dict):
            if self._throttle_settings(watch).get("interval_seconds"):
                history.setdefault(watch.label, []).append(now.isoformat())
            else:
                history[watch.label] = [now.isoformat()]
        self.state.save()

    @staticmethod
    def _serialize_alert(alert: Alert) -> dict[str, Any]:
        return {
            "watch": alert.watch.label,
            "event_type": alert.event_type,
            "created_at": alert.created_at.isoformat(),
            "message": alert.message,
            "slots": [
                {
                    "date": slot.slot_date.isoformat(),
                    "time": slot.slot_time,
                    "booking_url": slot.booking_url,
                    "seats": slot.seats,
                }
                for slot in alert.slots
            ],
        }

    def _pending(self) -> list[dict[str, Any]]:
        pending = self.state.meta.setdefault("pending_alerts", [])
        return pending if isinstance(pending, list) else []

    def _deliver_or_hold(self, alert: Alert, delivered: list[Alert]) -> None:
        now = self.clock()
        if self._is_quiet(alert.watch, now) or not self._can_dispatch(alert.watch, now):
            serialized = self._serialize_alert(alert)
            if serialized not in self._pending():
                self._pending().append(serialized)
                self.state.save()
            LOGGER.info(
                "held %s alert for %s",
                alert.event_type,
                alert.watch.label,
                extra={"event": "alert_held", "watch": alert.watch.label},
            )
            return
        self._dispatch(alert)
        self._mark_dispatched(alert.watch, now)
        delivered.append(alert)

    def _flush_pending(self, now: datetime, delivered: list[Alert]) -> None:
        pending = list(self._pending())
        if not pending:
            return
        watches = {watch.label: watch for watch in self.config.watches}
        remaining: list[dict[str, Any]] = []
        for item in pending:
            watch = watches.get(str(item.get("watch")))
            if watch is None or self._is_quiet(watch, now) or not self._can_dispatch(watch, now):
                remaining.append(item)
                continue
            try:
                alert = Alert(
                    watch=watch,
                    slots=tuple(
                        Slot(
                            watch,
                            date.fromisoformat(str(slot["date"])),
                            slot.get("time"),
                            slot.get("booking_url"),
                            int(slot.get("seats", 1)),
                        )
                        for slot in item.get("slots", [])
                    ),
                    created_at=datetime.fromisoformat(str(item["created_at"])),
                    event_type=str(item.get("event_type", "new")),
                    message=item.get("message"),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._dispatch(alert)
            self._mark_dispatched(watch, now)
            delivered.append(alert)
        self.state.meta["pending_alerts"] = remaining
        self.state.save()
