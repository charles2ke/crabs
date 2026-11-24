"""Dead man's switch: ping a monitoring URL after every completed cycle.

Services such as healthchecks.io or an Uptime Kuma push monitor raise an alarm
when the pings stop, which catches a watcher that silently stopped running. A
failed ping is logged and never fails the run.
"""

from __future__ import annotations

import http.client
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from .config import HEARTBEAT_DEFAULT_TIMEOUT

LOGGER = logging.getLogger("openclaw")


def heartbeat_endpoint(url: str) -> str:
    """Return only the scheme and host, because ping URLs embed a secret token."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    return f"{parsed.scheme}://{parsed.hostname or ''}"


def _reason(exc: BaseException) -> str:
    # Never str() the request itself: the URL path is the secret.
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    if isinstance(exc, OSError):
        return str(exc) or type(exc).__name__
    return type(exc).__name__


def send_heartbeat(settings: Mapping[str, Any]) -> bool:
    """GET the configured heartbeat URL; return whether it answered 2xx.

    Never raises: the heartbeat must not turn a good run into a failed one.
    """
    url = str(settings["url"])
    timeout = float(settings.get("timeout", HEARTBEAT_DEFAULT_TIMEOUT))
    endpoint = heartbeat_endpoint(url)
    try:
        with urllib.request.urlopen(  # noqa: S310 - scheme validated by config
            url, timeout=timeout
        ) as response:
            status = response.getcode()
            response.read(1024)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        LOGGER.warning(
            "heartbeat ping to %s failed: %s",
            endpoint,
            _reason(exc),
            extra={"event": "heartbeat_error"},
        )
        return False
    if not 200 <= status < 300:
        LOGGER.warning(
            "heartbeat ping to %s failed: HTTP %s",
            endpoint,
            status,
            extra={"event": "heartbeat_error"},
        )
        return False
    LOGGER.debug("heartbeat ping to %s sent", endpoint, extra={"event": "heartbeat"})
    return True
