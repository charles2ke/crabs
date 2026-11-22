# Open Claw

[![CI](https://github.com/charles2ke/crabs/actions/workflows/ci.yml/badge.svg)](https://github.com/charles2ke/crabs/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Dependencies](https://img.shields.io/badge/runtime%20dependencies-none-brightgreen)](pyproject.toml)

Open Claw watches Schengen visa appointment portals and tells you the moment a
slot appears — by console, file, webhook, Telegram, Slack, Discord, email,
ntfy, or Pushover.
It is a single dependency-free Python package that runs from a checkout, a cron
job, or a GitHub Actions schedule. The reference use case is applying from
Ireland at consulates and visa centres in Dublin.

## What it does and does not do

| Open Claw does | Open Claw never does |
| --- | --- |
| Poll endpoints you are entitled to poll | Book, hold, or submit an appointment |
| Compare results with persisted state | Bypass authentication, CAPTCHA, WAF, or rate limits |
| Notify you about the change | Solve a challenge or hide that one happened |

It is strictly an **observe-and-notify** tool. Read
[Security and responsible use](docs/security.md) before pointing it at a real
portal.

## Features

- Multiple watches and stdlib-only provider adapters (`mock`, `http-json`,
  `vfs-global`, `tlscontact`, and `bls-international`).
- Console, JSON Lines, webhook, Telegram, Slack, Discord, email (SMTP), ntfy,
  and Pushover notifications, with `--test-notifiers` to check them end to end.
- Opt-in new, disappeared, improved, and provider-health alerts.
- Failed notifier deliveries are retried on later runs, only to the notifier
  that failed.
- Persistent atomic state, cross-process locking, global or per-watch date
  windows (fixed dates or a rolling `within_days` horizon), weekday and
  minimum-seat filters, quiet hours, throttling, and cron-safe one-shot
  execution. Past-dated slots are ignored.
- `--list-slots` shows what a portal offers right now and which slots your
  filters keep, without alerting or touching state.
- Polite by design: a portal's `Retry-After` (HTTP 429 or 503) pauses that watch
  across runs, and an optional heartbeat ping tells a monitoring service such
  as healthchecks.io that scheduled runs are still completing.
- Offline config diagnostics, persisted health statistics, and text or JSON logs.
- Python 3.10+ (tested on 3.10 to 3.15) with no runtime dependencies.

## Quickstart: Dublin

```bash
git clone https://github.com/charles2ke/crabs.git
cd crabs
pip install -e .  # optional; running from the checkout also works
python -m openclaw --config examples/dublin.json --once
```

`examples/dublin.json` uses the offline `mock` provider and watches French and
Spanish appointments in Dublin, so the quickstart makes no network requests.
Its demo slots are dated relative to today (14, 28, and 32 days ahead), so the
dates you see will differ. A first run prints alerts such as:

```text
[2026-11-13T21:11:16+00:00] 2 new Schengen slot(s) for FR consulate in Dublin, IE (short-stay):
  * 2026-11-27 09:20 - FR consulate in Dublin, IE (short-stay) - 2 seat(s) - https://example.invalid/dublin/fr/book
  * 2026-12-15 11:00 - FR consulate in Dublin, IE (short-stay) - 1 seat(s) - https://example.invalid/dublin/fr/book
```

Run it again and persisted state prevents duplicate alerts. Remove `--once` to
keep polling.

## Your own config

A minimal config is one notifier and one watch:

```json
{
  "poll_interval": 300,
  "state_file": ".openclaw/state.json",
  "notifiers": [{ "type": "console" }],
  "watches": [
    { "country_from": "IE", "country_to": "FR", "city": "Dublin", "provider": "mock" }
  ]
}
```

For a real portal, copy one of the `examples/dublin_http.json`,
`examples/dublin_vfs.json`, `examples/dublin_tls.json`, or
`examples/dublin_bls.json` files and configure
only an endpoint you are entitled to poll. Secrets belong in environment
variables and are referenced as `${VAR}`. Then validate offline before the
first live run:

```bash
python -m openclaw --config config.json --validate-config
python -m openclaw --config config.json --test-notifiers
python -m openclaw --config config.json --list-slots
python -m openclaw --config config.json --once --log-format json
python -m openclaw --config config.json --stats
```

`--validate-config` never touches the network; `--test-notifiers` sends one
clearly labelled test alert through each notifier, so you know alerts will
arrive before a real slot does. `--list-slots` polls each watch once and
prints the slots that pass your filters, without alerting or writing state.
For `examples/dublin.json` it prints (dates move with the calendar):

```text
FR consulate in Dublin, IE (short-stay): 2 of 2 offered slot(s) match the filters
  * 2026-12-06 09:20 - 2 seat(s) - https://example.invalid/dublin/fr/book
  * 2026-12-24 11:00 - 1 seat(s) - https://example.invalid/dublin/fr/book
ES consulate in Dublin, IE (short-stay): 1 of 1 offered slot(s) match the filters
  * 2026-12-20 14:45 - 1 seat(s) - https://example.invalid/dublin/es/book
```

Name the file `openclaw.json`, `config.json`, or `.openclaw/config.json` and
`--config` becomes optional — `python -m openclaw --once` finds it in the
working directory.

`--stats` prints one line per watch, so you can see whether a portal is
answering at all:

```text
FR consulate in Dublin, IE (short-stay): slots_seen=3 successes=12 failures=0 challenges=0 last_success=2026-09-02T20:36:21+00:00
```

## Common CLI flags

| Flag | Purpose |
| --- | --- |
| `--config PATH`, `-c` | JSON config file (default: `openclaw.json`, `config.json`, or `.openclaw/config.json` in the working directory) |
| `--once` / `--cycles N` | Run one cycle, or exactly N cycles |
| `--list-watches` | Print configured watch identities and exit |
| `--validate-config` / `--dry-run` | Offline check of config, providers, and notifiers |
| `--test-notifiers` | Send one test alert through every notifier and report each result |
| `--list-slots` | Poll once and print the slots that pass your filters; no alerts, no state writes |
| `--stats` | Print persisted health statistics without polling |
| `--state PATH` | Override `state_file` |
| `--bootstrap` | On a cold state file, record current slots without slot alerts (health warnings are still sent) |
| `--lock-timeout SECONDS` | Wait for another run holding the state lock |
| `--log-format json` | One redacted JSON object per log line |
| `--verbose`, `-v` | Debug logging |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Ran successfully, no alert dispatched |
| `2` | Configuration, provider, or notifier setup error, or a failed `--test-notifiers` delivery |
| `3` | Provider failure, or a watch paused by the portal's `Retry-After`, and nothing was alerted |
| `4` | Another run holds the state lock |
| `10` | One or more slot or health alerts dispatched (accepted by at least one notifier) |

Code `10` wins over `3`, which makes `--once` safe to drive from cron or a
scheduled workflow.

## Troubleshooting

- **`no config file given and none found`.** Pass `--config PATH`, or name the
  file `openclaw.json`, `config.json`, or `.openclaw/config.json`.
- **No alerts on the second run.** Expected: state suppresses repeats. Delete
  the state file or use `--state` with a fresh path to start over.
- **A slot you can see on the portal never alerts.** Run `--list-slots`: it
  prints how many slots the portal offered and which ones pass your date,
  `within_days`, `weekdays`, and `min_seats` filters.
- **Alerts never arrive.** Run `--test-notifiers`; it prints `ok` or the
  (redacted) failure reason for each notifier. During normal runs a failed
  delivery is logged as `notifier_error` and retried on the next runs.
- **Exit code `3` and `the portal asked to wait until ...`.** The portal
  answered HTTP 429 or 503 with `Retry-After`, so that watch is paused until the
  time shown (also printed by `--stats` as `paused_until=`). Polling resumes on
  its own; consider a longer `poll_interval` or schedule.
- **Exit code `4`.** Another run still holds the lock; raise `--lock-timeout` or
  space out the schedule.
- **A challenge warning appears.** The portal returned a CAPTCHA/anti-bot/WAF
  response. That poll fails and a health warning is raised once
  `max_consecutive_challenges` (default `1`) is reached; Open Claw never solves
  or bypasses the challenge, so sign in yourself and decide how to proceed.
- **Missing environment variable.** `--validate-config` names the variable
  without printing any value.

`python -m openclaw --help` lists every flag together with usage examples and
the exit-code table.

## Documentation

- [Configuration and CLI](docs/configuration.md)
- [Provider and authentication reference](docs/providers.md)
- [Notifier reference](docs/notifiers.md)
- [Scheduling with Actions, cron, or systemd](docs/scheduling.md)
- [Security and responsible use](docs/security.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

## Development

```bash
pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m mypy openclaw
```

All tests are offline. CI runs them on Python 3.10 through 3.15, type-checks with
mypy, and builds and smoke-tests the wheel; see [CONTRIBUTING.md](CONTRIBUTING.md).
