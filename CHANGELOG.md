# Changelog

All notable changes are documented here in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
format. This project follows semantic versioning.

## [Unreleased]

### Added
- ntfy (`"type": "ntfy"`) and Pushover (`"type": "pushover"`) notifiers, with
  `examples/dublin_ntfy.json` and `examples/dublin_pushover.json`. The ntfy
  access token and the Pushover application token must be `${ENV_VAR}`
  placeholders; neither the ntfy topic nor either token appears in request URLs.
- `--test-notifiers` sends one clearly labelled `test` alert through every
  configured notifier and prints `ok` or a redacted failure for each. It exits
  `2` if any notifier fails, and never polls providers or touches state.
- Per-watch `earliest`, `latest`, and `min_seats` filters, plus a global
  `min_seats` (default `1`). A watch whose effective date window is empty is
  rejected at config load.
- Failed notifier deliveries are kept in state and retried on later cycles,
  only to the notifiers that failed, for up to 5 attempts in total (log events
  `alert_retry`, `alert_retried`, and `alert_dropped`). Retries wait for quiet
  hours but are not throttled again, and are dropped once their notifier or
  watch is no longer configured. Queued state entries gain the additive keys
  `retry_notifiers` and `attempts`.
- Mock provider slots accept `in_days` (days from today in UTC) instead of a
  fixed `date`, and a per-slot `booking_url`.
- CLI usability: `--config` is now optional and falls back to `openclaw.json`,
  `config.json`, or `.openclaw/config.json` in the working directory, with an
  error that names the paths searched; `--help` gained usage examples and the
  exit-code table; `--stats` explains what to do when no state exists yet.
- CAPTCHA/anti-bot challenge detection: providers raise `ChallengeError` for
  challenge responses, the monitor tracks `challenges` and
  `consecutive_challenges`, emits a provider-health warning (threshold
  `max_consecutive_challenges`, default 1), and `--stats` prints challenge
  counts. Challenges are reported, never solved or bypassed.
- Real-world alert integrations: Slack incoming webhooks (Block Kit), Discord
  webhooks (embeds), and plain-text email over SMTP, with example configs at
  `examples/dublin_slack.json`, `examples/dublin_discord.json`, and
  `examples/dublin_email.json`.
- Config validation requiring `${ENV_VAR}` placeholders for Slack and Discord
  webhook URLs and SMTP credentials.

### Changed
- Email `use_ssl: true` now selects implicit TLS without also enabling STARTTLS;
  explicitly setting both `use_ssl` and `use_tls` remains invalid.
- An alert counts as dispatched (exit code `10`) only once at least one
  notifier accepts it. A run whose alerts every notifier rejected now exits `0`
  (or `3`) and logs how many alerts await a retry, and the later run whose
  retry succeeds exits `10`.
- The bundled mock examples use `in_days` instead of fixed 2026 dates and no
  longer set a fixed date window, so the quickstart keeps producing alerts. The
  demo slots move with the calendar, so the scheduled demo workflow alerts at
  most once per UTC day.

### Fixed
- Past-dated slots were never filtered out, while state pruning forgot them,
  so every run re-alerted the same expired slots as new (visible in the
  scheduled demo workflow). Slots dated before today are now ignored, and a slot
  that simply expired is no longer reported as disappeared.
- Tests that hard-coded 2026 slot dates failed once those dates had passed;
  they now use dates relative to today.

## [1.0.0] - 2026-09-04

First tagged release, published as `v1`. It supersedes the never-tagged `0.2.0`
version number and declares the configuration, state-file, and exit-code contract
stable.

### Added
- Configurable slot disappearance and improvement events, timezone-aware quiet
  hours, persistent alert throttling, provider-health warnings, and health stats.
- Offline `--validate-config` / `--dry-run` diagnostics and redacted structured
  JSON logging.
- Dedicated CLI, notifier, alerting, and observability tests, plus mypy CI.
- Contributor, security, issue, pull-request, and expanded operator documentation.

### Changed
- State files now retain current and best slot data, queued alerts, throttle
  history, and health tracking. Legacy list and flat-object state files still load.
- Existing configurations remain new-slot-only by default; disappearance and
  improvement events, quiet hours, throttling, and health warnings are opt-in.
- Alerting runs return exit code `10` instead of `0`, as introduced with scheduled
  execution in PR #5. Cron and CI operators must accept `10` as success.

### Fixed
- Webhook notifiers now reject hostless URLs and non-positive timeouts and report
  non-2xx responses as delivery failures.
- Diagnostic and structured-log output removes URL query strings and redacts
  credential and personal-data fields.

## [0.1.0] - 2026-09-03

### Added
- PR #1: core watcher, config, state, mock/HTTP providers, and console/file/webhook notifiers.
- PR #2: authenticated reusable sessions and one re-authentication retry.
- PR #3: VFS Global, TLScontact, and BLS International adapters with offline fixtures.
- PR #4: Telegram notifier with safe formatting, splitting, redaction, and bounded 429 retry.
- PR #5: scheduled execution, locking, atomic state, pruning, bootstrap, and cron exit codes.

[Unreleased]: https://github.com/charles2ke/crabs/compare/v1...HEAD
[1.0.0]: https://github.com/charles2ke/crabs/compare/v0.1.0...v1
[0.1.0]: https://github.com/charles2ke/crabs/releases/tag/v0.1.0
