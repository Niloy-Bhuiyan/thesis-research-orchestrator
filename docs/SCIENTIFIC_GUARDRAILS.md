# Scientific guardrails

The system assumes an agent left alone with a thesis will eventually propose something
scientifically damaging, and prevents it structurally rather than by instruction.

## Two independent gates

A change proceeds unattended only when **both** agree:

1. **The failure classifier** - is this fix confident and scientifically inert?
2. **The policy engine** - does the policy file and current mode permit touching this
   field?

Either objecting routes to a human. The redundancy is deliberate: a misclassification
alone cannot authorise a change, and a permissive policy alone cannot either.

## Field permissions

| Permission | Meaning |
|---|---|
| `editable` | The agent may change it in auto exploration |
| `approval_required` | Always needs a human, in every mode |
| `locked` | Never changeable, by anyone, through this system |

Unlisted fields default to `approval_required`.

## Modes tighten, never loosen

| Mode | Effect |
|---|---|
| `manual` | Even `editable` fields need approval |
| `auto_exploration` | `editable` fields are free; the rest still gated |
| `locked_evaluation` | Only infrastructure recovery, and it still needs approval |

## Inviolable constraints

`held_out_test_set` and `data_leakage` are refused regardless of mode, and regardless
of what the policy file says. A policy marking the held-out test set `editable` is
overridden, not honoured.

## Never automatic

`EXPERIMENTAL_VALIDITY` and `METHODOLOGY` diagnoses always require a human, whatever
the confidence. A fix whose `scientific_impact` is anything other than
`none_expected` is never applied unattended. A NaN-loss fix that lowers the learning
rate changes optimisation, so it waits for you even though `learning_rate` is editable.

## Publication readiness

An eleven-point checklist over evidence actually collected: baseline comparison,
ablations, multiple seeds, confidence intervals, statistical tests, reproducibility,
leakage checks, robustness, limitations, novelty. Unknown criteria count as unmet.

Its summary always states that it does **not** predict acceptance at any venue. That is
what prevents "Q1-level" becoming an optimisation target: it is a description of
evidence, not a score to maximise.

## Integrity properties

- Failed and rejected experiments are preserved, never deleted
- A run that reports no metric never becomes the new best
- Metrics must be numeric; strings and booleans are dropped with a warning
- Every experiment records the commit, methodology version and mode it ran under
