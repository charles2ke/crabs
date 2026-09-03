import json
import os
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs

from openclaw.auth import Session
from openclaw.config import ConfigError, parse_config
from openclaw.monitor import Monitor
from openclaw.models import Watch
from openclaw.providers import AuthenticationError, ProviderError
from openclaw.providers.http_json import HttpJsonProvider


class PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        return

    @property
    def state(self):
        return self.server.state  # type: ignore[attr-defined]

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/csrf":
            self._send(200, b'<input name="_csrf" value="csrf123">')
            return
        if self.path == "/redirect-slots":
            self._send(302, headers={"Location": "/login"})
            return
        if self.path == "/html":
            self._send(200, b"<html><body>login</body></html>")
            return
        if self.path == "/after-login":
            self._send(200, b"after")
            return
        if self.path == "/slots":
            self.state["slot_requests"] = self.state.get("slot_requests", 0) + 1
            auth = self.headers.get("Authorization")
            cookie = self.headers.get("Cookie", "")
            self.state.setdefault("seen_authorization", []).append(auth)
            self.state.setdefault("seen_cookie", []).append(cookie)
            mode = self.state.get("mode")
            if mode == "basic":
                if auth == self.state["expected_authorization"]:
                    self._send(200, b'[{"date":"2026-09-14"}]')
                else:
                    self._send(401)
                return
            if mode == "token":
                if auth == self.state.get("valid_auth"):
                    self._send(200, b'[{"date":"2026-09-14"}]')
                else:
                    self._send(401)
                return
            if mode == "cookie-retry" and self.state["slot_requests"] == 1:
                self._send(401)
                return
            if "session=ok" in cookie:
                self._send(200, b'[{"date":"2026-09-14"}]')
            else:
                self._send(401)
            return
        self._send(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/login":
            self.state["login_posts"] = self.state.get("login_posts", 0) + 1
            self.state["last_login_body"] = body.decode("utf-8")
            if self.state.get("login_fails") or self.state["login_posts"] > self.state.get(
                "fail_after_login_posts", 999999
            ):
                self._send(401)
            elif self.state.get("login_redirects"):
                self._send(
                    302,
                    headers={"Location": "/after-login", "Set-Cookie": "session=ok; Path=/"},
                )
            else:
                self._send(200, b"ok", {"Set-Cookie": "session=ok; Path=/"})
            return
        if self.path == "/token":
            self.state["login_posts"] = self.state.get("login_posts", 0) + 1
            token = f"tok{self.state['login_posts']}"
            self.state["valid_auth"] = "Bearer " + token
            payload = {"data": {"token": token}, "expires_in": self.state.get("expires_in", 3600)}
            self._send(
                200,
                json.dumps(payload).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
            return
        self._send(404)


class AuthServerTestCase(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
        self.server.state = {}
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def _watch(self, auth, path="/slots"):
        return Watch(
            "IE",
            "FR",
            "Dublin",
            provider="http-json",
            options={"url": self.base_url + path, "auth": auth},
        )

    def test_form_login_sets_cookie_for_slots_request(self):
        auth = {
            "type": "form",
            "login_url": self.base_url + "/login",
            "fields": {"username": "alice", "password": "secret"},
        }
        slots = HttpJsonProvider().fetch(self._watch(auth))
        self.assertEqual(len(slots), 1)
        self.assertEqual(self.server.state["login_posts"], 1)
        self.assertIn("session=ok", self.server.state["seen_cookie"][-1])

    def test_form_login_posts_csrf_token(self):
        auth = {
            "type": "form",
            "login_url": self.base_url + "/login",
            "fields": {"username": "alice", "password": "secret"},
            "csrf": {
                "url": self.base_url + "/csrf",
                "regex": 'name="_csrf" value="([^"]+)"',
                "field": "_csrf",
            },
        }
        HttpJsonProvider().fetch(self._watch(auth))
        posted = parse_qs(self.server.state["last_login_body"])
        self.assertEqual(posted["_csrf"], ["csrf123"])

    def test_form_login_accepts_default_302_success(self):
        self.server.state["login_redirects"] = True
        auth = {
            "type": "form",
            "login_url": self.base_url + "/login",
            "fields": {"username": "alice", "password": "secret"},
        }
        slots = HttpJsonProvider().fetch(self._watch(auth))
        self.assertEqual(len(slots), 1)
        self.assertEqual(self.server.state["login_posts"], 1)

    def test_token_login_injects_authorization_header_and_dotted_key(self):
        self.server.state["mode"] = "token"
        auth = {
            "type": "token",
            "login_url": self.base_url + "/token",
            "body": {"email": "alice", "password": "secret"},
            "token_key": "data.token",
        }
        slots = HttpJsonProvider().fetch(self._watch(auth))
        self.assertEqual(len(slots), 1)
        self.assertEqual(self.server.state["seen_authorization"][-1], self.server.state["valid_auth"])

    def test_basic_auth_header_is_sent(self):
        self.server.state["mode"] = "basic"
        self.server.state["expected_authorization"] = "Basic YWxpY2U6c2VjcmV0"
        auth = {"type": "basic", "username": "alice", "password": "secret"}
        slots = HttpJsonProvider().fetch(self._watch(auth))
        self.assertEqual(len(slots), 1)
        self.assertEqual(self.server.state["seen_authorization"][-1], "Basic YWxpY2U6c2VjcmV0")

    def test_401_triggers_exactly_one_relogin_then_succeeds(self):
        self.server.state["mode"] = "cookie-retry"
        auth = {
            "type": "form",
            "login_url": self.base_url + "/login",
            "fields": {"username": "alice", "password": "secret"},
        }
        slots = HttpJsonProvider().fetch(self._watch(auth))
        self.assertEqual(len(slots), 1)
        self.assertEqual(self.server.state["login_posts"], 2)
        self.assertEqual(self.server.state["slot_requests"], 2)

    def test_relogin_failing_raises_authentication_error_after_one_retry(self):
        self.server.state["mode"] = "cookie-retry"
        self.server.state["fail_after_login_posts"] = 1
        auth = {
            "type": "form",
            "login_url": self.base_url + "/login",
            "fields": {"username": "alice", "password": "secret"},
        }
        with self.assertRaises(AuthenticationError):
            HttpJsonProvider().fetch(self._watch(auth))
        self.assertEqual(self.server.state["login_posts"], 2)
        self.assertEqual(self.server.state["slot_requests"], 1)

    def test_expired_token_triggers_proactive_relogin(self):
        self.server.state["mode"] = "token"
        auth = {
            "type": "token",
            "login_url": self.base_url + "/token",
            "body": {"email": "alice", "password": "secret"},
            "token_key": "data.token",
            "expires_key": "expires_in",
        }
        now = [1000.0]
        session = Session(auth, clock=lambda: now[0])
        provider = HttpJsonProvider()
        provider._session_for = lambda watch, auth: session  # type: ignore[method-assign]
        provider.fetch(self._watch(auth))
        now[0] = 5000.0
        provider.fetch(self._watch(auth))
        self.assertEqual(self.server.state["login_posts"], 2)
        self.assertEqual(len(self.server.state["seen_authorization"]), 2)

    def test_zero_token_expiry_does_not_relogin_every_fetch(self):
        self.server.state["mode"] = "token"
        self.server.state["expires_in"] = 0
        auth = {
            "type": "token",
            "login_url": self.base_url + "/token",
            "body": {"email": "alice", "password": "secret"},
            "token_key": "data.token",
            "expires_key": "expires_in",
        }
        provider = HttpJsonProvider()
        provider.fetch(self._watch(auth))
        provider.fetch(self._watch(auth))
        self.assertEqual(self.server.state["login_posts"], 1)

    def test_redirect_to_login_raises_authentication_error(self):
        auth = {
            "type": "form",
            "login_url": self.base_url + "/login",
            "fields": {"username": "alice", "password": "secret"},
        }
        with self.assertRaises(AuthenticationError):
            HttpJsonProvider().fetch(self._watch(auth, path="/redirect-slots"))

    def test_html_response_mentions_sign_in(self):
        with self.assertRaisesRegex(ProviderError, "may require sign in"):
            HttpJsonProvider()._get_json(self.base_url + "/html", {})

    def test_no_auth_still_uses_legacy_get_json_path(self):
        provider = HttpJsonProvider()
        calls = []
        provider._get_json = lambda url, headers: calls.append((url, headers)) or []  # type: ignore[method-assign]
        provider.fetch(
            Watch(
                "IE",
                "FR",
                "Dublin",
                provider="http-json",
                options={"url": "https://portal.invalid/api"},
            )
        )
        self.assertEqual(calls, [("https://portal.invalid/api", {})])

    def test_secret_does_not_appear_in_logs_or_exception(self):
        self.server.state["login_fails"] = True
        secret = "super-secret-value"
        os.environ["OPENCLAW_TEST_PASS"] = secret
        auth = {
            "type": "form",
            "login_url": self.base_url + "/login",
            "fields": {"username": "alice", "password": secret},
        }
        watch = self._watch(auth)
        with self.assertRaises(AuthenticationError) as caught:
            HttpJsonProvider().fetch(watch)
        self.assertNotIn(secret, str(caught.exception))

        try:
            config = parse_config(
                {
                    "watches": [
                        {
                            "country_from": "IE",
                            "country_to": "FR",
                            "city": "Dublin",
                            "provider": "http-json",
                            "options": {
                                "url": self.base_url + "/slots",
                                "auth": {
                                    "type": "form",
                                    "login_url": self.base_url + "/login",
                                    "fields": {
                                        "username": "alice",
                                        "password": "${OPENCLAW_TEST_PASS}",
                                    },
                                },
                            },
                        },
                        {
                            "country_from": "IE",
                            "country_to": "ES",
                            "city": "Dublin",
                            "provider": "mock",
                            "options": {"slots": [{"date": "2026-09-14"}]},
                        },
                    ]
                }
            )
        finally:
            del os.environ["OPENCLAW_TEST_PASS"]
        with self.assertLogs("openclaw", level="WARNING") as logs:
            alerts = Monitor(config, notifiers=[], sleeper=lambda _: None).run_once()
        self.assertEqual(len(alerts), 1)
        self.assertNotIn(secret, "\n".join(logs.output))


class AuthConfigTests(unittest.TestCase):
    def test_config_rejects_literal_password_in_auth(self):
        with self.assertRaisesRegex(ConfigError, "literal secret"):
            parse_config(
                {
                    "watches": [
                        {
                            "country_from": "IE",
                            "country_to": "FR",
                            "city": "Dublin",
                            "provider": "http-json",
                            "options": {
                                "url": "https://portal.invalid/api",
                                "auth": {
                                    "type": "basic",
                                    "username": "alice",
                                    "password": "committed-password",
                                },
                            },
                        }
                    ]
                }
            )

    def test_config_accepts_env_password_placeholder(self):
        os.environ["OPENCLAW_PASS"] = "expanded-secret"
        try:
            config = parse_config(
                {
                    "watches": [
                        {
                            "country_from": "IE",
                            "country_to": "FR",
                            "city": "Dublin",
                            "provider": "http-json",
                            "options": {
                                "url": "https://portal.invalid/api",
                                "auth": {
                                    "type": "basic",
                                    "username": "alice",
                                    "password": "${OPENCLAW_PASS}",
                                },
                            },
                        }
                    ]
                }
            )
        finally:
            del os.environ["OPENCLAW_PASS"]
        self.assertEqual(config.watches[0].options["auth"]["password"], "expanded-secret")

    def test_config_rejects_invalid_form_success_status(self):
        os.environ["OPENCLAW_PASS"] = "expanded-secret"
        try:
            with self.assertRaisesRegex(ConfigError, "success_status"):
                parse_config(
                    {
                        "watches": [
                            {
                                "country_from": "IE",
                                "country_to": "FR",
                                "city": "Dublin",
                                "provider": "http-json",
                                "options": {
                                    "url": "https://portal.invalid/api",
                                    "auth": {
                                        "type": "form",
                                        "login_url": "https://portal.invalid/login",
                                        "fields": {
                                            "username": "alice",
                                            "password": "${OPENCLAW_PASS}",
                                        },
                                        "success_status": ["ok"],
                                    },
                                },
                            }
                        ]
                    }
                )
        finally:
            del os.environ["OPENCLAW_PASS"]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
