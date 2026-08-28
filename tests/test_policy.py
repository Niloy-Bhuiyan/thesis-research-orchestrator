import pytest

from researchos.policy.engine import (
    ALLOW,
    APPROVAL_REQUIRED,
    AUTO,
    CONFIGURATION,
    DENY,
    IMPLEMENTATION,
    INFRASTRUCTURE,
    LOCKED_EVAL,
    MANUAL,
    METHODOLOGY,
    NEEDS_APPROVAL,
    PolicyError,
    ResearchPolicy,
    assess_readiness,
    evaluate,
    file_is_editable,
)

POLICY_YAML = """
research_goal:
  publication_target: "Q1-candidate"
  primary_metric:
    name: "E_AURC"
    direction: "minimize"

scientific_constraints:
  dataset_split: immutable
  held_out_test_set: never_optimize_on
  data_leakage: forbidden
  reproducibility: required
  multiple_seeds: required

agent_permissions:
  batch_size:
    policy: editable
  learning_rate:
    policy: editable
  optimizer:
    policy: approval_required
  loss_function:
    policy: approval_required
  model_architecture:
    policy: approval_required
  dataset_split:
    policy: locked
  evaluation_metric:
    policy: locked

files:
  editable:
    - "configs/experiments/*.yaml"
    - "src/train.py"
  locked:
    - "src/evaluate.py"
    - "configs/experiments/final_*.yaml"
"""


@pytest.fixture
def policy(tmp_path):
    p = tmp_path / "research_policy.yaml"
    p.write_text(POLICY_YAML, encoding="utf-8")
    return ResearchPolicy.load(p)


# ---------------- loading ----------------


def test_policy_loads_metric_and_direction(policy):
    assert policy.primary_metric_name == "E_AURC"
    assert policy.primary_metric_direction == "minimize"


def test_missing_metric_name_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("research_goal:\n  primary_metric:\n    direction: minimize\n")
    with pytest.raises(PolicyError):
        ResearchPolicy.load(p)


def test_bad_direction_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "research_goal:\n  primary_metric:\n    name: x\n    direction: sideways\n"
    )
    with pytest.raises(PolicyError):
        ResearchPolicy.load(p)


def test_unknown_permission_value_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "research_goal:\n  primary_metric:\n    name: x\n    direction: minimize\n"
        "agent_permissions:\n  batch_size:\n    policy: whatever\n"
    )
    with pytest.raises(PolicyError):
        ResearchPolicy.load(p)


def test_unclassified_field_defaults_to_approval_not_editable(policy):
    """A knob nobody classified must not be silently free to change."""
    assert policy.permission_for("mystery_knob") == APPROVAL_REQUIRED


# ---------------- locked fields ----------------


def test_locked_field_denied_in_every_mode(policy):
    for mode in (MANUAL, AUTO, LOCKED_EVAL):
        assert evaluate(policy, mode, "dataset_split").decision == DENY


def test_evaluation_metric_is_locked(policy):
    assert evaluate(policy, AUTO, "evaluation_metric").decision == DENY


def test_held_out_test_set_is_inviolable_even_if_policy_says_editable(tmp_path):
    p = tmp_path / "loose.yaml"
    p.write_text(
        "research_goal:\n  primary_metric:\n    name: x\n    direction: minimize\n"
        "agent_permissions:\n  held_out_test_set:\n    policy: editable\n"
    )
    loose = ResearchPolicy.load(p)
    verdict = evaluate(loose, AUTO, "held_out_test_set")
    assert verdict.decision == DENY
    assert "inviolable" in verdict.reason


def test_data_leakage_is_inviolable(policy):
    assert evaluate(policy, AUTO, "data_leakage").decision == DENY


# ---------------- mode interaction ----------------


def test_editable_field_is_free_in_auto_mode(policy):
    assert evaluate(policy, AUTO, "batch_size").decision == ALLOW


def test_editable_field_still_needs_approval_in_manual_mode(policy):
    verdict = evaluate(policy, MANUAL, "batch_size")
    assert verdict.decision == NEEDS_APPROVAL
    assert verdict.requires_approval


def test_approval_field_needs_approval_even_in_auto(policy):
    assert evaluate(policy, AUTO, "optimizer").decision == NEEDS_APPROVAL


def test_methodology_change_always_needs_approval(policy):
    verdict = evaluate(policy, AUTO, "batch_size", change_kind=METHODOLOGY)
    assert verdict.decision == NEEDS_APPROVAL


# ---------------- locked evaluation mode ----------------


def test_locked_eval_blocks_configuration_change(policy):
    verdict = evaluate(policy, LOCKED_EVAL, "batch_size", change_kind=CONFIGURATION)
    assert verdict.decision == DENY
    assert "locked evaluation" in verdict.reason


def test_locked_eval_blocks_implementation_change(policy):
    assert evaluate(policy, LOCKED_EVAL, "batch_size",
                    change_kind=IMPLEMENTATION).decision == DENY


def test_locked_eval_allows_infrastructure_recovery_with_approval(policy):
    verdict = evaluate(policy, LOCKED_EVAL, "batch_size", change_kind=INFRASTRUCTURE)
    assert verdict.decision == NEEDS_APPROVAL


def test_locked_eval_still_denies_locked_field_infrastructure(policy):
    assert evaluate(policy, LOCKED_EVAL, "dataset_split",
                    change_kind=INFRASTRUCTURE).decision == DENY


def test_unknown_mode_is_rejected(policy):
    with pytest.raises(PolicyError):
        evaluate(policy, "cowboy_mode", "batch_size")


# ---------------- file scoping ----------------


def test_editable_file_matches_pattern(policy):
    assert file_is_editable(policy, "configs/experiments/lr_sweep.yaml")
    assert file_is_editable(policy, "src/train.py")


def test_locked_file_wins_over_editable_pattern(policy):
    """final_*.yaml sits inside an editable directory but must stay locked."""
    assert not file_is_editable(policy, "configs/experiments/final_run.yaml")


def test_unlisted_file_is_not_editable(policy):
    assert not file_is_editable(policy, "src/data.py")
    assert not file_is_editable(policy, "src/evaluate.py")


# ---------------- publication readiness ----------------


def test_readiness_counts_only_proven_evidence():
    report = assess_readiness({"multiple_seeds": True, "baseline_comparison": True})
    assert report.satisfied["multiple_seeds"]
    assert not report.satisfied["statistical_test"]
    assert 0 < report.score < 1


def test_readiness_missing_lists_unmet_criteria():
    report = assess_readiness({"multiple_seeds": True})
    assert "statistical_test" in report.missing
    assert "multiple_seeds" not in report.missing


def test_readiness_never_claims_acceptance():
    summary = assess_readiness({c: True for c in ("multiple_seeds",)}).summary()
    assert "not a prediction of acceptance" in summary


def test_full_evidence_scores_one():
    from researchos.policy.engine import READINESS_CHECKS

    assert assess_readiness({c: True for c in READINESS_CHECKS}).score == 1.0
