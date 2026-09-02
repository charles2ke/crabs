"""Alert sinks: how users get told about newly available slots."""

from __future__ import annotations

import abc
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, IO, Mapping

from .models import Alert

DEFAULT_TIMEOUT = 15.0


class Notifier(abc.ABC):
    """Base class for alert sinks."""

    name: str = "base"

    @abc.abstractmethod
    def send(self, alert: Alert) -> None:
        """Deliver ``alert``. Failures should raise :class:`NotifierError`."""


class NotifierError(RuntimeError):
    """Raised when an alert could not be delivered."""


class ConsoleNotifier(Notifier):
    """Print alerts to a stream (stdout by default)."""

    name = "console"

    def __init__(self, stream: IO[str] | None = None) -> None:
        self.stream = stream if stream is not None else sys.stdout

    def send(self, alert: Alert) -> None:
        self.stream.write(f"[{alert.created_at.isoformat(timespec='seconds')}] ")
        self.stream.write(alert.to_text() + "\n")
        self.stream.flush()


class FileNotifier(Notifier):
    """Append alerts to a JSON Lines file, useful for auditing runs."""

    name = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def send(self, alert: Alert) -> None:
        record = {
            "created_at": alert.created_at.isoformat(),
            "watch": alert.watch.label,
            "slots": [slot.describe() for slot in alert.slots],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError as exc:
            raise NotifierError(f"cannot write alert to {self.path}: {exc}") from exc


class WebhookNotifier(Notifier):
    """POST alerts as JSON to a webhook (Slack, Discord, ntfy, ...)."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise NotifierError(f"unsupported webhook URL scheme {scheme!r}")
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        payload: dict[str, Any] = {
            "text": alert.to_text(),
            "watch": alert.watch.label,
            "created_at": alert.created_at.isoformat(),
            "slots": [
                {
                    "date": slot.slot_date.isoformat(),
                    "time": slot.slot_time,
                    "seats": slot.seats,
                    "booking_url": slot.booking_url,
                }
                for slot in alert.slots
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        for key, value in self.headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(  # noqa: S310 - scheme validated in __init__
                request, timeout=self.timeout
            ) as response:
                response.read(1024)
        except (urllib.error.URLError, OSError) as exc:
            raise NotifierError(f"webhook delivery failed: {exc}") from exc


def build_notifier(spec: Mapping[str, Any]) -> Notifier:
    """Create a notifier from a config mapping such as ``{"type": "console"}``."""
    kind = str(spec.get("type", "console")).lower()
    if kind == ConsoleNotifier.name:
        return ConsoleNotifier()
    if kind == FileNotifier.name:
        path = spec.get("path")
        if not path:
            raise NotifierError("file notifier requires a 'path'")
        return FileNotifier(path)
    if kind == WebhookNotifier.name:
        url = spec.get("url")
        if not url:
            raise NotifierError("webhook notifier requires a 'url'")
        return WebhookNotifier(url, spec.get("headers"), float(spec.get("timeout", DEFAULT_TIMEOUT)))
    raise NotifierError(f"unknown notifier type {kind!r}")
