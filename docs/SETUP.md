# Setup

## Prerequisites

Git, Python 3.11+, Node 20+, and these CLIs authenticated with your own accounts:
`gh`, `claude`, `codex`, `kaggle`, `vercel`.

## 1. Install

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

## 2. Initialise

```bash
researchos init --project my-thesis
```

Writes `researchos.yaml` and creates `data/state.sqlite3`. The config file holds
*paths* to secrets, never secret values, so it is safe to commit.

## 3. Secrets

All live under `.secrets/`, which is gitignored. Nothing here is printed back.

| File | How to get it |
|---|---|
| `telegram_token` | BotFather, `/newbot` |
| `telegram_allowlist` | Your numeric chat id, one per line |
| `supabase_project_ref` | Supabase project reference |
| `supabase_service_role_key` | Supabase, API keys |
| `api_token` | Generated automatically on first `researchos start` |

Kaggle credentials go in `~/.kaggle/kaggle.json`, from **Kaggle, Settings, API tokens,
Legacy API Credentials, Create Legacy API Key**. This is the current flow; older
guides describing "Account, Create New API Token" are out of date.

## 4. Verify

```bash
researchos doctor
```

Every line must read PASS before an overnight run. `doctor` uses the same code paths
the daemon does, so a green result means the daemon will work, not merely that files
exist.

## 5. Research policy

Create `research_policy.yaml` in your project and point the project row at it. See
[SCIENTIFIC_GUARDRAILS.md](SCIENTIFIC_GUARDRAILS.md) for the full vocabulary.

```yaml
research_goal:
  primary_metric:
    name: E_AURC
    direction: minimize

agent_permissions:
  batch_size: {policy: editable}
  learning_rate: {policy: editable}
  optimizer: {policy: approval_required}
  loss_function: {policy: approval_required}
  model_architecture: {policy: approval_required}
  dataset_split: {policy: locked}
  evaluation_metric: {policy: locked}
```

A field you do not list defaults to `approval_required`, never `editable`. An
unclassified knob must not be silently free to change.

## 6. Run

```bash
researchos start
```
