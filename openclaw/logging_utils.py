"""Text and structured logging with the project's redaction guarantees."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .auth import redact_url
from .notifiers import redact_telegram_token
from .providers._portal_common import redact_booking_url

_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")
_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def redact_log_text(text: str) -> str:
    """Remove Telegram tokens and sensitive URL query values from log text."""

    def clean_url(match: re.Match[str]) -> str:
        url = redact_url(match.group(0))
        return redact_booking_url(url) or "<redacted-url>"

    return redact_telegram_token(_URL_PATTERN.sub(clean_url, text))


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", "log"),
            "message": redact_log_text(record.getMessage()),
        }
        if hasattr(record, "watch"):
            payload["watch"] = redact_log_text(str(record.watch))
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and key not in payload and key not in {
                "event",
                "watch",
            }:
                payload[key] = redact_log_text(str(value))
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(verbose: bool, log_format: str) -> None:
    """Configure the root logger for CLI use."""
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[handler],
        force=True,
    )
