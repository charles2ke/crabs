# Notifiers

| Type | Options | Behavior |
| --- | --- | --- |
| `console` | none | Human-readable stdout alerts. |
| `file` | `path` | Appends one JSON object per alert as JSON Lines. |
| `webhook` | `url`, optional `headers`, `timeout` | POSTs structured JSON; HTTP(S) only. |
| `telegram` | `bot_token`, `chat_id`, `timeout`, `disable_notification` | Telegram Bot API HTML messages. |
| `slack` | `webhook_url`, optional `username`, `icon_emoji`, `timeout` | Slack incoming webhook with Block Kit sections. |
| `discord` | `webhook_url`, optional `username`, `timeout` | Discord webhook with a single embed. |
| `email` | `host`, `sender`, `recipients`, optional `port`, `username`, `password`, `use_tls`, `use_ssl`, `subject_prefix`, `timeout` | Plain-text email over SMTP. |
| `ntfy` | `topic`, optional `server`, `token`, `priority`, `tags`, `timeout` | Push notification through ntfy.sh or a self-hosted ntfy server. |
| `pushover` | `token`, `user`, optional `device`, `priority`, `sound`, `retry`, `expire`, `timeout` | Push notification through the Pushover API. |

All notifier formats identify `new`, `disappeared`, `improved`, and `health`
events, plus the `test` event sent by `--test-notifiers`. Webhook and file
records include `event_type`, `message`, watch identity, timestamp, and slots.

## Testing notifiers

```bash
python -m openclaw --config config.json --test-notifiers
```

```text
notifier ntfy: ok
notifier webhook: FAILED: webhook delivery failed: HTTP Error 500: Internal Server Error
```

Each configured notifier receives one alert labelled as a test, for the first
watch. Failures are printed with secrets redacted, the command exits `2` if any
notifier failed and `0` otherwise, and no provider is polled and no state is
read or written. Unlike `--validate-config`, this makes real network requests.

## Failed deliveries

When a notifier raises an error during a normal run, the failure is logged
(`notifier_error`) and the alert is kept in the state file to be retried on the
following cycles or scheduled runs. Retries go only to the notifiers that
failed, wait for quiet hours but do not count against the throttle again, and
stop after 5 attempts in total, when an `alert_dropped` error is logged; a
successful retry logs `alert_retried`. An alert counts towards exit code `10`
once at least one notifier has accepted it.
Retries are identified by each notifier's position and type in `notifiers`, so
reordering or removing notifiers drops retries that no longer match. Removing a
watch, or changing its `country_from`, `country_to`, `city`, or
`visa_category`, likewise drops its pending retries.

## Telegram

Create a bot with BotFather, send it a message, obtain the destination chat ID,
then configure:

```json
{"type": "telegram", "bot_token": "${OPENCLAW_TELEGRAM_BOT_TOKEN}",
 "chat_id": "${OPENCLAW_TELEGRAM_CHAT_ID}", "disable_notification": false}
```

```bash
export OPENCLAW_TELEGRAM_BOT_TOKEN="paste-your-botfather-token"
export OPENCLAW_TELEGRAM_CHAT_ID="123456789"
python -m openclaw --config examples/dublin_telegram.json --once
```

```text
2 new Schengen slot(s)
Centre: Dublin (IE)
Destination: FR
Visa category: short-stay
• 2026-09-14 09:20 — 2 seat(s) — booking link
```

Messages show centre, origin, destination, category, and slot details. Values and
links are HTML escaped, previews are disabled, long digests split on slot
boundaries, and HTTP 429 is retried once for a positive bounded `retry_after`.
Bot tokens are redacted from failures and must not be literal config values.

## Slack

Create an incoming webhook for the destination channel in your Slack app
settings, then configure:

```json
{"type": "slack", "webhook_url": "${OPENCLAW_SLACK_WEBHOOK_URL}",
 "username": "Open Claw", "icon_emoji": ":calendar:"}
```

```bash
export OPENCLAW_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
python -m openclaw --config examples/dublin_slack.json --once
```

Messages use a Block Kit section with one bullet per slot, booking links, and a
context line with the watch label and timestamp. Digests longer than Slack's
3000-character block limit are truncated with an `… and N more slot(s)` summary.

## Discord

Create a channel webhook under *Integrations*, then configure:

```json
{"type": "discord", "webhook_url": "${OPENCLAW_DISCORD_WEBHOOK_URL}",
 "username": "Open Claw"}
```

```bash
export OPENCLAW_DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python -m openclaw --config examples/dublin_discord.json --once
```

Alerts are sent as one embed whose title is the event summary and whose
description lists the slots with Markdown booking links, truncated to the
4096-character embed limit.

## Email

Any SMTP server works, including provider app passwords. STARTTLS is used by
default on port 587; set `use_ssl` for implicit TLS (usually port 465), which
disables STARTTLS when `use_tls` is omitted or `null`. Do not set both
`use_ssl` and `use_tls` to `true`. Set `use_tls` to `false` with `use_ssl` set
to `false` only for a trusted local relay, because that sends plaintext SMTP.

```json
{"type": "email", "host": "smtp.example.com", "port": 587, "use_tls": true,
 "sender": "openclaw@example.com", "recipients": ["applicant@example.com"],
 "username": "${OPENCLAW_SMTP_USERNAME}", "password": "${OPENCLAW_SMTP_PASSWORD}"}
```

```bash
export OPENCLAW_SMTP_USERNAME="openclaw@example.com"
export OPENCLAW_SMTP_PASSWORD="app-password"
python -m openclaw --config examples/dublin_email.json --once
```

Both credentials are required together and the password must use a `${ENV_VAR}`
placeholder. Slack and Discord webhook URLs must also be environment
placeholders, because they grant posting rights to the channel.

## ntfy

[ntfy](https://ntfy.sh) delivers push notifications to its Android, iOS, and web
apps. Pick a hard-to-guess topic name and subscribe to it in the app, then
configure:

```json
{"type": "ntfy", "server": "https://ntfy.sh", "topic": "${OPENCLAW_NTFY_TOPIC}",
 "priority": "high", "tags": ["passport_control"]}
```

```bash
export OPENCLAW_NTFY_TOPIC="openclaw-$(openssl rand -hex 12)"
python -m openclaw --config examples/dublin_ntfy.json --once
```

Alerts are published as JSON to the server root, so `server` is the base URL
(default `https://ntfy.sh`), never the topic URL, and the topic does not appear
in request URLs or errors. The notification title is the event summary, the
body lists slots up to ntfy's 4096-byte message limit, and tapping it opens the
first booking link. `priority` is `1`–`5` or `min`, `low`, `default`, `high`,
`max`, or `urgent` (default `default`); `tags` is a list of strings, at most 512
bytes combined, and tags matching emoji short codes are shown as emojis.

Anyone who knows a topic on the public ntfy.sh server can read and post to it,
so keep the topic in an environment variable. For a protected topic or a
self-hosted server, add an access token, which must be a placeholder and is
sent as a bearer token: `"token": "${OPENCLAW_NTFY_TOKEN}"`.

## Pushover

Create an application at [pushover.net](https://pushover.net) to get its API
token, copy your user key from the dashboard, then configure:

```json
{"type": "pushover", "token": "${OPENCLAW_PUSHOVER_TOKEN}",
 "user": "${OPENCLAW_PUSHOVER_USER}", "priority": 1}
```

```bash
export OPENCLAW_PUSHOVER_TOKEN="your-application-token"
export OPENCLAW_PUSHOVER_USER="your-user-key"
python -m openclaw --config examples/dublin_pushover.json --once
```

The title is the event summary, the body lists slots up to Pushover's
1024-character limit, and the first booking link is attached as a supplementary
URL. `priority` ranges from `-2` (silent) to `2` (emergency, repeated every
`retry` seconds, minimum `30` and default `60`, until acknowledged or `expire`
seconds pass, at most `10800` and default `3600`). `device` limits delivery to
the named device (or comma-separated devices) and `sound` selects a
notification sound. The application token must
be a placeholder; keep the user key in the environment too. API error messages
are reported with both values redacted.
