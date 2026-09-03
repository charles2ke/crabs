# Changelog

All notable changes are documented here in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
format. This project follows semantic versioning.

## [Unreleased]

### Added
- Dedicated CLI and notifier tests.
- Slot disappearance/improvement events, quiet hours, throttling, health warnings,
  statistics, offline validation, structured JSON logs, and mypy CI.
- Contributor, security, issue, pull-request, and operator documentation.

## [0.1.0] - 2026-09-03

### Added
- PR #1: core watcher, config, state, mock/HTTP providers, and console/file/webhook notifiers.
- PR #2: authenticated reusable sessions and one re-authentication retry.
- PR #3: VFS Global, TLScontact, and BLS International adapters with offline fixtures.
- PR #4: Telegram notifier with safe formatting, splitting, redaction, and bounded 429 retry.
- PR #5: scheduled execution, locking, atomic state, pruning, bootstrap, and cron exit codes.
