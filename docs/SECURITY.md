# Security

## Secret handling

Every secret is a file under `.secrets/`, gitignored. Config files store *paths*, not
values, so `researchos.yaml` is always safe to commit.

Secrets are never printed back after being saved, never logged, never placed in URLs,
and never included in the browser bundle.

| Secret | Location |
|---|---|
| Telegram bot token | `.secrets/telegram_token` |
| Supabase service role key | `.secrets/supabase_service_role_key` |
| Supabase DB password | `.secrets/supabase_db_password` |
| Local API token | `.secrets/api_token` |
| Kaggle credentials | `~/.kaggle/kaggle.json` |
| GitHub, Claude, Codex, Vercel | Managed by their own CLIs |

## CI enforcement

The secret-scan job greps tracked files for the credential shapes this project handles
and asserts that each secret path is gitignored. Findings fail the build rather than
warn, because a leaked key cannot be un-leaked.

## Network posture

- The daemon opens **no inbound port**. Coordination is outbound polling only.
- The local API binds `127.0.0.1` and still requires a bearer token, because anything
  running on the machine could otherwise reach a loopback port.
- `/api/health` is deliberately unauthenticated so the dashboard can distinguish
  "daemon offline" from "wrong token". It reveals nothing else.

## Command safety

Dashboard commands are checked against an explicit allowlist, so a new capability must
be added deliberately rather than by string coincidence. `stop` and `set_mode`
additionally require `confirm: true`.

## Filesystem safety

`is_within_workspace` rejects any path outside the configured root, including `..`
traversal. The daemon runs shell commands and edits files, so this is checked before
anything happens.

## No silent spending

Paid API fallback defaults OFF and the system refuses to start if an API key is present
without explicit opt-in. Multiple Kaggle accounts are not supported by design.
