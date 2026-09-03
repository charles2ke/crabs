# Configuration and CLI

## Top-level configuration

```jsonc
{
  "poll_interval": 300,
  "jitter": 60,
  "earliest": "2026-09-01",
  "latest": "2026-12-31",
  "state_file": ".openclaw/state.json",
  "quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "Europe/Dublin"},
  "throttle": {"max_alerts": 3, "interval_seconds": 3600, "minimum_gap_seconds": 60},
  "health": {"max_consecutive_empty": 6, "max_consecutive_errors": 3, "max_stale_hours": 24},
  "notifiers": [{"type": "console"}],
  "watches": [{"country_from": "IE", "country_to": "FR", "city": "Dublin"}]
}
```

`poll_interval` must be positive and `jitter` non-negative. `earliest` and
`latest` are inclusive ISO dates. State records alerted and currently offered
slots, queued alerts, throttle history, and provider health. Old list and flat
object state files remain readable.

Each watch requires `country_from`, `country_to`, and `city`; `visa_category`
defaults to `short-stay`, `provider` to `mock`, and `options` contains provider
settings. `alert_on` accepts any independent combination of `new`,
`disappeared`, and `improved`; its default is `["new"]`.

Global quiet hours and throttling may be overridden per watch. A quiet window
requires an explicit IANA timezone and `HH:MM` start/end. Alerts are persisted,
not dropped, and delivered on the first poll after quiet hours end. Throttled
alerts are handled the same way. `max_alerts` and `interval_seconds` must be
configured together; `minimum_gap_seconds` may be used alone.

Health detection is opt-in and can be global or per watch. Configure one or more
of `max_consecutive_empty`, `max_consecutive_errors`, and `max_stale_hours`.
A warning is sent once per stale episode and resets after slots are seen again.

Any `${VAR}` string is expanded from the environment. Password-like auth fields
and Telegram bot tokens must use a whole-value placeholder.

## CLI

```text
openclaw --config CONFIG [--once | --cycles N | --list-watches |
  --validate-config | --dry-run | --stats]
  [--state PATH] [--bootstrap] [--lock-timeout SECONDS]
  [--log-format text|json] [--verbose]
```

- `--once`: one polling cycle.
- `--cycles N`: exactly N positive cycles.
- `--list-watches`: print configured watch identities.
- `--validate-config` / `--dry-run`: resolve environment references, report
  missing variable names without values, validate provider/notifier setup, and
  list redacted endpoints without network access.
- `--stats`: print persisted slots-seen, success/failure, and last-success data
  without polling.
- `--state`: override `state_file`.
- `--bootstrap`: on a cold state file, record current slots without alerting.
- `--lock-timeout`: seconds to wait for another state-file user.
- `--log-format json`: one redacted JSON object per log line.

Exit codes remain: `0` successful/no dispatched alert, `2` configuration error,
`3` provider failure with no dispatched alert, `4` state locked, and `10` one or
more slot or health alerts dispatched. Alert code `10` wins over `3`.
