# Notifiers

| Type | Options | Behavior |
| --- | --- | --- |
| `console` | none | Human-readable stdout alerts. |
| `file` | `path` | Appends one JSON object per alert as JSON Lines. |
| `webhook` | `url`, optional `headers`, `timeout` | POSTs structured JSON; HTTP(S) only. |
| `telegram` | `bot_token`, `chat_id`, `timeout`, `disable_notification` | Telegram Bot API HTML messages. |

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

Messages show centre, origin, destination, category, and slot details. Values and
links are HTML escaped, previews are disabled, long digests split on slot
boundaries, and HTTP 429 is retried once for a positive bounded `retry_after`.
Bot tokens are redacted from failures and must not be literal config values.
