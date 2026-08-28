# Providers

Two subscription-authenticated CLIs. No API keys, no billing.

| Provider | Auth | Invocation |
|---|---|---|
| Codex | ChatGPT subscription | `codex exec --skip-git-repo-check -m <model>` |
| Claude Code | Claude subscription | `claude -p` |

Default order: Codex, then Claude Code. Configurable in `researchos.yaml`.

## Failover

The router moves to the next provider on auth failure, usage limit, rate limit,
timeout or command failure. When all are exhausted it pauses the loop, persists state
and notifies you. It never escalates to a paid API.

`assert_no_paid_fallback` refuses to run at all if `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY` is set without explicit opt-in.

## Model pinning on this machine

The global `~/.codex/config.toml` pins `gpt-5.6-sol`, which the API rejects for
ChatGPT accounts. Supported models were read from `~/.codex/models_cache.json`:
`gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4-mini`.

The router passes `-m gpt-5.6-terra` explicitly on every call rather than editing your
global config, which would affect your other projects.

## Reported status

`available`, `auth_required`, `unavailable`, `unknown`. Remaining quota is **not**
shown, because neither CLI reports a reliable number. Displaying an estimate would be
worse than displaying nothing.

## Handoff

Providers never read each other's transcripts. A `Handoff` object carries experiment
id, parent, status, failure class, diagnosis, methodology version, commit, proposed
change and next action as JSON, so a provider switch loses nothing.
