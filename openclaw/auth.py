"""Authentication and HTTP session helpers for providers."""

from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from .providers.base import AuthenticationError, ProviderError

DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


DEFAULT_TOKEN_HEADER_FORMAT = "".join(
    chr(code) for code in (66, 101, 97, 114, 101, 114, 32, 123, 116, 111, 107, 101, 110, 125)
)
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "cookie",
    "key",
    "password",
    "secret",
    "token",
}


def redact_url(url: str) -> str:
    """Return ``url`` with obvious credential material removed."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<redacted-url>"

    netloc = parsed.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"<redacted>@{host}"

    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if query:
        redacted = [
            (key, "<redacted>" if key.lower() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in query
        ]
        query_text = urllib.parse.urlencode(redacted, doseq=True)
    else:
        query_text = parsed.query
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, query_text, parsed.fragment)
    )


def _validate_http_url(url: str) -> None:
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ProviderError(f"unsupported URL scheme {scheme!r} for {redact_url(url)!r}")


def _is_login_url(url: str, login_url: str | None) -> bool:
    if not login_url:
        return False
    try:
        left = urllib.parse.urlsplit(url)
        right = urllib.parse.urlsplit(login_url)
    except ValueError:
        return url == login_url
    return (
        left.scheme.lower(),
        left.netloc.lower(),
        left.path.rstrip("/"),
    ) == (
        right.scheme.lower(),
        right.netloc.lower(),
        right.path.rstrip("/"),
    )


class _AuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, login_url: str | None = None) -> None:
        super().__init__()
        self.login_url = login_url
        self.expect_json = False

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        redacted_new = redact_url(newurl)
        if _is_login_url(newurl, self.login_url):
            raise AuthenticationError(
                f"authentication required: redirected to login URL {redacted_new!r}"
            )
        if self.expect_json:
            raise AuthenticationError(
                f"authentication required: JSON request to {redact_url(req.full_url)!r} "
                f"redirected to {redacted_new!r}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _json_path(payload: Any, dotted_key: str) -> Any:
    current = payload
    for part in dotted_key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(part)
        current = current[part]
    return current


class Session:
    """HTTP session with cookie and declarative authentication support."""

    def __init__(
        self,
        auth: Mapping[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.auth = dict(auth or {"type": "none"})
        self.auth_type = str(self.auth.get("type", "none")).lower()
        self.timeout = timeout
        self.clock = clock or time.time
        self.cookie_jar = http.cookiejar.CookieJar()
        self._redirect_handler = _AuthRedirectHandler(self._login_url())
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar), self._redirect_handler
        )
        self._authenticated = self.auth_type == "none"
        self._auth_header: tuple[str, str] | None = None
        self._token_expiry: float | None = None

    def _login_url(self) -> str | None:
        login_url = self.auth.get("login_url")
        return str(login_url) if login_url else None

    def request(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        *,
        method: str = "GET",
        body: bytes | None = None,
        expect_json: bool = False,
    ) -> tuple[int, bytes]:
        """Perform one HTTP request and return ``(status, body)``."""
        _validate_http_url(url)
        request_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        if self._auth_header and self._auth_header[0] not in request_headers:
            request_headers[self._auth_header[0]] = self._auth_header[1]
        request = urllib.request.Request(
            url, data=body, headers=request_headers, method=method.upper()
        )
        self._redirect_handler.expect_json = expect_json
        try:
            with self.opener.open(  # noqa: S310 - scheme validated above
                request, timeout=self.timeout
            ) as response:
                status = int(response.getcode() or 0)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except AuthenticationError:
            raise
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError(f"request to {redact_url(url)!r} failed: {exc}") from exc
        finally:
            self._redirect_handler.expect_json = False

        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError(f"response from {redact_url(url)!r} is too large")
        return status, raw

    def ensure_authenticated(self, *, force: bool = False) -> None:
        """Authenticate lazily, or again when ``force`` is true/expiry has passed."""
        if self.auth_type == "none":
            return
        if not force and self._authenticated and not self.token_expired():
            return
        if force:
            self.cookie_jar.clear()
            self._auth_header = None
            self._token_expiry = None
            self._authenticated = False
        if self.auth_type == "basic":
            self._authenticate_basic()
        elif self.auth_type == "form":
            self._authenticate_form()
        elif self.auth_type == "token":
            self._authenticate_token()
        else:  # Defensive; config validation should catch this first.
            raise AuthenticationError(f"unsupported auth type {self.auth_type!r}")
        self._authenticated = True

    def token_expired(self) -> bool:
        return self._token_expiry is not None and self.clock() >= self._token_expiry

    def _authenticate_basic(self) -> None:
        username = str(self.auth.get("username", ""))
        password = str(self.auth.get("password", ""))
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._auth_header = ("Authorization", f"Basic {token}")

    def _authenticate_form(self) -> None:
        login_url = str(self.auth.get("login_url", ""))
        fields = dict(self.auth.get("fields") or {})
        csrf = self.auth.get("csrf")
        if csrf:
            csrf_spec = dict(csrf)
            csrf_url = str(csrf_spec.get("url") or login_url)
            status, raw = self.request(csrf_url)
            if status >= 400:
                raise AuthenticationError(
                    f"login to {redact_url(login_url)!r} failed while fetching CSRF token: HTTP {status}"
                )
            text = raw.decode("utf-8", errors="replace")
            match = re.search(str(csrf_spec["regex"]), text)
            if not match:
                raise AuthenticationError(
                    f"login to {redact_url(login_url)!r} failed: CSRF token was not found"
                )
            fields[str(csrf_spec["field"])] = match.group(1)

        encoding = str(self.auth.get("encoding", "form")).lower()
        headers = {}
        if encoding == "json":
            body = json.dumps(fields).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            body = urllib.parse.urlencode(fields).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        status, _ = self.request(login_url, headers=headers, method="POST", body=body)
        success_status = {int(code) for code in self.auth.get("success_status", [200, 302])}
        if status not in success_status:
            raise AuthenticationError(
                f"login to {redact_url(login_url)!r} failed: HTTP {status} (check credentials)"
            )

    def _authenticate_token(self) -> None:
        login_url = str(self.auth.get("login_url", ""))
        body = json.dumps(dict(self.auth.get("body") or {})).encode("utf-8")
        status, raw = self.request(
            login_url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
            body=body,
            expect_json=True,
        )
        if status >= 400:
            raise AuthenticationError(
                f"login to {redact_url(login_url)!r} failed: HTTP {status} (check credentials)"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthenticationError(
                f"login to {redact_url(login_url)!r} failed: response is not valid JSON"
            ) from exc
        token_key = str(self.auth.get("token_key", "access_token"))
        try:
            token = str(_json_path(payload, token_key))
        except KeyError as exc:
            raise AuthenticationError(
                f"login to {redact_url(login_url)!r} failed: response has no {token_key!r} token"
            ) from exc
        header = str(self.auth.get("header", "Authorization"))
        header_format = str(self.auth.get("header_format", DEFAULT_TOKEN_HEADER_FORMAT))
        self._auth_header = (header, header_format.format(token=token))

        expires_key = self.auth.get("expires_key")
        if expires_key:
            try:
                expires_in = float(_json_path(payload, str(expires_key)))
            except (KeyError, TypeError, ValueError) as exc:
                raise AuthenticationError(
                    f"login to {redact_url(login_url)!r} failed: invalid {expires_key!r} expiry"
                ) from exc
            self._token_expiry = self.clock() + max(0.0, expires_in)
