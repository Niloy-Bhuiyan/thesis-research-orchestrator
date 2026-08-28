import json

import pytest

from researchos.state.db import Store, TransitionError


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "state.sqlite3") as s:
        s.create_project("thesis", "Thesis", str(tmp_path), methodology_version="v1")
        yield s


def test_experiment_ids_are_sequential_per_project(store):
    assert store.create_experiment("thesis") == "EXP-0001"
    assert store.create_experiment("thesis") == "EXP-0002"


def test_experiment_inherits_project_mode_and_methodology(store):
    store.set_mode("thesis", "auto_exploration")
    exp_id = store.create_experiment("thesis", hypothesis="lower LR helps")
    exp = store.get_experiment(exp_id)
    assert exp["mode"] == "auto_exploration"
    assert exp["methodology_version"] == "v1"
    assert exp["status"] == "proposed"


def test_legal_lifecycle_runs_to_completion(store):
    exp_id = store.create_experiment("thesis")
    for status in ("approved", "preparing", "queued", "running", "completed"):
        store.transition_experiment(exp_id, status)
    exp = store.get_experiment(exp_id)
    assert exp["status"] == "completed"
    assert exp["started_at"] is not None
    assert exp["ended_at"] is not None


def test_illegal_transition_is_rejected(store):
    exp_id = store.create_experiment("thesis")
    with pytest.raises(TransitionError):
        store.transition_experiment(exp_id, "running")  # proposed -> running skips stages


def test_terminal_state_cannot_be_left(store):
    exp_id = store.create_experiment("thesis")
    store.transition_experiment(exp_id, "rejected")
    with pytest.raises(TransitionError):
        store.transition_experiment(exp_id, "approved")


def test_failed_experiment_can_be_requeued_for_retry(store):
    exp_id = store.create_experiment("thesis")
    for status in ("approved", "preparing", "queued", "running", "failed"):
        store.transition_experiment(exp_id, status)
    store.transition_experiment(exp_id, "queued", retry_count=1)
    assert store.get_experiment(exp_id)["retry_count"] == 1


def test_rejected_experiments_are_preserved_not_deleted(store):
    exp_id = store.create_experiment("thesis", hypothesis="bad idea")
    store.transition_experiment(exp_id, "rejected")
    assert len(store.list_experiments("thesis")) == 1
    assert store.get_experiment(exp_id)["hypothesis"] == "bad idea"


def test_lineage_is_tracked(store):
    parent = store.create_experiment("thesis")
    child_a = store.create_experiment("thesis", parent_id=parent)
    child_b = store.create_experiment("thesis", parent_id=parent)
    assert {r["id"] for r in store.children_of(parent)} == {child_a, child_b}


def test_seeds_round_trip_as_json(store):
    exp_id = store.create_experiment("thesis", seeds=[11, 22, 33])
    assert json.loads(store.get_experiment(exp_id)["seeds"]) == [11, 22, 33]


def test_run_attempts_increment(store):
    exp_id = store.create_experiment("thesis")
    first = store.create_run(exp_id)
    second = store.create_run(exp_id)
    assert store.get_run(first)["attempt"] == 1
    assert store.get_run(second)["attempt"] == 2


def test_illegal_run_transition_is_rejected(store):
    exp_id = store.create_experiment("thesis")
    run_id = store.create_run(exp_id)
    with pytest.raises(TransitionError):
        store.transition_run(run_id, "complete")  # pending -> complete skips submission


def test_best_experiment_respects_minimize_direction(store):
    worse = store.create_experiment("thesis")
    better = store.create_experiment("thesis")
    for exp_id, value in ((worse, 0.42), (better, 0.31)):
        for status in ("approved", "preparing", "queued", "running", "completed"):
            store.transition_experiment(exp_id, status)
        store.set_primary_metric(exp_id, "E_AURC", value, "minimize")
    assert store.best_experiment("thesis")["id"] == better


def test_best_experiment_respects_maximize_direction(store):
    low = store.create_experiment("thesis")
    high = store.create_experiment("thesis")
    for exp_id, value in ((low, 0.71), (high, 0.88)):
        for status in ("approved", "preparing", "queued", "running", "completed"):
            store.transition_experiment(exp_id, status)
        store.set_primary_metric(exp_id, "accuracy", value, "maximize")
    assert store.best_experiment("thesis")["id"] == high


def test_state_survives_reopen(tmp_path):
    db = tmp_path / "state.sqlite3"
    with Store(db) as s:
        s.create_project("thesis", "Thesis", str(tmp_path))
        exp_id = s.create_experiment("thesis", hypothesis="persisted")
        s.transition_experiment(exp_id, "approved")

    with Store(db) as s:
        exp = s.get_experiment(exp_id)
        assert exp["status"] == "approved"
        assert exp["hypothesis"] == "persisted"


def test_crash_leaves_in_flight_runs_discoverable(tmp_path):
    """A daemon killed mid-run must be able to find what was still executing."""
    db = tmp_path / "state.sqlite3"
    with Store(db) as s:
        s.create_project("thesis", "Thesis", str(tmp_path))
        exp_id = s.create_experiment("thesis")
        run_id = s.create_run(exp_id, kernel_ref="niloybhuiyan/exp-0001")
        s.transition_run(run_id, "submitted")
        s.transition_run(run_id, "running")
        # process dies here: no clean shutdown, no ended_at written

    with Store(db) as s:
        orphans = s.find_orphaned_runs()
        assert [r["id"] for r in orphans] == [run_id]
        assert orphans[0]["kernel_ref"] == "niloybhuiyan/exp-0001"


def test_events_are_recorded_for_status_changes(store):
    exp_id = store.create_experiment("thesis")
    store.transition_experiment(exp_id, "approved")
    kinds = [e["kind"] for e in store.recent_events()]
    assert "experiment.status" in kinds
    assert "experiment.created" in kinds


def test_heartbeat_does_not_resurrect_a_paused_daemon(store):
    """A heartbeat that wrote status='running' would silently un-pause."""
    store.pause("budget exhausted")
    store.heartbeat(pid=1234)
    state = store.daemon_state()
    assert state["status"] == "paused"
    assert state["pause_reason"] == "budget exhausted"
    assert state["last_heartbeat"] is not None


def test_unknown_project_is_rejected(store):
    with pytest.raises(ValueError):
        store.create_experiment("does-not-exist")
