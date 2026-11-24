# Configuration and CLI

## Top-level configuration

```jsonc
{
  "poll_interval": 300,
  "jitter": 60,
  "earliest": "2026-09-01",
  "latest": "2026-12-31",
  "within_days": 60,
  "weekdays": ["mon", "tue", "wed", "thu", "fri"],
  "min_seats": 1,
  "state_file": ".openclaw/state.json",
  "quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "Europe/Dublin"},
  "throttle": {"max_alerts": 3, "interval_seconds": 3600, "minimum_gap_seconds": 60},
  "health": {"max_consecutive_empty": 6, "max_consecutive_errors": 3, "max_consecutive_challenges": 1, "max_stale_hours": 24},
  "heartbeat": {"url": "${OPENCLAW_HEARTBEAT_URL}", "timeout": 10},
  "notifiers": [{"type": "console"}],
  "watches": [{"country_from": "IE", "country_to": "FR", "city": "Dublin"}]
}
```

`poll_interval` must be positive and `jitter` non-negative. `earliest` and
`latest` are inclusive ISO dates, and `min_seats` (a positive integer, default
`1`) ignores slots offering fewer seats. Slots dated before today (UTC) are
always ignored, so they never alert and never count as disappeared. State
records alerted and currently offered slots, queued alerts and delivery
retries, throttle history, provider health, and portal-requested pauses. Old
list and flat object state files remain readable.

`within_days` (a whole number from 1 to 3650) keeps only slots at most that many
days after today (UTC, inclusive), a window that moves with the calendar. When
`latest` is also set, the earlier of the two dates applies. `weekdays` keeps only
slots on the listed days: full English names or three-letter abbreviations, in
any case. Both are off by default, and `--list-slots` shows what they keep.

Each watch requires `country_from`, `country_to`, and `city`; `visa_category`
defaults to `short-stay`, `provider` to `mock`, and `options` contains provider
settings. `alert_on` accepts any independent combination of `new`,
`disappeared`, and `improved`; its default is `["new"]`.

A watch may set its own `earliest`, `latest`, `within_days`, `weekdays`, and
`min_seats`; each one it sets replaces the global value and the others are
inherited. A watch whose effective window is empty (`earliest` after `latest`)
is rejected:

```json
{"country_from": "IE", "country_to": "ES", "city": "Dublin",
 "earliest": "2026-12-01", "latest": "2027-01-15", "min_seats": 2,
 "weekdays": ["fri", "saturday"]}
```

Global quiet hours and throttling may be overridden per watch. A quiet window
requires an explicit IANA timezone and `HH:MM` start/end. Alerts are persisted,
not dropped, and delivered on the first poll after quiet hours end. Throttled
alerts are handled the same way. `max_alerts` and `interval_seconds` must be
configured together; `minimum_gap_seconds` may be used alone.

Health detection is opt-in and can be global or per watch. Configure one or more
of `max_consecutive_empty`, `max_consecutive_errors`, and `max_stale_hours`.
A warning is sent once per stale episode and resets after slots are seen again.

CAPTCHA, anti-bot, and WAF challenge responses are always reported as a health
warning, on the first challenge by default. Open Claw never solves or bypasses
a challenge; the warning exists so a human can decide how to proceed. Raise
`max_consecutive_challenges` to tolerate occasional challenges before alerting.

When a portal answers HTTP 429, or HTTP 503 with a `Retry-After` header, the
watch is paused until the time the portal asked for, counted from its answer
and capped at 24 hours. A challenge served with such a response pauses the
watch too, and still raises the challenge warning. The pause is kept in the
state file, so later scheduled runs honour it too. A paused watch is skipped,
counts as failed (exit code `3`), keeps its alerted slots, and shows
`paused_until=` in `--stats`. Without a usable `Retry-After` delay, the watch
fails that cycle only. See [Providers](providers.md#rate-limits-and-retry-after).

`heartbeat` is an optional dead man's switch. After every completed polling
cycle, including cycles where a provider failed, Open Claw sends a `GET` to
`url`, for example a [healthchecks.io](https://healthchecks.io) or Uptime Kuma
push URL, so that service can alert you when the pings stop. `url` must be a
whole `${ENV_VAR}` placeholder, because ping URLs embed a secret token, and it
must resolve to an `http(s)` URL. `timeout` defaults to 10 seconds. A failed
ping is logged with only the scheme and host, and never changes the exit code.
`--list-slots` and `--test-notifiers` do not ping.

Any `${VAR}` string is expanded from the environment. Password-like auth fields,
Telegram bot tokens, Slack and Discord webhook URLs, SMTP passwords, ntfy and
Pushover tokens, and the heartbeat URL must use a whole-value placeholder.

## CLI

```text
openclaw [--config CONFIG] [--once | --cycles N | --list-watches |
  --validate-config | --dry-run | --stats | --test-notifiers | --list-slots]
  [--state PATH] [--bootstrap] [--lock-timeout SECONDS]
  [--log-format text|json] [--verbose]
```

- `--config` / `-c`: path to the JSON config file. When omitted, the first of
  `openclaw.json`, `config.json`, or `.openclaw/config.json` in the working
  directory is used; if none exists the error lists the paths searched.
- `--once`: one polling cycle.
- `--cycles N`: exactly N positive cycles.
- `--list-watches`: print configured watch identities.
- `--validate-config` / `--dry-run`: resolve environment references, report
  missing variable names without values, validate provider/notifier setup, and
  list redacted endpoints without network access.
- `--stats`: print persisted slots-seen, success/failure, challenge-count, and
  last-success data without polling, plus `paused_until=` for a watch the
  portal asked to wait. If no state file is configured or the state file does
  not exist yet, a short hint is printed instead of empty counters.
- `--test-notifiers`: send one `test` alert (for the first watch) through every
  configured notifier and print `ok` or a redacted failure per notifier. It
  makes real deliveries but never polls providers or touches the state file.
  Exits `0` when every notifier succeeds and `2` otherwise.
- `--list-slots`: poll every watch once and print the slots that pass its
  filters, soonest first, with how many the portal offered in total. It sends no
  alerts, reads the state file only to skip paused watches, never writes it, and
  does not ping the heartbeat. Exits `0`, or `3` if any watch failed or is
  paused.
- `--state`: override `state_file`.
- `--bootstrap`: on a cold state file, record current slots without slot alerts
  (health warnings may still be sent).
- `--lock-timeout`: seconds to wait for another state-file user.
- `--log-format json`: one redacted JSON object per log line.

Exit codes remain: `0` successful/no dispatched alert, `2` configuration error
(or a failed `--test-notifiers` delivery), `3` provider failure or paused watch
with no dispatched alert, `4` state locked, and `10` one or more slot or health
alerts dispatched. An alert counts as dispatched once at least one notifier accepts
it. Alert code `10` wins over `3`.
