"""Alert sinks: how users get told about newly available slots."""

from __future__ import annotations

import abc
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any, Callable, IO, Mapping

from .models import Alert, Slot

if TYPE_CHECKING:
    from .auth import Session

DEFAULT_TIMEOUT = 15.0
TELEGRAM_MESSAGE_LIMIT = 4096
MAX_TELEGRAM_RETRY_AFTER = 60.0
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"bot[0-9]+:[A-Za-z0-9_-]+")


class Notifier(abc.ABC):
    """Base class for alert sinks."""

    name: str = "base"

    @abc.abstractmethod
    def send(self, alert: Alert) -> None:
        """Deliver ``alert``. Failures should raise :class:`NotifierError`."""


class NotifierError(RuntimeError):
    """Raised when an alert could not be delivered."""


def redact_telegram_token(text: str, token: str | None = None) -> str:
    """Return ``text`` with Telegram Bot API tokens removed."""
    redacted = text
    if token:
        redacted = redacted.replace(f"bot{token}", "bot<redacted>")
    return _TELEGRAM_BOT_TOKEN_PATTERN.sub("bot<redacted>", redacted)


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


class TelegramNotifier(Notifier):
    """Send alerts via Telegram Bot API ``sendMessage``."""

    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        disable_notification: bool = False,
        session: "Session | None" = None,
        sleeper: Callable[[float], None] = sleep,
        message_limit: int = TELEGRAM_MESSAGE_LIMIT,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self.disable_notification = disable_notification
        if session is None:
            from .auth import Session

            session = Session(timeout=timeout)
        self.session = session
        self.sleeper = sleeper
        self.message_limit = message_limit
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, alert: Alert) -> None:
        for message in self._format_messages(alert):
            self._send_message(message)

    def _format_messages(self, alert: Alert) -> list[str]:
        header = self._format_header(alert)
        continuation_header = self._format_header(alert, continuation=True)
        messages: list[str] = []
        current = header
        current_slots = 0
        for slot in alert.slots:
            line = self._format_slot(slot)
            candidate = f"{current}\n{line}"
            if len(candidate) <= self.message_limit:
                current = candidate
                current_slots += 1
                continue
            if current_slots == 0:
                raise NotifierError("telegram message slot line exceeds Telegram length limit")

            messages.append(current)
            current = f"{continuation_header}\n{line}"
            if len(current) > self.message_limit:
                raise NotifierError("telegram message slot line exceeds Telegram length limit")
            current_slots = 1
        messages.append(current)
        return messages

    @staticmethod
    def _format_header(alert: Alert, *, continuation: bool = False) -> str:
        count = len(alert.slots)
        suffix = " (continued)" if continuation else ""
        city = html.escape(str(alert.watch.city or ""))
        country_from = html.escape(str(alert.watch.country_from or ""))
        country_to = html.escape(str(alert.watch.country_to or ""))
        visa_category = html.escape(str(alert.watch.visa_category or ""))
        return "\n".join(
            [
                f"<b>{count} new Schengen slot(s){suffix}</b>",
                f"Centre: {city} ({country_from})",
                f"Destination: {country_to}",
                f"Visa category: {visa_category}",
            ]
        )

    @staticmethod
    def _format_slot(slot: Slot) -> str:
        when = html.escape(slot.slot_date.isoformat())
        if slot.slot_time:
            when = f"{when} {html.escape(slot.slot_time)}"
        line = f"• {when} — {html.escape(str(slot.seats))} seat(s)"
        if slot.booking_url:
            url = html.escape(slot.booking_url, quote=True)
            line = f'{line} — <a href="{url}">booking link</a>'
        return line

    def _send_message(self, text: str) -> None:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if self.disable_notification:
            payload["disable_notification"] = True
        self._post(payload, allow_retry=True)

    def _post(self, payload: Mapping[str, Any], *, allow_retry: bool) -> None:
        body = json.dumps(dict(payload)).encode("utf-8")
        try:
            status, raw = self.session.request(
                self.url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                method="POST",
                body=body,
                expect_json=True,
            )
        except RuntimeError as exc:
            raise NotifierError(
                f"telegram delivery failed: {redact_telegram_token(str(exc), self.bot_token)}"
            ) from None

        response = self._decode_response(status, raw)
        if 200 <= status < 300 and response.get("ok") is True:
            return

        retry_after = self._retry_after(response)
        if (
            allow_retry
            and status == 429
            and retry_after is not None
            and 0 < retry_after <= MAX_TELEGRAM_RETRY_AFTER
        ):
            self.sleeper(retry_after)
            self._post(payload, allow_retry=False)
            return

        description = str(response.get("description") or "Telegram API error")
        raise NotifierError(
            redact_telegram_token(
                f"telegram delivery failed: HTTP {status}: {description}", self.bot_token
            )
        )

    def _decode_response(self, status: int, raw: bytes) -> dict[str, Any]:
        if not raw:
            return {"ok": False, "description": f"empty response (HTTP {status})"}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NotifierError(
                redact_telegram_token(
                    f"telegram delivery failed: invalid JSON response: {exc}", self.bot_token
                )
            ) from exc
        if not isinstance(payload, dict):
            raise NotifierError("telegram delivery failed: response is not a JSON object")
        return payload

    @staticmethod
    def _retry_after(response: Mapping[str, Any]) -> float | None:
        parameters = response.get("parameters")
        if not isinstance(parameters, Mapping) or "retry_after" not in parameters:
            return None
        try:
            return float(parameters["retry_after"])
        except (TypeError, ValueError):
            return None


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
        try:
            timeout = float(spec.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError) as exc:
            raise NotifierError(f"invalid webhook timeout: {exc}") from exc
        return WebhookNotifier(url, spec.get("headers"), timeout)
    if kind == TelegramNotifier.name:
        bot_token = spec.get("bot_token")
        chat_id = spec.get("chat_id")
        if not bot_token:
            raise NotifierError("telegram notifier requires a 'bot_token'")
        if not chat_id:
            raise NotifierError("telegram notifier requires a 'chat_id'")
        try:
            timeout = float(spec.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError) as exc:
            raise NotifierError(f"invalid telegram timeout: {exc}") from exc
        return TelegramNotifier(
            str(bot_token),
            str(chat_id),
            timeout=timeout,
            disable_notification=bool(spec.get("disable_notification", False)),
        )
    raise NotifierError(f"unknown notifier type {kind!r}")
