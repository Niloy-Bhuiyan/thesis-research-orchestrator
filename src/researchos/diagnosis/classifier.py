"""Failure classification for completed or crashed runs.

Pattern matching over logs produces a first-pass diagnosis cheaply and
deterministically, before any provider is spent on the problem. A provider is
only consulted for failures this cannot confidently name.

The safety rule this module enforces: a fix is auto-applicable only when it is
both high confidence and scientifically inert. Anything touching experimental
validity or methodology requires a human, regardless of confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Top-level failure classes, ordered most to least mechanical.
INFRASTRUCTURE = "INFRASTRUCTURE"
IMPLEMENTATION = "IMPLEMENTATION"
CONFIGURATION = "CONFIGURATION"
EXPERIMENTAL_VALIDITY = "EXPERIMENTAL_VALIDITY"
METHODOLOGY = "METHODOLOGY"

# Classes where no automated fix may ever be applied without a human.
NEVER_AUTOMATIC = frozenset({EXPERIMENTAL_VALIDITY, METHODOLOGY})

AUTO_APPLY_CONFIDENCE = 0.8

# (pattern, class, subclass, confidence, proposed_action, scientific_impact)
_RULES: list[tuple[str, str, str, float, str, str]] = [
    # -- infrastructure -------------------------------------------------
    (r"cuda out of memory|torch\.cuda\.OutOfMemoryError|out of memory",
     INFRASTRUCTURE, "gpu_oom", 0.95,
     "reduce batch size or enable gradient accumulation", "none_expected"),
    (r"cuda error: device-side assert",
     IMPLEMENTATION, "device_assert", 0.85,
     "inspect label range and indexing", "none_expected"),
    (r"your notebook tried to allocate more memory than is available|"
     r"kernel .*exceeded.*memory",
     INFRASTRUCTURE, "host_oom", 0.9,
     "reduce dataloader workers or prefetch", "none_expected"),
    (r"exceeded.*(?:time limit|maximum runtime)|execution timed out|"
     r"kernelworkerstatus\.timeout",
     INFRASTRUCTURE, "timeout", 0.9,
     "shorten the run or checkpoint and resume", "none_expected"),
    (r"no space left on device|disk quota exceeded",
     INFRASTRUCTURE, "disk_full", 0.9,
     "write fewer checkpoints or clean intermediate outputs", "none_expected"),
    (r"(?:connectionerror|failed to (?:download|fetch)|temporary failure in name resolution|"
     r"max retries exceeded)",
     INFRASTRUCTURE, "network", 0.8,
     "retry; enable internet on the kernel if it is off", "none_expected"),
    (r"could not find a version that satisfies|no matching distribution|"
     r"error: subprocess-exited-with-error",
     INFRASTRUCTURE, "dependency_install", 0.85,
     "pin the dependency to a version available on the runtime", "none_expected"),
    (r"modulenotfounderror|importerror",
     INFRASTRUCTURE, "missing_package", 0.85,
     "install the missing package in the kernel setup cell", "none_expected"),

    # -- implementation -------------------------------------------------
    (r"size mismatch|shapes? .*(?:are incompatible|must match)|"
     r"expected .*but got .*shape|mat1 and mat2 shapes cannot be multiplied",
     IMPLEMENTATION, "shape_mismatch", 0.9,
     "correct the tensor shapes at the reported layer", "none_expected"),
    (r"expected all tensors to be on the same device|"
     r"expected device cuda.* but got .*cpu|is on cpu, but expected .*cuda",
     IMPLEMENTATION, "device_mismatch", 0.9,
     "move the offending tensor to the training device", "none_expected"),
    (r"error loading state_dict|unexpected key\(s\) in state_dict|"
     r"missing key\(s\) in state_dict",
     IMPLEMENTATION, "checkpoint_mismatch", 0.85,
     "align the checkpoint with the current model definition", "none_expected"),
    (r"dataloader worker.*(?:killed|exited)|worker .*terminated",
     CONFIGURATION, "dataloader_workers", 0.8,
     "reduce num_workers", "none_expected"),
    (r"filenotfounderror|no such file or directory",
     CONFIGURATION, "bad_path", 0.8,
     "correct the dataset or checkpoint path", "none_expected"),
    (r"zerodivisionerror|nan.*loss|loss (?:became|is) nan",
     IMPLEMENTATION, "numerical_instability", 0.7,
     "lower the learning rate or add gradient clipping", "possible"),

    # -- configuration ---------------------------------------------------
    (r"(?:yaml|json)[\w.]*(?:parser|scanner|decode)\w*error|invalid configuration",
     CONFIGURATION, "invalid_config", 0.9,
     "fix the malformed config file", "none_expected"),

    # -- experimental validity (never auto-applied) ----------------------
    (r"test set|held.?out",
     EXPERIMENTAL_VALIDITY, "test_set_reference", 0.5,
     "human review: confirm the held-out set was not used for selection",
     "significant"),
    (r"data leakage|train.*overlap.*test|duplicate.*between splits",
     EXPERIMENTAL_VALIDITY, "leakage_risk", 0.7,
     "human review: verify split integrity", "significant"),
    (r"seed .*(?:not set|ignored)|non-?deterministic",
     EXPERIMENTAL_VALIDITY, "reproducibility", 0.6,
     "human review: pin seeds and deterministic flags", "moderate"),
]

_COMPILED = [
    (re.compile(pattern, re.IGNORECASE), *rest) for pattern, *rest in _RULES
]


@dataclass
class Diagnosis:
    failure_class: str
    subclass: str
    confidence: float
    evidence: str
    proposed_action: str
    scientific_impact: str

    @property
    def requires_approval(self) -> bool:
        """Approval is required unless the fix is confident and inert."""
        if self.failure_class in NEVER_AUTOMATIC:
            return True
        if self.scientific_impact != "none_expected":
            return True
        return self.confidence < AUTO_APPLY_CONFIDENCE

    @property
    def auto_applicable(self) -> bool:
        return not self.requires_approval


UNKNOWN = Diagnosis(
    failure_class=IMPLEMENTATION,
    subclass="unclassified",
    confidence=0.0,
    evidence="",
    proposed_action="escalate to a provider for analysis",
    scientific_impact="unknown",
)


def _evidence_line(log: str, match: re.Match) -> str:
    """The matched line, so a human sees why the classifier decided this."""
    start = log.rfind("\n", 0, match.start()) + 1
    end = log.find("\n", match.end())
    return log[start : end if end != -1 else len(log)].strip()[:300]


def classify(log: str) -> Diagnosis:
    """Best diagnosis for a log, or UNKNOWN when nothing matches.

    Rules are evaluated in order and the highest-confidence match wins, so a
    specific OOM beats a generic exception appearing later in the same log.
    """
    if not log or not log.strip():
        return UNKNOWN

    best: Diagnosis | None = None
    for pattern, cls, subclass, confidence, action, impact in _COMPILED:
        match = pattern.search(log)
        if not match:
            continue
        candidate = Diagnosis(
            failure_class=cls,
            subclass=subclass,
            confidence=confidence,
            evidence=_evidence_line(log, match),
            proposed_action=action,
            scientific_impact=impact,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    return best or UNKNOWN


def summarize_for_human(diagnosis: Diagnosis, experiment_id: str, runtime: str) -> str:
    """Telegram/dashboard text. States classification and impact plainly."""
    lines = [
        f"{experiment_id} failed after {runtime}.",
        "",
        f"Cause: {diagnosis.subclass.replace('_', ' ')} ({diagnosis.failure_class})",
    ]
    if diagnosis.evidence:
        lines.append(f"Evidence: {diagnosis.evidence}")
    lines += [
        f"Confidence: {diagnosis.confidence:.0%}",
        f"Proposed: {diagnosis.proposed_action}",
        f"Scientific impact: {diagnosis.scientific_impact}",
    ]
    lines.append(
        "Applied automatically." if diagnosis.auto_applicable else "Needs your approval."
    )
    return "\n".join(lines)
