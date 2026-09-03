# Security and responsible use

Open Claw reads only endpoints the operator configures. It contains no live
consulate endpoints, does not bypass CAPTCHA, WAF, authentication, or rate
limits, and never books, holds, or submits an appointment.

Keep secrets in `${ENV_VAR}` references. Literal password-like fields and
Telegram tokens are rejected. Authentication state remains in memory. Logs
redact Telegram tokens, credential query parameters, and common personal fields
in booking URLs. JSON logging follows the same rules.

Use generous polling intervals and jitter, validate configuration offline before
deployment, protect configuration and state files, and remember that public
Actions logs are world-readable. Verify terms, authorization, and local law for
every endpoint.

For vulnerability reporting, see [SECURITY.md](../SECURITY.md).
