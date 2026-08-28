import json

import pytest

from researchos.config import Settings
from researchos.daemon import Daemon
from researchos.kaggle.runner import KaggleRunner, QuotaUnavailable
from researchos.loop.optimizer import ABANDON, EXTERNAL_RUN, REQUEST_APPROVAL, RETRY_AUTO
from researchos.policy.engine import ResearchPolicy
from researchos.state.db import Store
from researchos.telegram.bot import TelegramBot

OWNER = 2088881866

POLICY_YAML = """
research_goal:
  primary_metric:
    name: E_AURC
    direction: minimize
agent_permissions:
  batch_size:
    policy: editable
  model_architecture:
    policy: approval_required
  data_path:
    policy: locked
"""

OOM_LOG = "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.20 GiB"
SHAPE_LOG = "RuntimeError: mat1 and mat2 shapes cannot be multiplied (32x768 and 512x256)"


class FakeKaggle(KaggleRunner):
    def __init__(self, statuses=None, raise_quota=False):
        super().__init__()
        self.statuses = statuses or {}
        self.raise_quota = raise_quota

    def status(self, ref):
        if self.raise_quota:
            raise QuotaUnavailable("You have exceeded your GPU quota")
        return self.statuses.get(ref, "running")


class FakeBot(TelegramBot):
    def __init__(self, queue=None):
        super().__init__("t", {OWNER})
        self.sent = []
        self.queue = list(queue or [])

    def _api(self, method, payload=None, timeout=60):
        if method == "getUpdates":
            batch, self.queue = self.queue, []
            return {"ok": True, "result": batch}
        if method == "sendMessage":
            self.sent.append(payload)
            return {"ok": True, "result": {"message_id": len(self.sent)}}
        return {"ok": True}


@pytest.fixture
def env(tmp_path):
    settings = Settings(workspace_root=str(tmp_path), active_project="thesis")
    store = Store(tmp_path / "state.sqlite3")
    store.create_project("thesis", "Thesis", str(tmp_path), mode="auto_exploration")
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(POLICY_YAML, encoding="utf-8")
    policy = ResearchPolicy.load(policy_file)
    yield settings, store, policy, tmp_path
    store.close()


def running_experiment(store, tmp_path, log=None, kernel_ref="niloybhuiyan/exp"):
    exp_id = store.create_experiment("thesis", hypothesis="h")
    for status in ("approved", "preparing", "queued", "running"):
        store.transition_experiment(exp_id, status)
    log_path = None
    if log is not None:
        log_path = tmp_path / f"{exp_id}.log"
        log_path.write_text(log, encoding="utf-8")
    run_id = store.create_run(exp_id, kernel_ref=kernel_ref,
                              log_path=str(log_path) if log_path else None)
    store.transition_run(run_id, "submitted")
    store.transition_run(run_id, "running")
    return exp_id, run_id


# ---------------- crash recovery ----------------


def test_recover_reattaches_in_flight_runs(env):
    settings, store, policy, tmp_path = env
    _, run_id = running_experiment(store, tmp_path)
    daemon = Daemon(settings, store, kaggle=FakeKaggle(), policy=policy)
    assert daemon.recover() == [run_id]


def test_recover_does_not_mark_runs_failed(env):
    """The kernel kept running on Kaggle; only a real poll may decide."""
    settings, store, policy, tmp_path = env
    _, run_id = running_experiment(store, tmp_path)
    Daemon(settings, store, kaggle=FakeKaggle(), policy=policy).recover()
    assert store.get_run(run_id)["status"] == "running"


def test_recover_logs_an_event(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path)
    Daemon(settings, store, kaggle=FakeKaggle(), policy=policy).recover()
    assert any(e["kind"] == "daemon.recovered" for e in store.recent_events())


def test_start_session_resets_budget_counters(env):
    settings, store, policy, _ = env
    store.conn.execute("UPDATE daemon_state SET consecutive_failures = 4 WHERE id = 1")
    Daemon(settings, store, kaggle=FakeKaggle(), policy=policy).start_session()
    assert store.daemon_state()["consecutive_failures"] == 0
    assert store.daemon_state()["status"] == "running"


# ---------------- run monitoring ----------------


def test_completed_run_records_metrics_and_completes_experiment(env):
    settings, store, policy, tmp_path = env
    exp_id, run_id = running_experiment(store, tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    (output / "metrics.json").write_text(json.dumps({"E_AURC": 0.31, "accuracy": 0.9}))
    store.conn.execute("UPDATE runs SET output_path = ? WHERE id = ?",
                       (str(output), run_id))

    daemon = Daemon(settings, store,
                    kaggle=FakeKaggle({"niloybhuiyan/exp": "complete"}), policy=policy)
    daemon.tick()

    exp = store.get_experiment(exp_id)
    assert exp["status"] == "completed"
    assert exp["primary_metric"] == 0.31
    assert {m["name"] for m in store.metrics_for(exp_id)} == {"E_AURC", "accuracy"}


def test_completion_without_metrics_file_does_not_invent_a_metric(env):
    settings, store, policy, tmp_path = env
    exp_id, _ = running_experiment(store, tmp_path)
    daemon = Daemon(settings, store,
                    kaggle=FakeKaggle({"niloybhuiyan/exp": "complete"}), policy=policy)
    daemon.tick()
    assert store.get_experiment(exp_id)["primary_metric"] is None


def test_oom_failure_auto_retries_in_auto_mode(env):
    settings, store, policy, tmp_path = env
    exp_id, _ = running_experiment(store, tmp_path, log=OOM_LOG)
    daemon = Daemon(settings, store,
                    kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}), policy=policy)
    report = daemon.tick()
    assert RETRY_AUTO in report.actions
    assert store.get_experiment(exp_id)["status"] == "failed"


def test_failure_writes_a_diagnosis_row(env):
    settings, store, policy, tmp_path = env
    exp_id, _ = running_experiment(store, tmp_path, log=OOM_LOG)
    Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}),
           policy=policy).tick()
    row = store.conn.execute("SELECT * FROM diagnoses WHERE experiment_id = ?",
                             (exp_id,)).fetchone()
    assert row["subclass"] == "gpu_oom"
    assert row["failure_class"] == "INFRASTRUCTURE"


def test_architecture_failure_creates_pending_proposal(env):
    settings, store, policy, tmp_path = env
    exp_id, _ = running_experiment(store, tmp_path, log=SHAPE_LOG)
    bot = FakeBot()
    daemon = Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}),
                    bot=bot, policy=policy)
    report = daemon.tick()
    assert REQUEST_APPROVAL in report.actions
    proposal = store.conn.execute("SELECT * FROM proposals").fetchone()
    assert proposal["status"] == "pending"
    assert proposal["target_field"] == "model_architecture"


def test_approval_request_sends_inline_buttons(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path, log=SHAPE_LOG)
    bot = FakeBot()
    Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}),
           bot=bot, policy=policy).tick()
    assert "reply_markup" in bot.sent[-1]
    assert "Approve" in bot.sent[-1]["reply_markup"]


def test_locked_field_failure_abandons(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path,
                       log="FileNotFoundError: no such file or directory: /data/x.csv")
    daemon = Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}),
                    policy=policy)
    assert ABANDON in daemon.tick().actions


def test_quota_loss_notifies_external_run(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path)
    bot = FakeBot()
    daemon = Daemon(settings, store, kaggle=FakeKaggle(raise_quota=True), bot=bot,
                    policy=policy)
    report = daemon.tick()
    assert EXTERNAL_RUN in report.actions
    assert "quota unavailable" in bot.sent[-1]["text"].lower()


def test_still_running_kernel_is_left_alone(env):
    settings, store, policy, tmp_path = env
    exp_id, _ = running_experiment(store, tmp_path)
    daemon = Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "running"}),
                    policy=policy)
    report = daemon.tick()
    assert report.runs_finished == 0
    assert store.get_experiment(exp_id)["status"] == "running"


# ---------------- budgets and pausing ----------------


def test_tick_pauses_when_consecutive_failure_budget_exhausted(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path)
    store.conn.execute("UPDATE daemon_state SET consecutive_failures = 99 WHERE id = 1")
    bot = FakeBot()
    daemon = Daemon(settings, store, kaggle=FakeKaggle(), bot=bot, policy=policy)
    daemon.tick()
    assert store.daemon_state()["status"] == "paused"
    assert "paused" in bot.sent[-1]["text"].lower()


def test_paused_daemon_does_not_poll_runs(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path)
    store.pause("manual")
    daemon = Daemon(settings, store,
                    kaggle=FakeKaggle({"niloybhuiyan/exp": "complete"}), policy=policy)
    report = daemon.tick()
    assert report.runs_checked == 0


# ---------------- telegram control ----------------


def test_status_command_reports_real_state(env):
    settings, store, policy, _ = env
    bot = FakeBot(queue=[{"update_id": 1, "message": {"chat": {"id": OWNER},
                                                      "text": "/status"}}])
    Daemon(settings, store, kaggle=FakeKaggle(), bot=bot, policy=policy).tick()
    assert "daemon:" in bot.sent[-1]["text"]


def test_pause_command_pauses_the_loop(env):
    settings, store, policy, _ = env
    bot = FakeBot(queue=[{"update_id": 1, "message": {"chat": {"id": OWNER},
                                                      "text": "/pause"}}])
    Daemon(settings, store, kaggle=FakeKaggle(), bot=bot, policy=policy).tick()
    assert store.daemon_state()["status"] == "paused"


def test_stranger_cannot_pause_the_loop(env):
    settings, store, policy, _ = env
    bot = FakeBot(queue=[{"update_id": 1, "message": {"chat": {"id": 4242},
                                                      "text": "/pause"}}])
    Daemon(settings, store, kaggle=FakeKaggle(), bot=bot, policy=policy).tick()
    assert store.daemon_state()["status"] != "paused"
    assert bot.sent == []


def test_approve_callback_marks_proposal_approved(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path, log=SHAPE_LOG)
    bot = FakeBot()
    daemon = Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}),
                    bot=bot, policy=policy)
    daemon.tick()
    proposal_id = store.conn.execute("SELECT id FROM proposals").fetchone()["id"]
    assert daemon._decide(proposal_id, True) == f"{proposal_id} approved."
    assert store.conn.execute("SELECT status FROM proposals").fetchone()["status"] == "approved"


def test_duplicate_approval_is_idempotent(env):
    settings, store, policy, tmp_path = env
    running_experiment(store, tmp_path, log=SHAPE_LOG)
    daemon = Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}),
                    bot=FakeBot(), policy=policy)
    daemon.tick()
    pid = store.conn.execute("SELECT id FROM proposals").fetchone()["id"]
    daemon._decide(pid, True)
    assert "already approved" in daemon._decide(pid, True)


def test_telegram_outage_does_not_stop_the_loop(env):
    """A notification failure must never take research down."""
    settings, store, policy, tmp_path = env
    exp_id, _ = running_experiment(store, tmp_path, log=OOM_LOG)

    class BrokenBot(FakeBot):
        def broadcast(self, text, keyboard=None):
            raise RuntimeError("telegram unreachable")

    daemon = Daemon(settings, store, kaggle=FakeKaggle({"niloybhuiyan/exp": "error"}),
                    bot=BrokenBot(), policy=policy)
    daemon.tick()  # must not raise
    assert store.get_experiment(exp_id)["status"] == "failed"
    assert any(e["kind"] == "telegram.error" for e in store.recent_events())


def test_heartbeat_is_written_every_tick(env):
    settings, store, policy, _ = env
    Daemon(settings, store, kaggle=FakeKaggle(), policy=policy).tick()
    assert store.daemon_state()["last_heartbeat"] is not None
