# Status: what is verified, what is not

Read this before trusting any part of the system with real thesis work. It
distinguishes three things that are easy to conflate: code that exists, code that is
tested, and code that has been exercised against the live service it talks to.

Last updated: 2026-08-29.

## Summary

The engine is built and its decision logic is tested thoroughly. **The full research
loop has never executed end to end against real Kaggle compute.** Until it has, treat
this as a system that is ready to be proven, not one that is proven.

## Component status

| Component | Code | Tests | Verified against the live service |
|---|---|---|---|
| State engine (SQLite) | Complete | 18 | N/A — local only |
| Provider router | Complete | 25 | **Yes** — real Codex↔Claude failover observed |
| Failure classifier | Complete | 20 | N/A — pure logic over log text |
| Policy engine | Complete | 25 | N/A — pure logic |
| Experiment optimizer | Complete | 30 | N/A — pure decision function |
| Telegram | Complete | 20 | **Yes** — outbound, inbound, buttons, allowlist |
| Local API | Complete | 18 | **Yes** — live server on an ephemeral port |
| Daemon | Complete | 31 | **Yes** — ran as a process, answered Telegram |
| External-run bundle | Complete | 20 | **Yes** — full export → import round trip |
| Coordination (Supabase) | Complete | 18 | **Partly** — see below |
| Kaggle runner | Complete | 25 | **Partly** — see below |
| Dashboard | Complete | Build only | **Yes** — rendered against live local API |

Total: 256 automated tests, all passing.

## The two important gaps

### 1. Kaggle submission has never run

`whoami`, `kernels status` and error handling were verified against the real Kaggle
API using the researcher's own account. **`kernels push` — the call that actually
submits a job — has never been executed against real Kaggle.** Neither has output
retrieval or log download.

What this means concretely: metadata field names come from `kaggle kernels init`
rather than assumption, and status parsing was checked against a real kernel in
`ERROR` state, but the submit-and-retrieve path is unproven. Expect to find problems
there on the first real run.

The first real submission is deliberately gated on explicit human approval and has
not been requested.

### 2. Supabase sync has not carried a real session

The project, schema, row level security and command queue are live and were verified:
REST is reachable, the tables exist, the queue reads correctly. **But no Supabase auth
user exists yet**, so `resolve_owner()` currently raises `OwnerNotFound` by design, and
no heartbeat, event or approval has actually been synced.

To close this: sign in once on the deployed dashboard, which creates the user, then
start the daemon.

## What has never been run

- A real Kaggle experiment, of any size
- The complete loop: propose → submit → monitor → diagnose → retry → record
- Provider-driven code modification (the router is proven; nothing has yet asked it
  to edit research code and commit the result)
- Any real research project — no `research_policy.yaml` exists for actual thesis work,
  and the experiments table is empty

## Known limitations

- **The deployed dashboard cannot read your local daemon.** This is a browser security
  boundary, not a bug that can be fixed here. Chrome gates requests from an https page
  to a loopback address behind a user permission prompt, and opting in with
  `Access-Control-Allow-Private-Network` (which the API now sends) is necessary but not
  sufficient. Verified empirically: `fetch` from the deployed origin fails even with a
  healthy daemon serving 200 locally.

  What this means in practice: **run the dashboard on your own machine** (`npm run dev`
  in `dashboard/`) to see experiments, metrics, lineage and logs. Use the deployed
  **Remote** page for status and approvals from anywhere, which works because it reads
  Supabase rather than your laptop. The affected pages now say this explicitly instead
  of showing a misleading "offline" banner.

- **Experiment code generation is not automated.** The system packages and submits a
  notebook you supply. It does not yet write the training code itself.
- **Git integration is partial.** Experiments record a commit SHA, but automatic
  branch-per-experiment and commit-on-change are not implemented.
- **No literature or novelty checking.** The publication-readiness checklist tracks
  criteria; it does not verify citations. It never claims a venue will accept work.
- **Hermes is installed but unused.** See [HERMES.md](HERMES.md).
- **Two moderate npm advisories remain** in `postcss`, transitively via Next. Fixing
  them requires a Next major bump. The critical Next RCE advisory *was* fixed by
  pinning 15.5.24.
- **The dashboard's Scientific Guard page shows an illustrative field table**, not the
  live contents of your policy file. The daemon enforces the real policy; that page
  currently demonstrates the vocabulary.

## What was verified with your own accounts

These are not simulations:

- GitHub CLI authenticated as `Niloy-Bhuiyan`
- Claude Code returned `CLAUDE_AUTH_OK` non-interactively
- Codex returned `CODEX_AUTH_OK` (model `gpt-5.6-terra`)
- Kaggle authenticated as `niloybhuiyan`; real kernel status read
- Telegram bot `@niloy_research_orch_bot` sent and received; buttons worked; a
  duplicate approval press was correctly rejected as already-decided
- Vercel deployment live and serving
- Supabase project `research-orchestrator` created, migrated, RLS enabled

## Bugs found by testing, and fixed

Recorded because they indicate the class of problem the tests catch:

1. **Codex was unreachable from the daemon.** npm installs it as a `.cmd` shim and
   Python's `subprocess` ignores `PATHEXT`, so every Codex call failed with
   `FileNotFoundError` while Claude worked. Fixed with `shutil.which`.
2. **The heartbeat un-paused a paused daemon.** `heartbeat()` wrote `status='running'`
   every tick, so a loop that paused itself after five consecutive failures resumed on
   the next tick, defeating the budget entirely.
3. **The lineage graph rendered flat.** HTML collapsed the leading spaces used for
   indentation, so every branch appeared at the same depth.
4. **A config-error pattern never matched.** The regex expected a space in
   `ScannerError`.
