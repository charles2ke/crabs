# Providers and authentication

| Provider | Required options | Notes |
| --- | --- | --- |
| `mock` | none; optional `slots` or `file` | Offline demos and tests. Each slot sets a fixed `date` or `in_days` (days from today in UTC, so demos never go stale), plus optional `time`, `seats`, and `booking_url`. |
| `http-json` | `url` | Generic JSON list parser. |
| `vfs-global` | `base_url`, `availability_path`, `centre_code`, `category_code`, `mission_code` | Permitted JSON availability paths only. |
| `tlscontact` | `base_url`, `availability_path`, `location_code`, `category_code`, `destination_code` | Permitted JSON calendar paths only. |
| `bls-international` | `base_url`, `availability_path`, `centre_code`, `category_code`, `mission_code` | Permitted JSON availability paths only. |

`http-json` supports `headers`, `items_key`, `date_key` (default `date`),
`date_format` (default `%Y-%m-%d`), `time_key`, `seats_key`, `booking_url`, and
`auth`. Zero-seat entries are ignored.

The partner adapters additionally support `sub_category_code`, static `query`
and `headers`, `booking_path`/`booking_query`, and a declarative `response`
mapping (`items_path`, date/time/seats keys, and related shape settings).

## Authentication

Authentication is lazy and in-memory. Sessions and tokens are reused across
cycles. A 401/403, login redirect, or expired token triggers one re-authentication
and one retry. Credentials are never written to state.

Form authentication posts configured fields and optionally extracts CSRF:

```jsonc
"auth": {
  "type": "form",
  "login_url": "https://portal.example/login",
  "fields": {"username": "${OPENCLAW_USER}", "password": "${OPENCLAW_PASS}"},
  "encoding": "form",
  "csrf": {"url": "https://portal.example/login", "regex": "value=\"([^\"]+)\"", "field": "_csrf"},
  "success_status": [200, 302]
}
```

Token auth posts `body`, reads `token_key` (dotted paths supported), optionally
reads `expires_key`, and applies `header`/`header_format`. Basic auth uses
`username` and `password`. All credential values should come from environment
variables.

```jsonc
"auth": {
  "type": "token",
  "login_url": "https://portal.example/api/auth",
  "body": {"email": "${OPENCLAW_USER}", "password": "${OPENCLAW_PASS}"},
  "token_key": "access_token",
  "expires_key": "expires_in",
  "header": "Authorization",
  "header_format": "Bearer {token}"
}
```

```jsonc
"auth": {
  "type": "basic",
  "username": "${OPENCLAW_USER}",
  "password": "${OPENCLAW_PASS}"
}
```

Adapters reject sign-in pages, malformed payloads, and CAPTCHA/anti-bot gates.
They do not circumvent controls. A detected CAPTCHA, anti-bot, or WAF challenge
raises `ChallengeError` and is reported as a provider-health warning so an
operator can intervene manually. Confirm portal terms and local law and stop
polling when requested.

## Rate limits and Retry-After

The HTTP providers (`http-json`, `vfs-global`, `tlscontact`, and
`bls-international`) raise `RateLimitError`, a `ProviderError` subclass, when a
portal answers HTTP 429, or answers HTTP 503 with a `Retry-After` header (even
an empty or malformed one). This applies to slot requests and to login, CSRF,
and token requests. A rate-limited login is not retried.
`RateLimitError.retry_after` holds the requested delay in seconds, parsed from
either a number of seconds or an HTTP date. It is `None` when the header is
missing or invalid. When such a 429 or 503 response is a CAPTCHA or anti-bot
challenge, the provider raises `ChallengeError` instead, with the same
`retry_after`; the challenge still counts toward the health warning.

The monitor then pauses that watch until the requested time, counted from when
the portal answered and capped at 24 hours, and stores the pause in the state
file. Later cycles and scheduled runs skip the watch until the pause ends. A
custom provider can do the same by raising
`RateLimitError(message, retry_after=seconds)` or
`ChallengeError(message, retry_after=seconds)`:

```python
from openclaw import RateLimitError

raise RateLimitError("portal asked us to slow down", retry_after=300)
```

Without a usable `Retry-After`, the watch simply fails that cycle and is polled
again on the next one. Keep `poll_interval` and `jitter` conservative either way.

## Adding a provider

```python
from openclaw import Provider, register_provider

class MyPortalProvider(Provider):
    name = "my-portal"

    def fetch(self, watch):
        ...  # return a list of openclaw.Slot

register_provider(MyPortalProvider.name, MyPortalProvider)
```

Keep endpoint parsing pure and add offline fixtures; see
[CONTRIBUTING.md](../CONTRIBUTING.md).
