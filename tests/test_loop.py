from datetime import datetime, timedelta, timezone

import pytest

from researchos.diagnosis.classifier import classify
from researchos.loop.budgets import (
    Budgets,
    check_retry,
    check_session,
    cooldown_remaining,
)
from researchos.loop.optimizer import (
    ABANDON,
    ESCALATE_PROVIDER,
    EXTERNAL_RUN,
    REQUEST_APPROVAL,
    RETRY_AUTO,
    decide_next_action,
    is_improvement,
    keep_or_revert,
)
from researchos.policy.engine import AUTO, LOCKED_EVAL, MANUAL, ResearchPolicy
from researchos.state.db import Store

POLICY_YAML = """
research_goal:
  primary_metric:
    name: E_AURC
    direction: minimize
agent_permissions:
  batch_size:
    policy: editable
  num_workers:
    policy: editable
  dependencies:
    policy: editable
  learning_rate:
    policy: editable
  model_architecture:
    policy: approval_required
  data_path:
    policy: locked
"""


@pytest.fixture
def policy(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY_YAML, encoding="utf-8")
    return ResearchPolicy.load(p)


OOM = classify("torch.cuda.OutOfMemoryError: CUDA out of memory")
SHAPE = classify("RuntimeError: mat1 and mat2 shapes cannot be multiplied (32x768 and 512x256)")
NAN = classify("RuntimeError: loss became nan at step 400")
MYSTERY = classify("Segmentation fault (core dumped)")


# ---------------- budgets ----------------


def test_retry_allowed_under_budget():
    assert check_retry(1, Budgets(max_retries_per_run=3))


def test_retry_blocked_at_budget():
    verdict = check_retry(3, Budgets(max_retries_per_run=3))
    assert not verdict
    assert "retry budget exhausted" in verdict.reason


def make_state(**kw):
    base = {
        "consecutive_failures": 0,
        "experiments_this_session": 0,
        "provider_calls_count": 0,
        "session_started_at": None,
    }
    base.update(kw)
    return base


def test_session_ok_when_fresh():
    assert check_session(make_state(), Budgets())


def test_consecutive_failures_stop_the_loop():
    verdict = check_session(make_state(consecutive_failures=5), Budgets())
    assert not verdict
    assert "consecutive failures" in verdict.reason


def test_experiment_count_limit_stops_the_loop():
    verdict = check_session(
        make_state(experiments_this_session=20), Budgets(max_experiments_per_session=20)
    )
    assert not verdict


def test_provider_call_budget_stops_the_loop():
    verdict = check_session(
        make_state(provider_calls_count=200), Budgets(max_provider_calls=200)
    )
    assert not verdict
    assert "provider call budget" in verdict.reason


def test_wall_clock_limit_stops_the_loop():
    started = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    verdict = check_session(
        make_state(session_started_at=started), Budgets(max_session_hours=12)
    )
    assert not verdict
    assert "wall clock" in verdict.reason


def test_budgets_are_read_from_persisted_state_so_restart_gives_no_fresh_allowance(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.conn.execute("UPDATE daemon_state SET consecutive_failures = 5 WHERE id = 1")
    store.close()

    store = Store(tmp_path / "s.sqlite3")  # simulate daemon restart
    assert not check_session(store.daemon_state(), Budgets(max_consecutive_failures=5))
    store.close()


def test_cooldown_counts_down():
    just_failed = datetime.now(timezone.utc).isoformat()
    remaining = cooldown_remaining(just_failed, Budgets(cooldown_minutes_after_failures=15))
    assert timedelta(minutes=14) < remaining <= timedelta(minutes=15)


def test_cooldown_zero_when_expired():
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert cooldown_remaining(old, Budgets()) == timedelta(0)


def test_cooldown_zero_when_never_failed():
    assert cooldown_remaining(None, Budgets()) == timedelta(0)


# ---------------- decision: the 3am behaviour ----------------


def test_oom_auto_retries_in_auto_mode(policy):
    d = decide_next_action(OOM, policy, AUTO, retry_count=0, budgets=Budgets())
    assert d.action == RETRY_AUTO
    assert d.target_field == "batch_size"


def test_oom_needs_approval_in_manual_mode(policy):
    d = decide_next_action(OOM, policy, MANUAL, retry_count=0, budgets=Budgets())
    assert d.action == REQUEST_APPROVAL


def test_oom_blocked_entirely_in_locked_evaluation_is_still_recoverable(policy):
    """Infra recovery is permitted in locked eval, but only with approval."""
    d = decide_next_action(OOM, policy, LOCKED_EVAL, retry_count=0, budgets=Budgets())
    assert d.action == REQUEST_APPROVAL


def test_shape_mismatch_needs_approval_since_architecture_is_gated(policy):
    d = decide_next_action(SHAPE, policy, AUTO, retry_count=0, budgets=Budgets())
    assert d.action == REQUEST_APPROVAL
    assert d.target_field == "model_architecture"


def test_nan_loss_never_auto_applies_even_though_lr_is_editable(policy):
    """The fix changes optimisation, so the classifier vetoes unattended use."""
    d = decide_next_action(NAN, policy, AUTO, retry_count=0, budgets=Budgets())
    assert d.action == REQUEST_APPROVAL


def test_unrecognised_failure_escalates_to_provider(policy):
    d = decide_next_action(MYSTERY, policy, AUTO, retry_count=0, budgets=Budgets())
    assert d.action == ESCALATE_PROVIDER
    assert not d.notify  # routine, not worth waking the human


def test_locked_target_field_abandons_rather_than_asking(policy):
    bad_path = classify("FileNotFoundError: no such file or directory: /data/train.csv")
    d = decide_next_action(bad_path, policy, AUTO, retry_count=0, budgets=Budgets())
    assert d.action == ABANDON
    assert "policy denies" in d.reason


def test_retry_budget_exhaustion_beats_everything(policy):
    d = decide_next_action(OOM, policy, AUTO, retry_count=3, budgets=Budgets(max_retries_per_run=3))
    assert d.action == ABANDON
    assert "retry budget" in d.reason


def test_quota_loss_routes_to_external_run_not_retry(policy):
    d = decide_next_action(
        OOM, policy, AUTO, retry_count=0, budgets=Budgets(), quota_available=False
    )
    assert d.action == EXTERNAL_RUN


def test_budget_check_precedes_quota_check(policy):
    """An exhausted budget must not produce a pointless external bundle."""
    d = decide_next_action(
        OOM, policy, AUTO, retry_count=9, budgets=Budgets(max_retries_per_run=3),
        quota_available=False,
    )
    assert d.action == ABANDON


# ---------------- keep / revert ----------------


def test_lower_is_better_when_minimizing():
    assert is_improvement(0.31, 0.42, "minimize")
    assert not is_improvement(0.51, 0.42, "minimize")


def test_higher_is_better_when_maximizing():
    assert is_improvement(0.88, 0.71, "maximize")
    assert not is_improvement(0.65, 0.71, "maximize")


def test_first_result_is_an_improvement():
    assert is_improvement(0.5, None, "minimize")


def test_missing_metric_never_counts_as_improvement():
    """A crashed run must not be recorded as the new best."""
    assert not is_improvement(None, 0.42, "minimize")
    assert keep_or_revert(None, 0.42, "minimize") == "revert"


def test_keep_or_revert_wording():
    assert keep_or_revert(0.1, 0.2, "minimize") == "keep"
    assert keep_or_revert(0.3, 0.2, "minimize") == "revert"
