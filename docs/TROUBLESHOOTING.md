# Troubleshooting

## `researchos doctor` shows codex unavailable

Check `codex login status`. If it reports logged in but calls still fail, your global
`~/.codex/config.toml` may pin a model your account cannot use. Read the supported
list from `~/.codex/models_cache.json` and set `providers.codex_model` in
`researchos.yaml`.

## Kaggle raises PermissionError on kaggle.json

Something removed inherited ACLs. Restore them:

```powershell
icacls "$env:USERPROFILE\.kaggle\kaggle.json" /reset
```

## Dashboard shows "Local runner offline"

The daemon is not reachable. Start it with `researchos start`. If it is running, check
the address and token in Settings; `researchos token` prints the token.

## Dashboard shows "Not authorized"

The daemon is reachable but the token is wrong. Re-paste from `researchos token`.

## Telegram bot does not answer

Confirm your chat id is in `.secrets/telegram_allowlist`. An unlisted sender is
ignored silently by design. Check `researchos events` for `telegram.error`.

## Remote page shows no runner

No Supabase auth user exists until you sign in on the deployed dashboard once. Until
then the daemon logs `coordination.error` with `OwnerNotFound` and keeps running
normally.

## The loop paused itself

Check `researchos status` for the pause reason: consecutive failures, session
experiment limit, provider call budget, or wall-clock limit. Resume with `/resume` on
Telegram or from the dashboard.

## Tests pass but real Kaggle fails

Expected, and documented. Tests use a seam. See [STATUS.md](STATUS.md).
