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
