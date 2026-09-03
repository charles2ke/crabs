"""Generic HTTP/JSON provider.

Most public appointment portals expose (or are fronted by) a JSON endpoint that
lists open days for a centre. Rather than hard-coding one portal, this provider
is configured declaratively so a new consulate can be watched by editing the
config file only.

Expected ``options``::

    {
      "url": "https://example.org/api/slots?centre=dublin",
      "headers": {"Accept": "application/json"},
      "items_key": "dates",          # optional: key holding the list
      "date_key": "date",
      "date_format": "%Y-%m-%d",
      "time_key": "time",            # optional
      "seats_key": "available",      # optional
      "booking_url": "https://example.org/book"
    }
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from ..models import Slot, Watch
from .base import Provider, ProviderError, register_provider

DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class HttpJsonProvider(Provider):
    """Fetch slots from a JSON HTTP endpoint described by the watch options."""

    name = "http-json"

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def fetch(self, watch: Watch) -> list[Slot]:
        options = dict(watch.options)
        url = options.get("url")
        if not url:
            raise ProviderError(f"watch {watch.label} is missing the 'url' option")

        payload = self._get_json(url, options.get("headers") or {})
        items = self._extract_items(payload, options.get("items_key"))
        return self._build_slots(watch, items, options)

    def _get_json(self, url: str, headers: dict[str, str]) -> Any:
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ProviderError(f"unsupported URL scheme {scheme!r} for {url!r}")

        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(  # noqa: S310 - scheme validated above
                request, timeout=self.timeout
            ) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError(f"request to {url!r} failed: {exc}") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError(f"response from {url!r} is too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"response from {url!r} is not valid JSON: {exc}") from exc

    @staticmethod
    def _extract_items(payload: Any, items_key: str | None) -> list[Any]:
        if items_key:
            if not isinstance(payload, dict) or items_key not in payload:
                raise ProviderError(f"response has no {items_key!r} key")
            payload = payload[items_key]
        if not isinstance(payload, list):
            raise ProviderError("expected a list of slots in the response")
        return payload

    @staticmethod
    def _build_slots(watch: Watch, items: list[Any], options: dict[str, Any]) -> list[Slot]:
        date_key = options.get("date_key", "date")
        date_format = options.get("date_format", "%Y-%m-%d")
        time_key = options.get("time_key")
        seats_key = options.get("seats_key")
        booking_url = options.get("booking_url")

        slots: list[Slot] = []
        for item in items:
            if not isinstance(item, dict) or date_key not in item:
                raise ProviderError(f"slot entry is missing the {date_key!r} key")
            try:
                slot_date = datetime.strptime(str(item[date_key]), date_format).date()
            except ValueError as exc:
                raise ProviderError(f"cannot parse slot date: {exc}") from exc

            seats = 1
            if seats_key:
                try:
                    seats = int(item.get(seats_key, 1))
                except (TypeError, ValueError):
                    seats = 1
            if seats <= 0:
                continue

            slot_time = None
            if time_key and item.get(time_key):
                slot_time = str(item[time_key])

            slots.append(
                Slot(
                    watch=watch,
                    slot_date=slot_date,
                    slot_time=slot_time,
                    booking_url=item.get("url") or booking_url,
                    seats=seats,
                )
            )
        return slots


register_provider(HttpJsonProvider.name, HttpJsonProvider)
