# Relationship to autoresearch

Andrej Karpathy's `autoresearch` was architectural inspiration for the iterative
experiment loop. No code was copied and the repository is not a dependency.

## What was taken

The shape of the loop: observe, diagnose, hypothesise, propose, validate, apply, run,
evaluate, keep or revert, update memory, repeat.

## What differs, and why

**Generalised across model families.** Training and evaluation commands, primary
metric, direction, budgets and editable-file globs come from per-project
configuration, so the framework is not bound to one architecture.

**A policy layer autoresearch does not need.** A single-author exploratory loop can
change anything. A thesis cannot: the held-out test set, dataset split and evaluation
metric must be structurally unchangeable, and methodology changes must reach a human.

**Failures are first-class.** Diagnosis, classification, safe-retry rules and budgets
exist because the target environment is a shared free GPU queue that fails often, at
night, for mechanical reasons.

**A human is in the loop by design**, not as a fallback: approvals, external run
handoff, and modes that restrict autonomy.

## Attribution

See [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
