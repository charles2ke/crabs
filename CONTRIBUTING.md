# Contributing

Use Python 3.10 or newer. Runtime code must remain standard-library only.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m mypy openclaw
```

Tests must be fully offline. Never add live credentials, portal traffic, booking
automation, seat holds, CAPTCHA/WAF/rate-limit bypasses, or undocumented
authentication workarounds.

## Releasing

Open Claw follows semantic versioning. Treat incompatible changes to configuration
files, the state-file format, or documented exit codes as breaking changes that
require a major release. Additive, opt-in functionality is a minor release, and
backwards-compatible fixes are patch releases.

To release:

1. Move the relevant entries from `Unreleased` into a dated version section in
   `CHANGELOG.md`.
2. Bump the version in `pyproject.toml`, then merge the release pull request.
3. Tag the merge commit `vX.Y.Z` and push the tag.
4. Wait for the release workflow to pass tests and mypy, build both distributions,
   and publish the GitHub Release.
5. Verify the release notes, sdist, wheel, and installed `openclaw --version`.

State files are backwards compatible. PR #5 added appointment dates and pruning;
PR #6 added health tracking, queued alerts, and throttle state. New releases may
add state fields, but must continue loading older list and flat-object formats.

## Providers

Subclass `Provider`, return typed `Slot` values, and register the factory in the
provider registry. Keep parsing pure where possible, validate configuration
before requests, redact URLs, add offline fixtures for normal/empty/malformed and
sign-in responses, and document authorization/terms constraints.

## Notifiers

Subclass `Notifier`, raise `NotifierError` on delivery failure, register it in
`build_notifier`, preserve all event types, redact secrets and PII, and stub the
transport in tests.

Keep changes focused, update user-facing docs and `CHANGELOG.md`, and include
tests. Mypy currently checks the package with practical strict options
(`check_untyped_defs`, disallowing incomplete definitions/generics, and warnings).
The ratchet plan is to eliminate remaining `Any`-heavy config/HTTP boundaries,
then enable `disallow_untyped_defs`, `disallow_any_generics`, and finally
`strict = true` module by module.
