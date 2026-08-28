# Roadmap

Ordered by what most increases trust in the system.

## Next

1. **First real Kaggle submission.** The only way to prove the submit-and-retrieve
   path. Requires explicit approval; nothing has consumed GPU quota yet.
2. **Sign in once on the deployed dashboard**, which creates the Supabase user and lets
   the daemon sync.
3. **Wire a real research project**: policy file, editable and locked file globs, real
   primary metric.

## Then

4. Automatic branch-per-experiment and commit-on-change
5. Provider-driven code modification with diffs surfaced for approval
6. Research context pack: deterministic retrieval over methodology and prior results
7. Scientific Guard page reading the live policy file rather than illustrating it
8. Simulated failure acceptance suite as a documented, runnable report

## Later

9. Multi-seed orchestration with confidence intervals
10. Statistical testing in the readiness checklist
11. Literature and novelty checking with verified identifiers
12. Hermes adapter, if a provider ever becomes available to it
