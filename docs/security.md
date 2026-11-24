# Security and responsible use

Open Claw reads only endpoints the operator configures. It contains no live
consulate endpoints, does not bypass CAPTCHA, WAF, authentication, or rate
limits, and never books, holds, or submits an appointment. When a portal
answers with a CAPTCHA or anti-bot challenge, the poll fails and a
provider-health warning is raised for a human to handle; the challenge is
never solved or worked around. When a portal answers HTTP 429 or 503 with
`Retry-After`, the watch is not polled again until that time (at most 24
hours), including by later scheduled runs that share the state file.

Keep secrets in `${ENV_VAR}` references. Literal password-like fields, Telegram
bot tokens, Slack and Discord webhook URLs, SMTP passwords, ntfy and Pushover
tokens, and heartbeat ping URLs are rejected; keep an ntfy topic on the public
server and a Pushover user key in the environment as well. Diagnostics and
heartbeat errors show only the ping URL's scheme and host. Authentication state
remains in memory. Logs redact Telegram tokens, credential query parameters, and
common personal fields in booking URLs. JSON logging follows the same rules.

Use generous polling intervals and jitter, validate configuration offline before
deployment, protect configuration and state files, and remember that public
Actions logs are world-readable. `--validate-config` makes no network requests,
`--list-slots` polls the configured portals once without alerting, and
`--test-notifiers` delivers a real test message to every notifier. Verify
terms, authorization, and local law for every endpoint.

For vulnerability reporting, see [SECURITY.md](../SECURITY.md).
