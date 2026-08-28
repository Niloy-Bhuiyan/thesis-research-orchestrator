# Hermes

Hermes Agent v0.20.6 (NousResearch, MIT) is **installed but not used**.

## Why it is installed

You asked for it explicitly, after being told what follows.

## Why it is not used

Hermes is a body, not a brain. It needs a model provider, and it only supports:

- Individual paid API keys, or
- A paid Nous Portal subscription

It cannot drive your Claude Code or ChatGPT subscriptions, which work only through
their own CLIs. Using Hermes as the research controller would mean per-call API
spending, which is explicitly off in this project.

## Why nothing is lost

Everything Hermes would have contributed is already required elsewhere in the design:

| Hermes provides | Already built here |
|---|---|
| Agent memory | SQLite state engine, specified as authoritative |
| Telegram | Direct bot with an allowlist |
| Scheduling | The daemon tick loop |
| Process supervision | The daemon, with crash recovery |
| Model routing | The provider router |

## Current state

Installed at `%LOCALAPPDATA%\hermes`, the binary runs, and `.env` contains only empty
config placeholders with **zero API keys**. It sits idle.

To adopt it later, implement an adapter against the orchestrator interface rather than
restructuring around it. The local state machine stays authoritative either way.
