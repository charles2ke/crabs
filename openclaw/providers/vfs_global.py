"""VFS Global availability adapter."""

from __future__ import annotations

from typing import Any, Mapping

from ..models import Slot, Watch
from .base import Provider, ProviderError, register_provider
from .http_json import HttpJsonProvider
from ._portal_common import (
    build_slot,
    build_url,
    coerce_seats,
    ensure_no_sign_in_wall,
    ensure_not_html,
    http_status_from_error,
    value_at,
)


class VfsGlobalProvider(HttpJsonProvider, Provider):
    """Fetch slots from VFS-style JSON calendar endpoints."""

    name = "vfs-global"

    def fetch(self, watch: Watch) -> list[Slot]:
        options = dict(watch.options)
        base_url = str(options.get("base_url") or "")
        availability_path = str(options.get("availability_path") or "")
        if not base_url or not availability_path:
            raise ProviderError(
                f"watch {watch.label!r} is missing 'base_url' or 'availability_path' options"
            )
        missing = [
            key for key in ("centre_code", "category_code", "mission_code") if not options.get(key)
        ]
        if missing:
            raise ProviderError(
                f"watch {watch.label!r} is missing required VFS option(s): {', '.join(missing)}"
            )

        query = dict(options.get("query") or {})
        query[str(options.get("centre_param", "centreCode"))] = options.get("centre_code")
        query[str(options.get("category_param", "categoryCode"))] = options.get("category_code")
        query[str(options.get("sub_category_param", "subCategoryCode"))] = options.get(
            "sub_category_code"
        )
        query[str(options.get("mission_param", "missionCode"))] = options.get("mission_code")

        url = build_url(base_url, availability_path, query)
        headers = dict(options.get("headers") or {})
        auth = dict(options.get("auth") or {"type": "none"})
        try:
            payload = self._get_json_with_auth(watch, url, headers, auth)
        except ProviderError as exc:
            status = http_status_from_error(exc)
            if status == 429:
                raise ProviderError(
                    f"request to {url!r} was rate limited (HTTP 429); keep poll interval/jitter conservative"
                ) from exc
            if status is not None and 500 <= status <= 599:
                raise ProviderError(
                    f"request to {url!r} failed with HTTP {status}; portal appears temporarily unavailable"
                ) from exc
            raise
        return parse_vfs_availability(watch, payload, options, request_url=url)


def parse_vfs_availability(
    watch: Watch,
    payload: Any,
    options: Mapping[str, Any],
    *,
    request_url: str,
) -> list[Slot]:
    """Convert a VFS-style availability payload into :class:`Slot` entries."""
    ensure_not_html(payload, request_url)
    ensure_no_sign_in_wall(payload, request_url)

    response = dict(options.get("response") or {})
    items = value_at(payload, str(response.get("items_path", "data.days")), default=payload)
    if not isinstance(items, list):
        raise ProviderError("VFS response does not contain a list of availability days")

    date_key = str(response.get("date_key", "date"))
    date_format = str(response.get("date_format", "%Y-%m-%d"))
    times_key = str(response.get("times_key", "times"))
    time_key = str(response.get("time_key", "time"))
    seats_key = str(response.get("seats_key", "count"))
    booking_key = str(response.get("booking_url_key", "bookingUrl"))
    default_booking_url = build_url(
        str(options.get("base_url") or ""),
        str(options.get("booking_path") or ""),
        dict(options.get("booking_query") or {}),
    )

    slots: list[Slot] = []
    for day in items:
        if not isinstance(day, Mapping) or date_key not in day:
            raise ProviderError(f"VFS day entry is missing required {date_key!r}")

        times = day.get(times_key)
        if isinstance(times, list):
            for entry in times:
                if not isinstance(entry, Mapping):
                    raise ProviderError("VFS times entries must be objects")
                slot = build_slot(
                    watch,
                    date_value=day[date_key],
                    date_format=date_format,
                    slot_time=str(entry.get(time_key)) if entry.get(time_key) else None,
                    seats=coerce_seats(entry.get(seats_key), default=coerce_seats(day.get(seats_key), 1)),
                    booking_url=str(entry.get(booking_key) or day.get(booking_key) or default_booking_url),
                )
                if slot:
                    slots.append(slot)
            continue

        slot = build_slot(
            watch,
            date_value=day[date_key],
            date_format=date_format,
            slot_time=str(day.get(time_key)) if day.get(time_key) else None,
            seats=coerce_seats(day.get(seats_key), 1),
            booking_url=str(day.get(booking_key) or default_booking_url),
        )
        if slot:
            slots.append(slot)
    return slots


register_provider(VfsGlobalProvider.name, VfsGlobalProvider)
