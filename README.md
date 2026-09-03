# crabs

Secure Open Claw for everyone

**Open Claw** is a small, dependency-free Python tool that watches Schengen visa
appointment portals for a given country and **alerts you about every available
interview slot**. The reference use case shipped in this repo is applying from
**Ireland, at the Schengen consulates / visa centres in Dublin**.

## Why

Schengen appointment slots in Dublin are released irregularly and disappear in
minutes. Open Claw polls the portals you configure, keeps track of what it has
already seen, and alerts you the moment a *new* slot shows up — on the console,
in a JSON Lines log file, or to a webhook (Slack, Discord, ntfy, …).

## Features

- **Watch many consulates at once** — one entry per country/city/visa category.
- **Pluggable providers** — a generic `http-json` provider is configured
  declaratively (URL, keys, date format), so a new portal needs a config edit
  only, not code. Built-in adapters are available for `vfs-global`,
  `tlscontact`, and `bls-international` when you have a permitted JSON access
  path. A `mock` provider makes demos and tests fully offline.
- **Alert on every available slot**, de-duplicated so you are notified once per
  slot; if a slot disappears and comes back it alerts again.
- **Multiple alert sinks** — `console`, `file` (JSON Lines), `webhook`.
- **Date window filtering** with `earliest` / `latest`.
- **Persistent state** so restarts do not replay old alerts.
- **Polite polling** — configurable `poll_interval` plus random `jitter`.
- **Secrets stay out of config files** — `${ENV_VAR}` placeholders are expanded
  from the environment.
- Python 3.10+, standard library only.

## Install

```bash
git clone https://github.com/charles2ke/crabs.git
cd crabs
pip install -e .          # optional; the CLI also runs straight from the checkout
```

## Quick start — the Dublin example

`examples/dublin.json` watches the French and Spanish Schengen appointment
centres in Dublin using the offline `mock` provider, so you can try the whole
flow without touching a real portal:

```bash
python -m openclaw --config examples/dublin.json --once
```

```text
[2026-09-02T20:36:21+00:00] 2 new Schengen slot(s) for FR consulate in Dublin, IE (short-stay):
  * 2026-09-14 09:20 - FR consulate in Dublin, IE (short-stay) - 2 seat(s) - https://example.invalid/dublin/fr/book
  * 2026-10-02 11:00 - FR consulate in Dublin, IE (short-stay) - 1 seat(s) - https://example.invalid/dublin/fr/book
[2026-09-02T20:36:21+00:00] 1 new Schengen slot(s) for ES consulate in Dublin, IE (short-stay):
  * 2026-09-28 14:45 - ES consulate in Dublin, IE (short-stay) - 1 seat(s) - https://example.invalid/dublin/es/book
```

Run it again and nothing is reported — already-alerted slots are remembered in
the `state_file`. Drop the `--once` flag to keep polling forever.

To watch a real portal, start from `examples/dublin_http.json` (generic JSON) or
`examples/dublin_vfs.json`, `examples/dublin_tls.json`, `examples/dublin_bls.json`
(partner-specific templates), then point options at endpoints you are entitled to
poll:

```bash
export OPENCLAW_WEBHOOK_URL="https://hooks.example.com/your-hook"
export OPENCLAW_API_TOKEN="your-token"
python -m openclaw --config examples/dublin_http.json
```

## CLI

```text
python -m openclaw --config CONFIG [--once] [--cycles N] [--list-watches] [--verbose]
```

| Flag | Meaning |
| --- | --- |
| `--config`, `-c` | Path to the JSON config file (required). |
| `--once` | Run a single polling cycle and exit. |
| `--cycles N` | Stop after N cycles (default: run until interrupted). |
| `--list-watches` | Print the configured watches and exit. |
| `--verbose`, `-v` | Debug logging. |

Exit code `0` on success, `2` for configuration errors.

## Configuration reference

```jsonc
{
  "poll_interval": 300,                 // seconds between cycles (> 0)
  "jitter": 60,                         // random extra delay, seconds
  "earliest": "2026-09-01",             // ignore slots before this date
  "latest": "2026-12-31",               // ignore slots after this date
  "state_file": ".openclaw/state.json", // remembers already-alerted slots
  "notifiers": [ { "type": "console" } ],
  "watches": [ { "...": "see below" } ]
}
```

### Watch

| Key | Required | Description |
| --- | --- | --- |
| `country_from` | yes | Where you apply from, e.g. `"IE"`. |
| `country_to` | yes | Schengen state you apply to, e.g. `"FR"`. |
| `city` | yes | Appointment centre location, e.g. `"Dublin"`. |
| `visa_category` | no | Defaults to `"short-stay"`. |
| `provider` | no | `"mock"` (default), `"http-json"`, `"vfs-global"`, `"tlscontact"`, or `"bls-international"`. |
| `options` | no | Provider-specific settings. |

### Provider reference

| Provider | Required options | Auth modes | Caveats |
| --- | --- | --- | --- |
| `mock` | none (`slots` or `file` optional) | n/a | Offline/testing only. |
| `http-json` | `url` | `none`, `form`, `token`, `basic` | Generic JSON list parser. |
| `vfs-global` | `base_url`, `availability_path`, `centre_code`, `category_code`, `mission_code` | `none`, `form`, `token`, `basic` | Requires a permitted JSON calendar/availability path; no anti-bot/CAPTCHA bypass support. |
| `tlscontact` | `base_url`, `availability_path`, `location_code`, `category_code`, `destination_code` | `none`, `form`, `token`, `basic` | Requires a permitted JSON calendar path; no anti-bot/CAPTCHA bypass support. |
| `bls-international` | `base_url`, `availability_path`, `centre_code`, `category_code`, `mission_code` | `none`, `form`, `token`, `basic` | Requires a permitted JSON availability path; no anti-bot/CAPTCHA bypass support. |

### Provider options

**`http-json`**

| Option | Description |
| --- | --- |
| `url` | Required JSON availability endpoint (`http`/`https` only). |
| `headers` | Extra static request headers. Values may use `${ENV_VAR}` placeholders. |
| `items_key` | Optional key holding the list of slot entries. |
| `date_key` | Slot date key (default `date`). |
| `date_format` | Python date format (default `%Y-%m-%d`). |
| `time_key` | Optional slot time key. |
| `seats_key` | Optional seats key; entries with `0` seats are skipped. |
| `booking_url` | Fallback booking URL for slot alerts. |
| `auth` | Optional authenticated-portal block; see below. |

**`mock`** — inline `slots` (`[{"date": "...", "time": "...", "seats": 1}]`) or a
`file` containing the same list.

**`vfs-global`, `tlscontact`, `bls-international`** share a declarative shape:

| Option | Description |
| --- | --- |
| `base_url` | Required portal host, e.g. `https://partner.example.invalid`. |
| `availability_path` | Required calendar/availability endpoint path. |
| `centre_code` / `location_code` | Provider-specific centre/location identifier. |
| `category_code` | Visa category code required by the portal. |
| `sub_category_code` | Optional visa sub-category code. |
| `mission_code` / `destination_code` | Mission/destination country code used by the endpoint. |
| `query` | Optional extra static query params. |
| `headers` | Optional request headers (values may use `${ENV_VAR}`). |
| `booking_path` + `booking_query` | Optional fallback deep link used in alerts. |
| `response` | Optional response mapping (`items_path`, `date_key`, `time_key`, `seats_key`, etc.). |
| `auth` | Optional authenticated-portal block; see below. |

### Notifiers

| Type | Options | Behaviour |
| --- | --- | --- |
| `console` | – | Prints alerts to stdout. |
| `file` | `path` | Appends one JSON object per alert (JSON Lines). |
| `webhook` | `url`, `headers`, `timeout` | POSTs the alert as JSON. |

Any `${VAR}` in the config is replaced with the environment variable `VAR`, so
tokens and webhook URLs never need to be committed.


### Authenticated portals

`http-json`, `vfs-global`, `tlscontact`, and `bls-international` watches may
include an `options.auth` block. Authentication is lazy:
Open Claw logs in on the first fetch for that watch, stores cookies and token
headers in memory, and reuses the same per-watch session across poll cycles.
Nothing auth-related is written to the `state_file`. If a slots request returns
401/403, redirects to the login URL, or a recorded token expiry has passed, Open
Claw re-authenticates once and retries that fetch once. If that also fails, the
watch logs an `AuthenticationError` and the monitor continues polling other
watches.

Use `${ENV_VAR}` placeholders for credential values. Literal password-like
auth fields such as `password`, `*_secret`, `api_key`, or `access_token` are
rejected with `ConfigError` so secrets are not accidentally committed. Open Claw
also avoids logging credentials, cookies, tokens, or `Authorization` values.

**`auth.type: "none"`** is the default and preserves unauthenticated behavior.

**`auth.type: "form"`** posts classic form credentials and keeps any session
cookies set by the portal:

```jsonc
"auth": {
  "type": "form",
  "login_url": "https://portal.example/login",
  "fields": { "username": "${OPENCLAW_USER}", "password": "${OPENCLAW_PASS}" },
  "encoding": "form",              // "form" (default) or "json"
  "csrf": {                         // optional
    "url": "https://portal.example/login",
    "regex": "name=\"_csrf\" value=\"([^\"]+)\"",
    "field": "_csrf"
  },
  "success_status": [200, 302]
}
```

### Adapter notes: VFS Global, TLScontact, BLS International

Each adapter is inert unless a watch explicitly sets that provider name.

- Configure only endpoints and account flows you are explicitly permitted to use.
- Operator responsibility: confirm ToS, local law, and mission-specific rules
  before polling or automated sign-in.
- If a portal returns CAPTCHA / anti-bot challenges, Open Claw fails cleanly and
  does not attempt bypassing.
- Open Claw observes availability only. It does not submit applications, hold
  seats, or auto-book appointments.

**`auth.type: "token"`** posts JSON to a token endpoint and injects the returned
token into a request header:

```jsonc
"auth": {
  "type": "token",
  "login_url": "https://portal.example/api/auth",
  "body": { "email": "${OPENCLAW_USER}", "password": "${OPENCLAW_PASS}" },
  "token_key": "access_token",       // dotted paths like "data.token" work
  "expires_key": "expires_in",       // optional seconds until expiry
  "header": "Authorization",         // default
  "header_format": "Bearer {token}" // default
}
```

**`auth.type: "basic"`** sends HTTP Basic credentials on slot requests:

```jsonc
"auth": {
  "type": "basic",
  "username": "${OPENCLAW_USER}",
  "password": "${OPENCLAW_PASS}"
}
```

## Adding a new portal in code

```python
from openclaw import Provider, register_provider

class MyPortalProvider(Provider):
    name = "my-portal"

    def fetch(self, watch):
        ...  # return a list of openclaw.Slot

register_provider(MyPortalProvider.name, MyPortalProvider)
```

## Development

```bash
python -m unittest discover -s tests -v
```

The test suite is offline and covers config validation, providers/adapters,
de-duplication, date windows, notifiers and the CLI.

## Responsible use

Open Claw only reads endpoints **you** configure — it does not bundle live
consulate URLs and does not bypass CAPTCHA, anti-bot controls, WAFs, rate limits,
or authentication. If a configured endpoint is gated by those controls without a
supported API/session flow, the watch fails with an operator-facing message.

Open Claw never auto-books, holds, or submits appointments. It is an observe +
notify tool only.

Credentials must come from `${ENV_VAR}` placeholders in config. Literal
password-like secrets are rejected. Open Claw also avoids logging credentials and
redacts common personal identifiers from booking-link query strings.

Before polling any VFS/TLScontact/BLS (or other) portal, verify that your access
path and polling behavior comply with that portal's terms plus local law. Keep
`poll_interval` generous (10 minutes or more), keep `jitter` enabled, and stop if
the portal requests that you do so. You are responsible for compliant use.
