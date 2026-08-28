"""Scientific guard: what the agent may change, and when.

Two independent gates decide every proposed change:

1. the per-field permission in research_policy.yaml (editable / approval_required
   / locked), and
2. the project's operating mode.

Both must allow it. Mode can only ever be *more* restrictive than the field
permission, never less - locked_evaluation refuses scientific changes outright
even for fields marked editable.

The publication-readiness score here is descriptive. It is never an objective
the optimizer may target, and it never claims a venue will accept the work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Field permissions, in increasing order of restriction.
EDITABLE = "editable"
APPROVAL_REQUIRED = "approval_required"
LOCKED = "locked"

# Operating modes.
MANUAL = "manual"
AUTO = "auto_exploration"
LOCKED_EVAL = "locked_evaluation"

# Verdicts.
ALLOW = "allow"
NEEDS_APPROVAL = "needs_approval"
DENY = "deny"

# What kind of thing a proposal changes. Only INFRASTRUCTURE is considered
# scientifically inert, and even then only when the policy says so.
INFRASTRUCTURE = "infrastructure"
CONFIGURATION = "configuration"
IMPLEMENTATION = "implementation"
METHODOLOGY = "methodology"

# Constraints that no mode and no permission may override.
INVIOLABLE = {
    "held_out_test_set": "never_optimize_on",
    "data_leakage": "forbidden",
}


class PolicyError(Exception):
    pass


@dataclass
class Verdict:
    decision: str
    reason: str
    field_policy: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision in (ALLOW, NEEDS_APPROVAL)

    @property
    def requires_approval(self) -> bool:
        return self.decision == NEEDS_APPROVAL


@dataclass
class ResearchPolicy:
    primary_metric_name: str
    primary_metric_direction: str
    publication_target: str | None = None
    permissions: dict[str, str] = field(default_factory=dict)
    constraints: dict[str, str] = field(default_factory=dict)
    editable_files: list[str] = field(default_factory=list)
    locked_files: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "ResearchPolicy":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        goal = data.get("research_goal") or {}
        metric = goal.get("primary_metric") or {}
        if not metric.get("name"):
            raise PolicyError("research_goal.primary_metric.name is required")
        direction = metric.get("direction")
        if direction not in ("minimize", "maximize"):
            raise PolicyError("primary_metric.direction must be minimize or maximize")

        permissions = {}
        for name, spec in (data.get("agent_permissions") or {}).items():
            policy = spec.get("policy") if isinstance(spec, dict) else spec
            if policy not in (EDITABLE, APPROVAL_REQUIRED, LOCKED):
                raise PolicyError(f"{name}: unknown permission {policy!r}")
            permissions[name] = policy

        files = data.get("files") or {}
        return cls(
            primary_metric_name=metric["name"],
            primary_metric_direction=direction,
            publication_target=goal.get("publication_target"),
            permissions=permissions,
            constraints=data.get("scientific_constraints") or {},
            editable_files=files.get("editable") or [],
            locked_files=files.get("locked") or [],
        )

    def permission_for(self, field_name: str) -> str:
        """Unknown fields are treated as approval_required, not as editable.

        Defaulting an unrecognised knob to editable would let the agent change
        things nobody thought to classify.
        """
        return self.permissions.get(field_name, APPROVAL_REQUIRED)


def evaluate(
    policy: ResearchPolicy,
    mode: str,
    field_name: str,
    change_kind: str = CONFIGURATION,
) -> Verdict:
    """Decide whether a proposed change may proceed."""
    if mode not in (MANUAL, AUTO, LOCKED_EVAL):
        raise PolicyError(f"unknown mode: {mode}")

    if field_name in INVIOLABLE:
        return Verdict(DENY, f"{field_name} is inviolable ({INVIOLABLE[field_name]})", LOCKED)

    permission = policy.permission_for(field_name)

    if permission == LOCKED:
        return Verdict(DENY, f"{field_name} is locked by research policy", LOCKED)

    if mode == LOCKED_EVAL:
        # Only provably inert infrastructure recovery is permitted here.
        if change_kind != INFRASTRUCTURE:
            return Verdict(
                DENY,
                f"locked evaluation forbids {change_kind} changes; only infrastructure "
                "recovery that cannot alter scientific meaning is allowed",
                permission,
            )
        return Verdict(NEEDS_APPROVAL, "infrastructure recovery during locked evaluation",
                       permission)

    if change_kind == METHODOLOGY:
        return Verdict(NEEDS_APPROVAL, "methodology changes always require approval",
                       permission)

    if permission == APPROVAL_REQUIRED:
        return Verdict(NEEDS_APPROVAL, f"{field_name} requires approval", permission)

    # permission == EDITABLE
    if mode == MANUAL:
        return Verdict(NEEDS_APPROVAL, "manual mode requires approval for any change",
                       permission)
    return Verdict(ALLOW, f"{field_name} is editable in auto exploration", permission)


def file_is_editable(policy: ResearchPolicy, path: str) -> bool:
    """Locked file patterns win over editable ones."""
    from fnmatch import fnmatch

    if any(fnmatch(path, pattern) for pattern in policy.locked_files):
        return False
    if not policy.editable_files:
        return False
    return any(fnmatch(path, pattern) for pattern in policy.editable_files)


# ---------------- publication readiness (descriptive only) ----------------

READINESS_CHECKS = (
    "primary_metric_improved",
    "baseline_comparison",
    "ablation_coverage",
    "multiple_seeds",
    "confidence_intervals",
    "statistical_test",
    "reproducibility",
    "leakage_check",
    "robustness_check",
    "limitations_documented",
    "literature_novelty_checked",
)


@dataclass
class ReadinessReport:
    satisfied: dict[str, bool]

    @property
    def score(self) -> float:
        return sum(self.satisfied.values()) / len(READINESS_CHECKS)

    @property
    def missing(self) -> list[str]:
        return [c for c in READINESS_CHECKS if not self.satisfied.get(c)]

    def summary(self) -> str:
        pct = round(self.score * 100)
        return (
            f"Publication readiness: {pct}% of tracked criteria met. "
            "This is a checklist over evidence collected so far, not a "
            "prediction of acceptance at any venue."
        )


def assess_readiness(evidence: dict[str, bool]) -> ReadinessReport:
    """Score only what evidence proves. Unknown criteria count as unmet."""
    return ReadinessReport({c: bool(evidence.get(c, False)) for c in READINESS_CHECKS})
