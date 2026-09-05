# Security policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.0.x (tagged `v1`) | Yes |
| 0.1.x and earlier | No |

Security fixes are made on the latest release and the `main` branch. Older
releases are not patched; upgrade to the latest `1.0.x` release instead.

## Scope

Open Claw observes configured public or authorized availability endpoints and
sends notifications. It never books, holds, or submits appointments and does not
support CAPTCHA, WAF, authentication, or rate-limit bypass. Runtime code is
standard-library only, so the deployed attack surface is this repository plus the
Python runtime.

In scope: credential or personal-data leaks in logs, diagnostics, state files, or
release artifacts; unsafe handling of configured URLs and provider responses;
state-file or locking flaws that a local attacker can abuse; and weaknesses in the
release workflow.

Out of scope: vulnerabilities in third-party appointment portals, requests to add
booking automation or anti-bot bypasses, and issues that require an operator to
configure an endpoint they are not authorized to poll. For operator-side hardening
guidance, see [docs/security.md](docs/security.md).

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Security** tab using
**Report a vulnerability** (private vulnerability reporting). Include affected
versions, reproduction steps, impact, and any suggested mitigation. Do not open
a public issue for an undisclosed vulnerability or include real credentials,
personal data, or live booking details. Redact tokens and URLs in any log excerpt
you attach.

Maintainers will acknowledge a report as soon as practical, coordinate validation
and remediation, and credit reporters who wish to be credited. Please allow time
for a fix to ship in a release before disclosing publicly.
