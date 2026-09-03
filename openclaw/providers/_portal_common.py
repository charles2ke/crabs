"""Shared helpers for declarative Schengen portal adapters."""

from __future__ import annotations

import re
import urllib.parse
from datetime import date, datetime
from typing import Any, Mapping

from ..auth import redact_url
from ..models import Slot, Watch
from .base import AuthenticationError, ProviderError

_PII_QUERY_KEYS = {
    "applicant",
    "application",
    "application_id",
    "application_number",
    "dob",
    "date_of_birth",
    "email",
    "first_name",
    "lastname",
    "last_name",
    "name",
    "passport",
    "passport_number",
    "reference",
    "surname",
}


def value_at(payload: Any, dotted_path: str | None, *, default: Any = None) -> Any:
    """Return a nested value from ``payload`` using dotted-path syntax."""
    if not dotted_path:
        return payload
    current = payload
    for part in dotted_path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        return default
    return current


def parse_slot_date(raw: Any, date_format: str) -> date:
    """Parse one slot date using the configured format."""
    try:
        return datetime.strptime(str(raw), date_format).date()
    except ValueError as exc:  # pragma: no cover - message asserted by callers
        raise ProviderError(f"cannot parse slot date: {exc}") from exc


def build_url(base_url: str, path: str, query: Mapping[str, Any] | None = None) -> str:
    """Join ``base_url`` and ``path`` and append ``query`` parameters."""
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if query:
        encoded = urllib.parse.urlencode(
            [(str(key), str(value)) for key, value in query.items() if value not in (None, "")]
        )
        if encoded:
            separator = "&" if urllib.parse.urlsplit(url).query else "?"
            url = f"{url}{separator}{encoded}"
    return url


def redact_booking_url(url: str | None) -> str | None:
    """Redact common personal identifiers from booking-link query parameters."""
    if not url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None

    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, "<redacted>" if key.lower() in _PII_QUERY_KEYS else value)
        for key, value in query_pairs
    ]
    query_text = urllib.parse.urlencode(redacted, doseq=True)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query_text, parsed.fragment)
    )


def ensure_not_html(payload: Any, context_url: str) -> None:
    """Reject payloads that are HTML documents or snippets."""
    if isinstance(payload, str):
        lowered = payload.lstrip().lower()
        if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
            raise ProviderError(
                f"response from {redact_url(context_url)!r} looks like an HTML page, not JSON — "
                "the portal may require sign in via a supported access path"
            )


def ensure_no_sign_in_wall(payload: Any, context_url: str) -> None:
    """Raise when payload appears to be a sign-in/captcha challenge response."""
    text_candidates: list[str] = []
    if isinstance(payload, Mapping):
        for key in ("message", "error", "detail", "status"):
            value = payload.get(key)
            if isinstance(value, str):
                text_candidates.append(value)
        if payload.get("authenticated") is False or payload.get("requiresLogin") is True:
            raise AuthenticationError(
                f"portal response from {redact_url(context_url)!r} indicates sign-in is required"
            )
    elif isinstance(payload, str):
        text_candidates.append(payload)

    marker_text = "\n".join(text_candidates).lower()
    if "captcha" in marker_text or "recaptcha" in marker_text or "bot" in marker_text:
        raise AuthenticationError(
            "portal requires CAPTCHA or anti-bot verification; "
            "Open Claw only supports permitted JSON endpoint access"
        )
    if "sign in" in marker_text or "login" in marker_text or "log in" in marker_text:
        raise AuthenticationError(
            f"portal response from {redact_url(context_url)!r} indicates sign-in is required"
        )


def coerce_seats(raw: Any, default: int = 1) -> int:
    """Convert seat-count values to integers."""
    try:
        seats = int(raw if raw is not None else default)
    except (TypeError, ValueError):
        seats = default
    return seats


def build_slot(
    watch: Watch,
    *,
    date_value: Any,
    date_format: str,
    slot_time: str | None,
    seats: int,
    booking_url: str | None,
) -> Slot | None:
    """Create a :class:`Slot`, skipping non-positive seat entries."""
    if seats <= 0:
        return None
    return Slot(
        watch=watch,
        slot_date=parse_slot_date(date_value, date_format),
        slot_time=slot_time,
        seats=seats,
        booking_url=redact_booking_url(booking_url),
    )


def http_status_from_error(exc: ProviderError) -> int | None:
    """Best-effort extraction of HTTP status code from provider error text."""
    match = re.search(r"HTTP\s+(\d{3})", str(exc))
    if not match:
        return None
    return int(match.group(1))
