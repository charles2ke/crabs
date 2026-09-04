# Changelog

All notable changes are documented here in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
format. This project follows semantic versioning.

## [Unreleased]

## [0.2.0] - 2026-09-04

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

[Unreleased]: https://github.com/charles2ke/crabs/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/charles2ke/crabs/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/charles2ke/crabs/releases/tag/v0.1.0
