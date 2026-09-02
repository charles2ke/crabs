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
  only, not code. A `mock` provider makes demos and tests fully offline.
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

To watch a real portal, start from `examples/dublin_http.json`, which uses the
`http-json` provider, and point it at the availability endpoint you are entitled
to poll:

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
| `provider` | no | `"mock"` (default) or `"http-json"`. |
| `options` | no | Provider-specific settings. |

### Provider options

**`http-json`** — `url` (required, `http`/`https` only), `headers`, `items_key`
(key holding the list), `date_key` (default `date`), `date_format` (default
`%Y-%m-%d`), `time_key`, `seats_key` (entries with `0` seats are skipped) and
`booking_url`.

**`mock`** — inline `slots` (`[{"date": "...", "time": "...", "seats": 1}]`) or a
`file` containing the same list.

### Notifiers

| Type | Options | Behaviour |
| --- | --- | --- |
| `console` | – | Prints alerts to stdout. |
| `file` | `path` | Appends one JSON object per alert (JSON Lines). |
| `webhook` | `url`, `headers`, `timeout` | POSTs the alert as JSON. |

Any `${VAR}` in the config is replaced with the environment variable `VAR`, so
tokens and webhook URLs never need to be committed.

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

The test suite is offline and covers config validation, both providers,
de-duplication, date windows, notifiers and the CLI.

## Responsible use

Open Claw only reads endpoints **you** configure — it does not bundle, bypass or
scrape any consulate portal, and it never books an appointment for you. Before
pointing it at a live site, check that portal's terms of service, keep
`poll_interval` generous (10 minutes or more) and keep `jitter` enabled. You are
responsible for how you use it.
