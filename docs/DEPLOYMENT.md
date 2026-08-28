# Deployment

## Supabase

Carries only coordination metadata: liveness, a command queue, approval requests and
event lines truncated to 500 characters. Experiments, metrics, logs, code, datasets
and credentials never leave your machine.

Tables: `runners`, `commands`, `proposals`, `events`. All owner-scoped under row level
security, so one account cannot read or command another's runner.

Commands carry an expiry, so one queued while the laptop was closed does not fire
hours later.

```bash
npx supabase link --project-ref <ref>
npx supabase db push
```

## Vercel

```bash
cd dashboard
vercel link
vercel deploy --prod
```

Environment variables, production scope:

| Variable | Notes |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Public |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Public, protected by RLS |
| `NEXT_PUBLIC_RUNNER_ID` | Which runner this dashboard controls |

The **service-role key is never deployed.** It stays on the machine running the daemon.
Anything prefixed `NEXT_PUBLIC_` is embedded in the browser bundle and must be safe to
expose.

## Two dashboard modes

Locally the dashboard reads the loopback API directly and shows everything. Deployed,
the Remote page shows only synced coordination data, and says so explicitly rather
than rendering empty tables.
