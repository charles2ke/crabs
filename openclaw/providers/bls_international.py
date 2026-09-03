"""BLS International availability adapter."""

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


class BlsInternationalProvider(HttpJsonProvider, Provider):
    """Fetch slots from BLS-style availability endpoints."""

    name = "bls-international"

    def fetch(self, watch: Watch) -> list[Slot]:
        options = dict(watch.options)
        base_url = str(options.get("base_url") or "")
        availability_path = str(options.get("availability_path") or "")
        if not base_url or not availability_path:
            raise ProviderError(
                f"watch {watch.label!r} is missing 'base_url' or 'availability_path' options"
            )

        query = dict(options.get("query") or {})
        query[str(options.get("centre_param", "centreCode"))] = options.get("centre_code")
        query[str(options.get("category_param", "visaCategoryCode"))] = options.get("category_code")
        query[str(options.get("sub_category_param", "visaSubCategoryCode"))] = options.get(
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
        return parse_bls_availability(watch, payload, options, request_url=url)


def parse_bls_availability(
    watch: Watch,
    payload: Any,
    options: Mapping[str, Any],
    *,
    request_url: str,
) -> list[Slot]:
    """Convert a BLS-style availability payload into :class:`Slot` entries."""
    ensure_not_html(payload, request_url)
    ensure_no_sign_in_wall(payload, request_url)

    response = dict(options.get("response") or {})
    items = value_at(payload, str(response.get("items_path", "availability")), default=payload)
    if not isinstance(items, list):
        raise ProviderError("BLS response does not contain a list of availability slots")

    date_key = str(response.get("date_key", "appointmentDate"))
    date_format = str(response.get("date_format", "%Y-%m-%d"))
    time_key = str(response.get("time_key", "appointmentTime"))
    seats_key = str(response.get("seats_key", "available"))
    booking_key = str(response.get("booking_url_key", "bookingUrl"))
    default_booking_url = build_url(
        str(options.get("base_url") or ""),
        str(options.get("booking_path") or ""),
        dict(options.get("booking_query") or {}),
    )

    slots: list[Slot] = []
    for item in items:
        if not isinstance(item, Mapping) or date_key not in item:
            raise ProviderError(f"BLS slot entry is missing required {date_key!r}")
        slot = build_slot(
            watch,
            date_value=item[date_key],
            date_format=date_format,
            slot_time=str(item.get(time_key)) if item.get(time_key) else None,
            seats=coerce_seats(item.get(seats_key), 1),
            booking_url=str(item.get(booking_key) or default_booking_url),
        )
        if slot:
            slots.append(slot)
    return slots


register_provider(BlsInternationalProvider.name, BlsInternationalProvider)
