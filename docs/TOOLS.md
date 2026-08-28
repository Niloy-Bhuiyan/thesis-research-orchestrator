# External tools

Every tool installed, and why.

| Tool | Purpose | Auth | Necessary |
|---|---|---|---|
| Git | Source of truth for research code | n/a | Yes |
| GitHub CLI | Repo creation and CI inspection | Browser OAuth | Yes |
| Python 3.13 | Daemon, engine, tests | n/a | Yes |
| Node 24 | Dashboard build | n/a | Yes |
| Claude Code | Provider | Subscription | Yes |
| Codex CLI | Provider | ChatGPT subscription | Yes |
| Kaggle CLI 2.2.4 | GPU execution | `kaggle.json` | Yes |
| Vercel CLI | Dashboard deploy | Browser OAuth | Yes |
| Supabase CLI | Migrations, project management | PAT / device flow | Yes |
| Hermes Agent | Installed, unused | None configured | No, see HERMES.md |

## Python dependencies

`pyyaml`, `requests`. Dev: `pytest`, `ruff`. Deliberately small.

The local API uses the standard library's `http.server` rather than FastAPI: it is a
single-user loopback service, and two fewer dependencies to pin is worth more than the
ergonomics.

## Node dependencies

`next` (pinned 15.5.24), `react`, `react-dom`, `@supabase/supabase-js`. No CSS
framework; the stylesheet is hand-written so the visual language stays deliberate.

## MCP servers, skills, plugins

None installed for this project. Every capability needed was available through normal
CLIs, which are simpler to audit and pin.
