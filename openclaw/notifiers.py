"""Alert sinks: how users get told about newly available slots."""

from __future__ import annotations

import abc
import html
import json
import math
import re
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any, Callable, IO, Iterable, Mapping, Sequence

from .models import Alert, Slot

if TYPE_CHECKING:
    from .auth import Session

DEFAULT_TIMEOUT = 15.0
TELEGRAM_MESSAGE_LIMIT = 4096
MAX_TELEGRAM_RETRY_AFTER = 60.0
SLACK_BLOCK_TEXT_LIMIT = 3000
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"bot[0-9]+:[A-Za-z0-9_-]+")


def _validate_http_url(url: Any, label: str) -> str:
    """Return ``url`` if it is a usable HTTP(S) URL, else raise."""
    if not url:
        raise NotifierError(f"{label} requires a URL")
    parsed = urllib.parse.urlparse(str(url))
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise NotifierError(f"unsupported {label} URL scheme {scheme!r}")
    if not parsed.hostname:
        raise NotifierError(f"{label} URL must include a host")
    return str(url)


def _validate_timeout(timeout: Any, label: str) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise NotifierError(f"invalid {label} timeout: {exc}") from exc
    if not math.isfinite(value) or value <= 0:
        raise NotifierError(f"{label} timeout must be greater than 0")
    return value


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    timeout: float,
    label: str,
    headers: Mapping[str, str] | None = None,
) -> None:
    """POST ``payload`` as JSON and raise :class:`NotifierError` on failure."""
    body = json.dumps(dict(payload)).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(  # noqa: S310 - scheme validated by caller
            request, timeout=timeout
        ) as response:
            status = response.getcode()
            response.read(1024)
            if status < 200 or status >= 300:
                raise NotifierError(f"{label} delivery failed: HTTP {status}")
    except (urllib.error.URLError, OSError) as exc:
        raise NotifierError(f"{label} delivery failed: {exc}") from exc


def _fit_lines(header: str, lines: Sequence[str], limit: int) -> str:
    """Join ``header`` and ``lines`` within ``limit`` characters.

    Slot lines that do not fit are replaced with a summary of how many were
    omitted, so real portals with hundreds of slots never break delivery.
    """
    text = header
    for index, line in enumerate(lines):
        remaining = len(lines) - index
        candidate = f"{text}\n{line}"
        overflow = f"\n… and {remaining} more slot(s)"
        if len(candidate) + (len(overflow) if remaining > 1 else 0) > limit:
            truncated = f"{text}\n… and {remaining} more slot(s)"
            return truncated if len(truncated) <= limit else text[:limit]
        text = candidate
    return text


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
            "event_type": alert.event_type,
            "message": alert.message,
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
        self.url = _validate_http_url(url, "webhook")
        self.headers = dict(headers or {})
        self.timeout = _validate_timeout(timeout, "webhook")

    def send(self, alert: Alert) -> None:
        payload: dict[str, Any] = {
            "text": alert.to_text(),
            "watch": alert.watch.label,
            "event_type": alert.event_type,
            "message": alert.message,
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
                status = response.getcode()
                response.read(1024)
                if status < 200 or status >= 300:
                    raise NotifierError(f"webhook delivery failed: HTTP {status}")
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
        if alert.event_type == "health":
            return [self._format_header(alert, 0)]
        chunks: list[list[str]] = []
        current_lines: list[str] = []
        for slot in alert.slots:
            line = self._format_slot(slot)
            header = self._format_header(
                alert, len(current_lines) + 1, continuation=bool(chunks)
            )
            candidate = "\n".join([header, *current_lines, line])
            if len(candidate) <= self.message_limit:
                current_lines.append(line)
                continue
            if not current_lines:
                raise NotifierError(
                    "single slot line exceeds the Telegram message length limit"
                )

            chunks.append(current_lines)
            current_lines = [line]
            header = self._format_header(alert, 1, continuation=True)
            if len("\n".join([header, line])) > self.message_limit:
                raise NotifierError(
                    "continuation slot line exceeds the Telegram message length limit"
                )
        chunks.append(current_lines)
        return [
            "\n".join(
                [
                    self._format_header(alert, len(chunk), continuation=index > 0),
                    *chunk,
                ]
            )
            for index, chunk in enumerate(chunks)
        ]

    @staticmethod
    def _format_header(alert: Alert, count: int, *, continuation: bool = False) -> str:
        suffix = " (continued)" if continuation else ""
        city = html.escape(str(alert.watch.city or ""))
        country_from = html.escape(str(alert.watch.country_from or ""))
        country_to = html.escape(str(alert.watch.country_to or ""))
        visa_category = html.escape(str(alert.watch.visa_category or ""))
        if alert.event_type == "health":
            heading = "<b>Provider health warning</b>"
        else:
            event = html.escape(alert.event_type)
            heading = f"<b>{count} {event} Schengen slot(s){suffix}</b>"
        lines = [
            heading,
            f"Centre: {city} ({country_from})",
            f"Destination: {country_to}",
            f"Visa category: {visa_category}",
        ]
        if alert.message:
            lines.append(html.escape(alert.message))
        return "\n".join(lines)

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


class SlackNotifier(Notifier):
    """Post alerts to a Slack incoming webhook using Block Kit sections."""

    name = "slack"

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        username: str | None = None,
        icon_emoji: str | None = None,
    ) -> None:
        self.webhook_url = _validate_http_url(webhook_url, "slack webhook")
        self.timeout = _validate_timeout(timeout, "slack")
        self.username = username
        self.icon_emoji = icon_emoji

    def send(self, alert: Alert) -> None:
        summary = self._summary(alert)
        lines: list[str] = []
        if alert.message:
            lines.append(alert.message)
        lines.extend(self._format_slot(slot) for slot in alert.slots)
        body = _fit_lines(
            f"*{summary}*", lines,
            SLACK_BLOCK_TEXT_LIMIT,
        )
        payload: dict[str, Any] = {
            "text": summary,
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": body}},
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"{alert.watch.label} • "
                                f"{alert.created_at.isoformat(timespec='seconds')}"
                            ),
                        }
                    ],
                },
            ],
        }
        if self.username:
            payload["username"] = self.username
        if self.icon_emoji:
            payload["icon_emoji"] = self.icon_emoji
        _post_json(self.webhook_url, payload, self.timeout, "slack")

    @staticmethod
    def _summary(alert: Alert) -> str:
        if alert.event_type == "health":
            return f"Provider health warning for {alert.watch.label}"
        return (
            f"{len(alert.slots)} {alert.event_type} Schengen slot(s) for "
            f"{alert.watch.label}"
        )

    @staticmethod
    def _format_slot(slot: Slot) -> str:
        when = slot.slot_date.isoformat()
        if slot.slot_time:
            when = f"{when} {slot.slot_time}"
        line = f"• {when} — {slot.seats} seat(s)"
        if slot.booking_url:
            line = f"{line} — <{slot.booking_url}|booking link>"
        return line


class DiscordNotifier(Notifier):
    """Post alerts to a Discord webhook as a single embed."""

    name = "discord"

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        username: str | None = None,
    ) -> None:
        self.webhook_url = _validate_http_url(webhook_url, "discord webhook")
        self.timeout = _validate_timeout(timeout, "discord")
        self.username = username

    def send(self, alert: Alert) -> None:
        title = SlackNotifier._summary(alert)
        description = _fit_lines(
            alert.message or "",
            [self._format_slot(slot) for slot in alert.slots],
            DISCORD_EMBED_DESCRIPTION_LIMIT,
        ).strip()
        embed: dict[str, Any] = {
            "title": title[:256],
            "timestamp": alert.created_at.isoformat(),
        }
        if description:
            embed["description"] = description
        payload: dict[str, Any] = {"content": title[:2000], "embeds": [embed]}
        if self.username:
            payload["username"] = self.username
        _post_json(self.webhook_url, payload, self.timeout, "discord")

    @staticmethod
    def _format_slot(slot: Slot) -> str:
        when = slot.slot_date.isoformat()
        if slot.slot_time:
            when = f"{when} {slot.slot_time}"
        line = f"• {when} — {slot.seats} seat(s)"
        if slot.booking_url:
            line = f"{line} — [booking link]({slot.booking_url})"
        return line


class EmailNotifier(Notifier):
    """Send alerts as plain-text email through an SMTP server.

    ``use_tls=None`` means use STARTTLS unless ``use_ssl`` selects implicit TLS.
    Passing ``use_tls=True`` with ``use_ssl=True`` still raises because the two
    TLS modes are mutually exclusive.
    """

    name = "email"

    def __init__(
        self,
        host: str,
        recipients: Iterable[str],
        *,
        sender: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool | None = None,
        use_ssl: bool = False,
        subject_prefix: str = "[Open Claw]",
        timeout: float = DEFAULT_TIMEOUT,
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
    ) -> None:
        if not host:
            raise NotifierError("email notifier requires a 'host'")
        self.recipients = tuple(str(address) for address in recipients if address)
        if not self.recipients:
            raise NotifierError("email notifier requires at least one recipient")
        if not sender:
            raise NotifierError("email notifier requires a 'sender'")
        if use_tls is None:
            use_tls = not use_ssl
        elif use_tls and use_ssl:
            raise NotifierError(
                "email notifier cannot enable both 'use_tls' (STARTTLS) and "
                "'use_ssl' (implicit TLS); set only one"
            )
        if (username and not password) or (password and not username):
            raise NotifierError("email notifier requires both 'username' and 'password'")
        try:
            self.port = int(port)
        except (TypeError, ValueError) as exc:
            raise NotifierError(f"invalid email port: {exc}") from exc
        if not 1 <= self.port <= 65535:
            raise NotifierError("email port must be between 1 and 65535")
        self.host = str(host)
        self.sender = str(sender)
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.subject_prefix = subject_prefix
        self.timeout = _validate_timeout(timeout, "email")
        if smtp_factory is None:
            smtp_factory = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        self.smtp_factory = smtp_factory

    def send(self, alert: Alert) -> None:
        message = EmailMessage()
        subject = SlackNotifier._summary(alert)
        message["Subject"] = f"{self.subject_prefix} {subject}".strip()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(
            f"{alert.created_at.isoformat(timespec='seconds')}\n\n{alert.to_text()}\n"
        )
        try:
            with self.smtp_factory(self.host, self.port, timeout=self.timeout) as client:
                if self.use_tls:
                    client.starttls()
                if self.username and self.password:
                    client.login(self.username, self.password)
                client.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifierError(f"email delivery failed: {exc}") from exc


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
    if kind == SlackNotifier.name:
        return SlackNotifier(
            str(spec.get("webhook_url") or ""),
            timeout=spec.get("timeout", DEFAULT_TIMEOUT),
            username=_optional_str(spec.get("username")),
            icon_emoji=_optional_str(spec.get("icon_emoji")),
        )
    if kind == DiscordNotifier.name:
        return DiscordNotifier(
            str(spec.get("webhook_url") or ""),
            timeout=spec.get("timeout", DEFAULT_TIMEOUT),
            username=_optional_str(spec.get("username")),
        )
    if kind == EmailNotifier.name:
        recipients = spec.get("recipients")
        if isinstance(recipients, str):
            recipients = [recipients]
        if recipients is not None and not isinstance(recipients, list):
            raise NotifierError("email notifier 'recipients' must be a list of addresses")
        return EmailNotifier(
            str(spec.get("host") or ""),
            recipients or [],
            sender=str(spec.get("sender") or ""),
            port=spec.get("port", 587),
            username=_optional_str(spec.get("username")),
            password=_optional_str(spec.get("password")),
            use_tls=(
                bool(spec["use_tls"])
                if "use_tls" in spec and spec["use_tls"] is not None
                else None
            ),
            use_ssl=bool(spec.get("use_ssl", False)),
            subject_prefix=str(spec.get("subject_prefix", "[Open Claw]")),
            timeout=spec.get("timeout", DEFAULT_TIMEOUT),
        )
    raise NotifierError(f"unknown notifier type {kind!r}")


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
