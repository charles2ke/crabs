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

All notifier formats identify `new`, `disappeared`, `improved`, and `health`
events. Webhook and file records include `event_type`, `message`, watch identity,
timestamp, and slots.

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
default on port 587; set `use_ssl` for implicit TLS (usually port 465) and
`use_tls` to `false` only for a trusted local relay.

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
