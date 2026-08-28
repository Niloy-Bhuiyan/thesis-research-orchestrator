# Threat model

## Defended against

**A stranger finding the Telegram bot.** Allowlist enforced before dispatch; unknown
senders get no reply at all. The offset still advances so they cannot wedge the queue.

**Another Supabase account reading your research.** Row level security scopes every
table to the owner. Command pulls are scoped to the runner id, so a second machine
cannot drain your queue.

**The public dashboard leaking data.** Only the anon key ships to the browser, and it
is useless without an authenticated session. The service-role key never leaves the
laptop.

**A stale command firing late.** Commands expire after an hour, so one queued while
the laptop was closed does not execute unexpectedly.

**A returned external bundle corrupting the record.** Manifest and config hash are
validated before import; a mismatch is rejected rather than attached.

**Accidental credential commits.** Gitignore, a CI secret scan, and a pre-commit grep.

**An agent damaging the science.** Two independent gates, inviolable constraints, mode
restrictions, and preservation of failed experiments.

**A runaway loop.** Budgets persisted in SQLite, so a restart grants no fresh
allowance.

## Not defended against

**A compromised local machine.** Anything running as your user can read `.secrets/`.
This is the same trust boundary as your SSH keys and browser sessions.

**A malicious provider response.** Claude and Codex output is trusted to the extent
policy allows it to act. The policy engine limits blast radius; it does not sandbox
generated code, which runs on Kaggle with your account's permissions.

**Supabase or Vercel being compromised.** Coordination metadata would be exposed.
Research data would not, since it never goes there.

**A malicious person you hand an external run bundle to.** They see your code and
config. Config-hash validation detects altered *results*; it cannot stop someone
reading what you sent them.

**Kaggle notebook content.** Code submitted to Kaggle runs there under your account.
The system does not review it for you.
