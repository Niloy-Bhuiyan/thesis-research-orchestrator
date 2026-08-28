# Telegram

Remote control and approvals, using long polling from the daemon so the laptop never
exposes an inbound port.

## Setup

1. Message `@BotFather`, send `/newbot`, choose a name and a username ending in `bot`
2. Save the token to `.secrets/telegram_token`
3. Open your bot, send `/start`
4. Save your numeric chat id to `.secrets/telegram_allowlist`

## Security

The allowlist is enforced before dispatch. An unknown sender receives **no reply at
all**, not an error, so the bot does not confirm its own existence to strangers.

The update offset advances even for dropped updates, otherwise a stranger could wedge
the queue by messaging repeatedly.

The bot refuses to start with an empty allowlist.

## Commands

`/status` `/experiments` `/providers` `/pause` `/resume` `/logs` `/help`

## Approvals

Failures needing a decision arrive with inline Approve / Reject / Logs buttons.
Callbacks are acknowledged so the button stops spinning, and a duplicate press is
handled idempotently: the second press returns "already decided" rather than approving
twice.

## Resilience

A Telegram outage is caught and logged. It never takes the research loop down.
