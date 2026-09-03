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
in a JSON Lines log file, to Telegram, or to a webhook (Slack, Discord, ntfy, …).

## Features

- **Watch many consulates at once** — one entry per country/city/visa category.
- **Pluggable providers** — a generic `http-json` provider is configured
  declaratively (URL, keys, date format), so a new portal needs a config edit
  only, not code. Built-in adapters are available for `vfs-global`,
  `tlscontact`, and `bls-international` when you have a permitted JSON access
  path. A `mock` provider makes demos and tests fully offline.
- **Alert on every available slot**, de-duplicated so you are notified once per
  slot; if a slot disappears and comes back it alerts again.
- **Multiple alert sinks** — `console`, `file` (JSON Lines), `telegram`, `webhook`.
- **Date window filtering** with `earliest` / `latest`.
- **Persistent state** so restarts do not replay old alerts — with atomic
  writes, cross-process locking and past-date pruning, so `--once` is safe to
  run from cron, a systemd timer or GitHub Actions.
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

### Telegram alerts

`examples/dublin_telegram.json` shows the Dublin watch with Telegram delivery.
Telegram bot tokens must be supplied from the environment; literal tokens in
config files are rejected.

1. In Telegram, open **BotFather**, create a bot with `/newbot`, and copy the bot
   token it returns.
2. Send any message to your new bot, then obtain the chat id for your user or
   group using your preferred Telegram client/tooling.
3. Export the credentials and run Open Claw:

```bash
export OPENCLAW_TELEGRAM_BOT_TOKEN="paste-your-botfather-token"
export OPENCLAW_TELEGRAM_CHAT_ID="123456789"
python -m openclaw --config examples/dublin_telegram.json --once
```

A rendered Telegram alert looks like:

```text
2 new Schengen slot(s)
Centre: Dublin (IE)
Destination: FR
Visa category: short-stay
• 2026-09-14 09:20 — 2 seat(s) — booking link
• 2026-10-02 11:00 — 1 seat(s) — booking link
```

## CLI

```text
python -m openclaw --config CONFIG [--once] [--cycles N] [--list-watches]
                   [--state PATH] [--bootstrap] [--lock-timeout SECONDS] [--verbose]
```

| Flag | Meaning |
| --- | --- |
| `--config`, `-c` | Path to the JSON config file (required). |
| `--once` | Run a single polling cycle and exit. |
| `--cycles N` | Stop after N cycles (default: run until interrupted). |
| `--list-watches` | Print the configured watches and exit. |
| `--state PATH` | Seen-slot state file; overrides `state_file` in the config. |
| `--bootstrap` | On a cold state store, record the slots currently on offer as already seen and do **not** alert. Use it for the very first scheduled run so you are not paged about the whole existing backlog. Once the state file exists the flag is a no-op. |
| `--lock-timeout SECONDS` | How long to wait for the state lock held by another run (default `0`, i.e. give up immediately). |
| `--verbose`, `-v` | Debug logging. |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Ran fine, no new slots (also used for `--list-watches` and Ctrl-C). |
| `2` | Configuration error (bad config, unusable notifier/provider setup). |
| `3` | Ran fine, but at least one watch failed because its provider was unavailable, and nothing was alerted. Transient — safe to retry on the next schedule. |
| `4` | Another Open Claw run holds the state lock; this run did nothing. |
| `10` | Ran fine and alerted about at least one new slot. |

`10` wins over `3`: if any alert was dispatched the run reports `10` even when a
different watch failed.

## Running on a schedule

Open Claw does not need a long-lived process. `--once` plus a scheduler is the
recommended deployment: state is persisted between runs, writes are atomic
(temp file + `os.replace`), a lock file next to the state file (`<state>.lock`)
keeps overlapping runs from racing, and entries for appointment dates in the
past are pruned so the state file cannot grow without bound.

### GitHub Actions

`.github/workflows/watch.yml` runs `python -m openclaw --config … --once` every
30 minutes and on `workflow_dispatch` (which also accepts a `config` path and a
`bootstrap` toggle).

1. Add the credentials your config references as **repository secrets**, e.g.
   `OPENCLAW_TELEGRAM_BOT_TOKEN`, `OPENCLAW_TELEGRAM_CHAT_ID`, and any provider
   credentials (`OPENCLAW_USER`, `OPENCLAW_PASS`, `OPENCLAW_API_TOKEN`,
   `OPENCLAW_WEBHOOK_URL`). The workflow exposes them as environment variables;
   configs reference them with `${ENV_VAR}` placeholders, never literals.
2. Point `OPENCLAW_CONFIG` (or the `workflow_dispatch` input) at your config.
3. Change the interval by editing the `schedule.cron` expression. Keep it
   polite; GitHub also runs `schedule` triggers late under load and disables
   them on repositories with no activity for 60 days.

Cache behaviour and tradeoffs:

- State lives in `.openclaw/` and is carried between runs with
  `actions/cache/restore` + `actions/cache/save`, using a unique key per run
  (`openclaw-state-<run_id>`) and the `openclaw-state-` restore prefix, so every
  run restores the newest state and always saves an updated copy.
- Actions caches are evicted after 7 days of no use and under repository size
  pressure, and they are branch-scoped. A cold cache therefore happens
  occasionally — the workflow passes `--bootstrap` whenever no cache was
  matched, so a cold run records what is currently on offer instead of alerting
  about all of it. The cost is that slots which appear *during* a cold run are
  only reported on the next run.
- The `concurrency: openclaw-watch` group serialises runs; `--lock-timeout 30`
  is a second line of defence for self-hosted runners sharing a state file.
- Provider outages exit `3` and are surfaced as a workflow *warning*, not a
  failure, so transient portal errors do not produce a wall of red CI.
- **Public repositories publish Actions logs.** Anything the run prints —
  centres, dates, booking links — is world-readable. Keep configs free of
  personal data, keep every credential in secrets, and use a private repository
  if in doubt.

### System crontab (VPS, Raspberry Pi, …)

Use absolute paths, an explicit `--state` path outside the checkout, and
redirect logs. `--bootstrap` is safe to leave in place permanently: it only
takes effect when the state file is missing.

```cron
# m h dom mon dow  command
*/20 * * * * /opt/openclaw/.venv/bin/openclaw \
  --config /etc/openclaw/dublin.json \
  --state /var/lib/openclaw/state.json \
  --once --bootstrap >> /var/log/openclaw.log 2>&1
```

Cron gives a bare environment, so export secrets in the crontab or source them
from a root-only file:

```cron
OPENCLAW_TELEGRAM_BOT_TOKEN=paste-your-botfather-token
OPENCLAW_TELEGRAM_CHAT_ID=123456789
```

Keep that file `chmod 600`. Overlapping runs are already safe (the second run
exits `4` immediately), so no `flock` wrapper is needed.

### systemd timer

`/etc/systemd/system/openclaw.service`:

```ini
[Unit]
Description=Open Claw Schengen slot watcher (single poll)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
DynamicUser=yes
StateDirectory=openclaw
EnvironmentFile=/etc/openclaw/secrets.env
ExecStart=/opt/openclaw/.venv/bin/openclaw \
  --config /etc/openclaw/dublin.json \
  --state /var/lib/openclaw/state.json --once --bootstrap
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_INET AF_INET6
SystemCallFilter=@system-service
```

`/etc/systemd/system/openclaw.timer`:

```ini
[Unit]
Description=Run Open Claw every 20 minutes

[Timer]
OnCalendar=*:0/20
RandomizedDelaySec=300
Persistent=true
Unit=openclaw.service

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now openclaw.timer
systemctl list-timers openclaw.timer
journalctl -u openclaw.service -f
```

`RandomizedDelaySec` spreads requests so every Open Claw deployment does not hit
a portal on the same minute; `Persistent=true` catches up a missed run after a
reboot. `EnvironmentFile` should be `chmod 600` and hold the `${ENV_VAR}` values
the config expects. With `DynamicUser=yes`, prefer the systemd-managed
`StateDirectory` path (`/var/lib/openclaw`) for `--state`.

### Choosing an interval

Poll no more often than you need: 20–30 minutes is plenty for consulate
calendars, matches the `poll_interval` guidance below (10 minutes or more), and
keeps you well inside portal rate limits. Add jitter — `RandomizedDelaySec` for
systemd, a random minute offset for cron, the `jitter` config key for long-lived
runs. If a portal returns errors or asks you to stop, back off or stop entirely.

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
| `telegram` | `bot_token`, `chat_id`, `timeout`, `disable_notification` | Sends compact HTML-formatted Telegram Bot API messages with link previews disabled. |
| `webhook` | `url`, `headers`, `timeout` | POSTs the alert as JSON. |

Any `${VAR}` in the config is replaced with the environment variable `VAR`, so
tokens and webhook URLs never need to be committed.

Telegram alerts use the Bot API `sendMessage` endpoint. Messages are split on
slot boundaries before Telegram's length limit, and every interpolated value is
HTML-escaped before sending.


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
de-duplication, date windows, notifiers, scheduled-run behaviour (bootstrap,
locking, atomic state writes, pruning, exit codes) and the CLI.

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
Telegram bot tokens are also required to come from `${ENV_VAR}` placeholders and
are redacted from Open Claw output, including errors from Telegram request URLs.

Before polling any VFS/TLScontact/BLS (or other) portal, verify that your access
path and polling behavior comply with that portal's terms plus local law. Keep
`poll_interval` generous (10 minutes or more), keep `jitter` enabled, and stop if
the portal requests that you do so. You are responsible for compliant use.
