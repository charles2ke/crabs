"""Core data models for the Open Claw Schengen slot watcher."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class Watch:
    """A single thing to watch: a consulate/centre in a source country.

    ``country_from`` is where the applicant applies from (e.g. ``"IE"`` for
    Ireland), ``country_to`` is the Schengen state being applied to
    (e.g. ``"FR"``), and ``city`` is the appointment centre location
    (e.g. ``"Dublin"``).
    """

    country_from: str
    country_to: str
    city: str
    visa_category: str = "short-stay"
    provider: str = "mock"
    # Provider specific settings (endpoint URLs, centre ids, ...).
    options: Mapping[str, Any] = field(default_factory=dict)
    alert_on: tuple[str, ...] = ("new",)
    quiet_hours: Mapping[str, Any] | None = None
    throttle: Mapping[str, Any] = field(default_factory=dict)
    health: Mapping[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return (
            f"{self.country_to} consulate in {self.city}, {self.country_from} "
            f"({self.visa_category})"
        )


@dataclass(frozen=True)
class Slot:
    """An appointment slot advertised by a provider."""

    watch: Watch
    slot_date: date
    slot_time: str | None = None
    booking_url: str | None = None
    seats: int = 1

    @property
    def key(self) -> str:
        """Stable identifier used to avoid alerting twice for the same slot."""
        raw = "|".join(
            [
                self.watch.country_from,
                self.watch.country_to,
                self.watch.city,
                self.watch.visa_category,
                self.slot_date.isoformat(),
                self.slot_time or "",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def describe(self) -> str:
        when = self.slot_date.isoformat()
        if self.slot_time:
            when = f"{when} {self.slot_time}"
        text = f"{when} - {self.watch.label} - {self.seats} seat(s)"
        if self.booking_url:
            text = f"{text} - {self.booking_url}"
        return text


@dataclass(frozen=True)
class Alert:
    """A slot-change or provider-health event for one watch."""

    watch: Watch
    slots: tuple[Slot, ...]
    created_at: datetime
    event_type: str = "new"
    message: str | None = None

    def to_text(self) -> str:
        if self.event_type == "health":
            return f"Health warning for {self.watch.label}: {self.message or 'provider is stale'}"
        event = {
            "new": "new",
            "disappeared": "disappeared",
            "improved": "improved",
        }.get(self.event_type, self.event_type)
        lines = [f"{len(self.slots)} {event} Schengen slot(s) for {self.watch.label}:"]
        lines.extend(f"  * {slot.describe()}" for slot in self.slots)
        return "\n".join(lines)
