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

## Two dashboard modes, and why you need both

| Where you open it | What works |
|---|---|
| `localhost` on the daemon machine | Everything: experiments, metrics, lineage, logs |
| The deployed Vercel URL | Remote page only: status, events, approvals |

This split is forced by browser security, not by choice. A page served over https
cannot fetch `http://127.0.0.1`: Chrome gates public-to-loopback requests behind a
user permission prompt, and the `Access-Control-Allow-Private-Network` opt-in the API
sends is necessary but not sufficient. It was verified empirically that `fetch` from
the deployed origin fails while the same daemon answers 200 locally.

So run the dashboard locally for full data:

```bash
npm run dev --prefix dashboard
```

and use the deployed URL for remote status and approvals, which reads Supabase rather
than your laptop. The local-only pages state this when opened from the deployed site
rather than claiming the runner is offline.
