"""Deterministic offline provider used for demos and tests.

It reads slots from a JSON file (``options.file``) or from inline
``options.slots`` entries, so the Dublin example can be run end to end without
hitting a real consulate portal.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import Slot, Watch
from .base import Provider, ProviderError, register_provider


class MockProvider(Provider):
    """Return slots described in the watch options."""

    name = "mock"

    def fetch(self, watch: Watch) -> list[Slot]:
        options = dict(watch.options)
        entries: Any = options.get("slots")
        source = options.get("file")
        if source:
            path = Path(source).expanduser()
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProviderError(f"cannot read mock slots from {path}: {exc}") from exc
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise ProviderError("mock provider expects a list of slot entries")

        slots: list[Slot] = []
        for entry in entries:
            if not isinstance(entry, dict) or "date" not in entry:
                raise ProviderError("mock slot entries need a 'date' key")
            try:
                slot_date = datetime.strptime(str(entry["date"]), "%Y-%m-%d").date()
            except ValueError as exc:
                raise ProviderError(f"cannot parse mock slot date: {exc}") from exc
            try:
                seats = int(entry.get("seats", 1))
            except (TypeError, ValueError) as exc:
                raise ProviderError(f"cannot parse mock slot seats: {exc}") from exc
            slots.append(
                Slot(
                    watch=watch,
                    slot_date=slot_date,
                    slot_time=entry.get("time"),
                    booking_url=entry.get("url") or options.get("booking_url"),
                    seats=seats,
                )
            )
        return slots


register_provider(MockProvider.name, MockProvider)
