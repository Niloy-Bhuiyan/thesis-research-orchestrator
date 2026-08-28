"""Experiment optimizer: what the system does after a run ends.

The decision itself is a pure function of (diagnosis, policy, mode, budget).
Keeping it free of I/O is what makes the 3am behaviour testable: every branch
below can be exercised without Kaggle, a provider, or a network.

Architecturally this follows the observe -> diagnose -> hypothesise -> validate
-> apply -> run -> evaluate -> keep/revert loop, generalised so the training
and evaluation commands come from a per-project adapter rather than being
hardcoded to one model family.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..diagnosis.classifier import Diagnosis
from ..policy.engine import (
    DENY,
    NEEDS_APPROVAL,
    ResearchPolicy,
    evaluate,
)
from ..policy.engine import (
    INFRASTRUCTURE as POLICY_INFRASTRUCTURE,
)
from .budgets import Budgets, check_retry

# What the loop does next.
RETRY_AUTO = "retry_auto"
REQUEST_APPROVAL = "request_approval"
ESCALATE_PROVIDER = "escalate_provider"
EXTERNAL_RUN = "external_run"
ABANDON = "abandon"

# Diagnosis subclass -> the policy field its fix would touch.
FIX_TARGETS = {
    "gpu_oom": "batch_size",
    "host_oom": "num_workers",
    "dataloader_workers": "num_workers",
    "timeout": "max_steps",
    "missing_package": "dependencies",
    "dependency_install": "dependencies",
    "network": "dependencies",
    "disk_full": "checkpoint_frequency",
    "bad_path": "data_path",
    "shape_mismatch": "model_architecture",
    "device_mismatch": "device_placement",
    "checkpoint_mismatch": "checkpoint_frequency",
    "numerical_instability": "learning_rate",
    "device_assert": "model_architecture",
    "invalid_config": "config_file",
}


@dataclass
class Decision:
    action: str
    reason: str
    target_field: str | None = None
    requires_approval: bool = False
    notify: bool = True
    details: dict = field(default_factory=dict)


def decide_next_action(
    diagnosis: Diagnosis,
    policy: ResearchPolicy,
    mode: str,
    retry_count: int,
    budgets: Budgets,
    quota_available: bool = True,
) -> Decision:
    """Choose the next action after a failed run.

    Order matters. Budget exhaustion is checked before anything else so a
    runaway loop cannot argue its way past the limit, and quota loss is checked
    before any retry because retrying without GPU cannot succeed.
    """
    budget = check_retry(retry_count, budgets)
    if not budget:
        return Decision(ABANDON, budget.reason, notify=True)

    if not quota_available:
        return Decision(
            EXTERNAL_RUN,
            "GPU quota unavailable; packaging an external run bundle",
            notify=True,
        )

    if diagnosis.confidence == 0.0:
        return Decision(
            ESCALATE_PROVIDER,
            "failure not recognised; asking a provider to analyse the log",
            notify=False,
        )

    target = FIX_TARGETS.get(diagnosis.subclass)
    if target is None:
        return Decision(
            ESCALATE_PROVIDER,
            f"no known fix mapping for {diagnosis.subclass}",
            notify=False,
        )

    # A scientifically inert infrastructure fix is judged as infrastructure;
    # anything else is judged as a configuration change and gets less latitude.
    change_kind = (
        POLICY_INFRASTRUCTURE
        if diagnosis.scientific_impact == "none_expected"
        else "configuration"
    )
    verdict = evaluate(policy, mode, target, change_kind=change_kind)

    if verdict.decision == DENY:
        return Decision(
            ABANDON, f"policy denies the only known fix: {verdict.reason}",
            target_field=target, notify=True,
        )

    # The classifier and the policy must BOTH be satisfied for an unattended
    # fix. Either one objecting sends it to a human.
    if verdict.decision == NEEDS_APPROVAL or diagnosis.requires_approval:
        reason = (
            verdict.reason
            if verdict.decision == NEEDS_APPROVAL
            else f"{diagnosis.subclass} is not safe to apply unattended"
        )
        return Decision(
            REQUEST_APPROVAL, reason, target_field=target, requires_approval=True,
            notify=True,
        )

    return Decision(
        RETRY_AUTO,
        f"{diagnosis.proposed_action} ({diagnosis.confidence:.0%} confidence)",
        target_field=target,
        notify=True,
    )


def is_improvement(new: float | None, best: float | None, direction: str) -> bool:
    """Whether a result beats the incumbent. Missing metrics never win."""
    if new is None:
        return False
    if best is None:
        return True
    return new < best if direction == "minimize" else new > best


def keep_or_revert(new: float | None, best: float | None, direction: str) -> str:
    return "keep" if is_improvement(new, best, direction) else "revert"
