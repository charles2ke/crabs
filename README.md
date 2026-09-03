# Open Claw

Secure Open Claw for everyone.

Open Claw is a dependency-free Python tool that polls configured Schengen visa
appointment portals and alerts on availability changes. The reference use case is
applying from Ireland at consulates and visa centres in Dublin.

It is strictly an **observe-and-notify** tool: it never books, holds, or submits
appointments and does not bypass authentication, CAPTCHA, WAF, or rate limits.

## Features

- Multiple watches and stdlib-only provider adapters (`mock`, `http-json`,
  `vfs-global`, `tlscontact`, and `bls-international`).
- Console, JSON Lines, webhook, and Telegram notifications.
- Opt-in new, disappeared, improved, and provider-health alerts.
- Persistent atomic state, cross-process locking, date-window filtering, quiet
  hours, throttling, and cron-safe one-shot execution.
- Offline config diagnostics, persisted health statistics, and text or JSON logs.
- Python 3.10+ with no runtime dependencies.

## Quickstart: Dublin

```bash
git clone https://github.com/charles2ke/crabs.git
cd crabs
pip install -e .  # optional; running from the checkout also works
python -m openclaw --config examples/dublin.json --once
```

`examples/dublin.json` uses the offline `mock` provider and watches French and
Spanish appointments in Dublin. A first run prints alerts such as:

```text
[2026-09-02T20:36:21+00:00] 2 new Schengen slot(s) for FR consulate in Dublin, IE (short-stay):
  * 2026-09-14 09:20 - FR consulate in Dublin, IE (short-stay) - 2 seat(s) - https://example.invalid/dublin/fr/book
  * 2026-10-02 11:00 - FR consulate in Dublin, IE (short-stay) - 1 seat(s) - https://example.invalid/dublin/fr/book
```

Run it again and persisted state prevents duplicate alerts. Remove `--once` to
keep polling. Before using a real portal, copy one of the `dublin_http`,
`dublin_vfs`, `dublin_tls`, or `dublin_bls` examples and configure only an
endpoint you are entitled to poll.

```bash
python -m openclaw --config config.json --validate-config
python -m openclaw --config config.json --once --log-format json
python -m openclaw --config config.json --stats
```

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
python -m unittest discover -s tests -v
python -m mypy openclaw
```

All tests are offline.
