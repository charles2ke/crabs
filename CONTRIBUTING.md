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

Version `1.0.0` is the first tagged release, published as the `v1` tag. From
`1.0.0` onwards the configuration file format, the state-file format, and the
documented exit codes are a stable contract: they only change incompatibly in a
new major release.

Tags use `vX.Y.Z`; the shorter `vX` and `vX.Y` forms are also accepted and are
normalised to `X.Y.0` / `X.0.0` when the release workflow looks up the changelog
section and checks `pyproject.toml`.

To release:

1. Move the relevant entries from `Unreleased` into a dated version section in
   `CHANGELOG.md`.
2. Bump the version in `pyproject.toml`, then merge the release pull request. The
   tag must resolve to the same version as `pyproject.toml`, or the workflow fails.
3. Tag the merge commit `vX.Y.Z` and push the tag. Push a tag rather than creating
   a release from the GitHub UI: only a tag push (or a `workflow_dispatch` run of
   `release.yml` for that tag) runs the tests, builds the distributions, and
   attaches them with the changelog notes.
4. Wait for the release workflow to pass tests and mypy, build both distributions,
   and publish the GitHub Release.
5. Verify the release notes, sdist, wheel, and installed `openclaw --version`.

If a release exists with no sdist/wheel and auto-generated notes, it was created
outside the workflow. Re-run `release.yml` via `workflow_dispatch` with that tag;
the workflow updates the existing release in place, uploading the distributions
and replacing the notes with the changelog section. The tag itself does not need
to be deleted or moved.

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
