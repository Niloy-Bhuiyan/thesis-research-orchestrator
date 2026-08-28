"""Local research daemon.

Owns the loop: heartbeat, Telegram polling, Kaggle run monitoring, and the
decision to retry, ask, or stop. Everything it learns is written to SQLite
before it acts, so a crash between two ticks loses at most the current tick,
never experiment lineage.

`tick` is deliberately synchronous and side-effect-ordered: observe, persist,
then notify. A notification never precedes the state change it describes.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .diagnosis.classifier import classify, summarize_for_human
from .kaggle.runner import KaggleRunner, QuotaUnavailable, parse_metrics
from .loop.budgets import Budgets, check_session
from .loop.optimizer import (
    ABANDON,
    ESCALATE_PROVIDER,
    EXTERNAL_RUN,
    REQUEST_APPROVAL,
    RETRY_AUTO,
    decide_next_action,
    is_improvement,
)
from .policy.engine import ResearchPolicy
from .state.db import Store, utcnow
from .telegram.bot import TelegramBot, Unauthorized, approval_keyboard

# Run statuses that mean the kernel is still executing on Kaggle's side.
ACTIVE = ("submitted", "running")


@dataclass
class TickReport:
    """What one iteration did. Returned so tests can assert on behaviour."""

    runs_checked: int = 0
    runs_finished: int = 0
    telegram_updates: int = 0
    actions: list[str] = None

    def __post_init__(self):
        if self.actions is None:
            self.actions = []


class Daemon:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        kaggle: KaggleRunner | None = None,
        bot: TelegramBot | None = None,
        policy: ResearchPolicy | None = None,
        router=None,
    ):
        self.settings = settings
        self.store = store
        self.kaggle = kaggle or KaggleRunner()
        self.bot = bot
        self.policy = policy
        self.router = router
        self.budgets = Budgets(**vars(settings.budgets))
        self._stop = False
        self._session_started = False
        if bot is not None:
            self._register_handlers()

    # ---------------- lifecycle ----------------

    def recover(self) -> list[str]:
        """Re-attach to runs that were in flight when the daemon last died.

        The Kaggle kernel kept running server-side, so these are recoverable
        rather than lost. Nothing is marked failed here: the next status poll
        decides, using real Kaggle state rather than an assumption.
        """
        orphans = self.store.find_orphaned_runs()
        if orphans:
            self.store.add_event(
                kind="daemon.recovered",
                message=f"re-attached {len(orphans)} in-flight run(s) after restart",
                level="warn",
            )
        return [row["id"] for row in orphans]

    def start_session(self) -> None:
        self.store.conn.execute(
            "UPDATE daemon_state SET status = 'running', pid = ?, session_started_at = ?,"
            " experiments_this_session = 0, consecutive_failures = 0,"
            " provider_calls_count = 0, pause_reason = NULL WHERE id = 1",
            (os.getpid(), utcnow()),
        )
        self._session_started = True
        self.store.add_event(kind="daemon.started", message=f"daemon up (pid {os.getpid()})")

    def stop(self, reason: str = "stopped by request") -> None:
        self._stop = True
        self.store.conn.execute(
            "UPDATE daemon_state SET status = 'stopped', pid = NULL WHERE id = 1"
        )
        self.store.add_event(kind="daemon.stopped", message=reason, level="warn")

    def notify(self, text: str, keyboard: dict | None = None) -> None:
        if self.bot is None:
            return
        try:
            self.bot.broadcast(text, keyboard)
        except Exception as exc:  # noqa: BLE001
            # A Telegram outage must never take the research loop down.
            self.store.add_event(
                kind="telegram.error", message=f"notify failed: {exc}", level="warn"
            )

    # ---------------- monitoring ----------------

    def check_runs(self, report: TickReport) -> None:
        placeholders = ", ".join("?" for _ in ACTIVE)
        rows = self.store.conn.execute(
            f"SELECT * FROM runs WHERE status IN ({placeholders})", ACTIVE
        ).fetchall()

        for run in rows:
            report.runs_checked += 1
            if not run["kernel_ref"]:
                continue
            try:
                status = self.kaggle.status(run["kernel_ref"])
            except QuotaUnavailable:
                self._handle_quota_loss(run, report)
                continue
            except Exception as exc:  # noqa: BLE001
                self.store.add_event(
                    kind="kaggle.error", message=str(exc)[:300], level="warn",
                    run_id=run["id"], experiment_id=run["experiment_id"],
                )
                continue

            if status == run["status"] or status == "unknown":
                continue

            if status in ACTIVE:
                self.store.transition_run(run["id"], status)
                continue

            self.store.transition_run(run["id"], status)
            report.runs_finished += 1
            if status == "complete":
                self._handle_success(run, report)
            else:
                self._handle_failure(run, report)

    def _experiment_of(self, run):
        return self.store.get_experiment(run["experiment_id"])

    def _handle_success(self, run, report: TickReport) -> None:
        exp = self._experiment_of(run)
        metrics_file = Path(run["output_path"] or "") / "metrics.json"
        value = None
        if metrics_file.is_file():
            metrics = parse_metrics(metrics_file)
            for name, number in metrics.items():
                self.store.record_metric(exp["id"], name, number, run_id=run["id"])
            if self.policy and self.policy.primary_metric_name in metrics:
                value = metrics[self.policy.primary_metric_name]
                self.store.set_primary_metric(
                    exp["id"], self.policy.primary_metric_name, value,
                    self.policy.primary_metric_direction,
                )

        self.store.transition_experiment(exp["id"], "completed")
        self.store.conn.execute(
            "UPDATE daemon_state SET consecutive_failures = 0,"
            " experiments_this_session = experiments_this_session + 1 WHERE id = 1"
        )

        best = self.store.best_experiment(exp["project_id"])
        direction = self.policy.primary_metric_direction if self.policy else "minimize"
        improved = best is not None and best["id"] == exp["id"] and value is not None
        report.actions.append("completed")
        self.notify(
            f"{exp['id']} completed.\n"
            f"{self.policy.primary_metric_name if self.policy else 'metric'}: "
            f"{value if value is not None else 'not reported'}\n"
            + ("New best." if improved else "No improvement over best.")
        )

    def _handle_failure(self, run, report: TickReport) -> None:
        exp = self._experiment_of(run)
        log = ""
        if run["log_path"] and Path(run["log_path"]).is_file():
            log = Path(run["log_path"]).read_text(encoding="utf-8", errors="replace")
        diagnosis = classify(log)

        self.store.conn.execute(
            "INSERT INTO diagnoses (id, experiment_id, run_id, failure_class, subclass,"
            " confidence, evidence, proposed_action, scientific_impact,"
            " requires_approval, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"DX-{run['id'][-8:]}-{exp['retry_count']}",
                exp["id"], run["id"], diagnosis.failure_class, diagnosis.subclass,
                diagnosis.confidence, diagnosis.evidence, diagnosis.proposed_action,
                diagnosis.scientific_impact, int(diagnosis.requires_approval), utcnow(),
            ),
        )
        self.store.transition_experiment(
            exp["id"], "failed", failure_class=diagnosis.failure_class
        )
        self.store.conn.execute(
            "UPDATE daemon_state SET consecutive_failures = consecutive_failures + 1"
            " WHERE id = 1"
        )

        if self.policy is None:
            report.actions.append("no_policy")
            self.notify(summarize_for_human(diagnosis, exp["id"], "unknown"))
            return

        project = self.store.get_project(exp["project_id"])
        decision = decide_next_action(
            diagnosis, self.policy, project["mode"], exp["retry_count"], self.budgets
        )
        report.actions.append(decision.action)
        self.store.add_event(
            kind=f"loop.{decision.action}", message=decision.reason,
            experiment_id=exp["id"], run_id=run["id"],
            level="warn" if decision.action == ABANDON else "info",
        )

        text = summarize_for_human(diagnosis, exp["id"], self._runtime(run))
        if decision.action == REQUEST_APPROVAL:
            proposal_id = f"PROP-{exp['id']}-{exp['retry_count']}"
            self.store.conn.execute(
                "INSERT INTO proposals (id, experiment_id, kind, summary, detail,"
                " target_field, scientific_impact, policy_verdict, requires_approval,"
                " status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,1,'pending',?,?)",
                (
                    proposal_id, exp["id"], diagnosis.subclass,
                    diagnosis.proposed_action, decision.reason, decision.target_field,
                    diagnosis.scientific_impact, decision.action, "classifier", utcnow(),
                ),
            )
            self.notify(f"{text}\n\n{decision.reason}", approval_keyboard(proposal_id))
        elif decision.notify:
            self.notify(f"{text}\n\n{decision.reason}")

    def _handle_quota_loss(self, run, report: TickReport) -> None:
        report.actions.append(EXTERNAL_RUN)
        self.store.add_event(
            kind="kaggle.quota_unavailable",
            message="GPU quota unavailable; external run required",
            level="warn", run_id=run["id"], experiment_id=run["experiment_id"],
        )
        self.notify(
            f"GPU quota unavailable.\n\nExperiment: {run['experiment_id']}\n"
            "Mode: external manual execution\n\n"
            "Run `researchos bundle` to produce the portable run package."
        )

    def _runtime(self, run) -> str:
        if not run["started_at"] or not run["ended_at"]:
            return "unknown"
        from datetime import datetime

        delta = datetime.fromisoformat(run["ended_at"]) - datetime.fromisoformat(
            run["started_at"]
        )
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        return f"{hours}h {remainder // 60:02d}m"

    # ---------------- telegram ----------------

    def _register_handlers(self) -> None:
        bot = self.bot
        bot.on("/start", lambda u: "ResearchOS online. /help for commands.")
        bot.on("/help", lambda u: __import__(
            "researchos.telegram.bot", fromlist=["HELP"]).HELP)
        bot.on("/status", lambda u: self.status_text())
        bot.on("/experiments", lambda u: self.experiments_text())
        bot.on("/providers", lambda u: self.providers_text())
        bot.on("/pause", lambda u: self._pause_cmd())
        bot.on("/resume", lambda u: self._resume_cmd())
        bot.on("/logs", lambda u: self.logs_text())
        bot.on("@approve", lambda u: self._decide(u.callback_data.split(":")[1], True))
        bot.on("@reject", lambda u: self._decide(u.callback_data.split(":")[1], False))
        bot.on("@logs", lambda u: self.logs_text())

    def _decide(self, proposal_id: str, approved: bool) -> str:
        row = self.store.conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            return f"{proposal_id} not found."
        if row["status"] != "pending":
            return f"{proposal_id} already {row['status']}."
        status = "approved" if approved else "rejected"
        self.store.conn.execute(
            "UPDATE proposals SET status = ?, decided_at = ?, decided_by = 'telegram'"
            " WHERE id = ?",
            (status, utcnow(), proposal_id),
        )
        self.store.add_event(
            kind=f"proposal.{status}", message=proposal_id,
            experiment_id=row["experiment_id"],
        )
        return f"{proposal_id} {status}."

    def _pause_cmd(self) -> str:
        self.store.pause("paused from Telegram")
        return "Loop paused."

    def _resume_cmd(self) -> str:
        self.store.conn.execute(
            "UPDATE daemon_state SET status = 'running', pause_reason = NULL WHERE id = 1"
        )
        self.store.add_event(kind="daemon.resumed", message="resumed from Telegram")
        return "Loop resumed."

    def status_text(self) -> str:
        state = self.store.daemon_state()
        lines = [
            f"daemon: {state['status']}",
            f"heartbeat: {state['last_heartbeat'] or 'never'}",
            f"active: {state['active_experiment_id'] or '-'}",
        ]
        if state["pause_reason"]:
            lines.append(f"paused: {state['pause_reason']}")
        project_id = self.settings.active_project
        if project_id:
            project = self.store.get_project(project_id)
            if project:
                lines.append(f"mode: {project['mode']}")
            best = self.store.best_experiment(project_id)
            if best:
                lines.append(
                    f"best: {best['id']} {best['primary_metric_name']}="
                    f"{best['primary_metric']}"
                )
        return "\n".join(lines)

    def experiments_text(self, limit: int = 10) -> str:
        project_id = self.settings.active_project
        if not project_id:
            return "No active project."
        rows = self.store.list_experiments(project_id)[-limit:]
        if not rows:
            return "No experiments yet."
        return "\n".join(
            f"{r['id']} {r['status']}"
            + (f" {r['primary_metric']}" if r["primary_metric"] is not None else "")
            for r in rows
        )

    def providers_text(self) -> str:
        if self.router is None:
            return "No provider router configured."
        return "\n".join(f"{name}: {status}"
                         for name, status in self.router.statuses().items())

    def logs_text(self, limit: int = 10) -> str:
        rows = self.store.recent_events(limit)
        return "\n".join(f"{r['level']}: {r['message']}" for r in reversed(rows)) or "No events."

    def poll_telegram(self, report: TickReport) -> None:
        if self.bot is None:
            return
        try:
            handled = self.bot.run_once(timeout=self.settings.telegram.poll_timeout_seconds)
        except Unauthorized:
            return
        except Exception as exc:  # noqa: BLE001
            self.store.add_event(
                kind="telegram.error", message=str(exc)[:300], level="warn"
            )
            return
        report.telegram_updates += len(handled)

    # ---------------- main loop ----------------

    def tick(self) -> TickReport:
        report = TickReport()
        self.store.heartbeat(os.getpid(), self.store.daemon_state()["active_experiment_id"])
        self.poll_telegram(report)

        state = self.store.daemon_state()
        if self._session_started and state["status"] == "stopped":
            # `researchos stop` runs in a different process and communicates
            # through the database, the only shared channel. Only honoured
            # once we have started, so the initial 'stopped' default does
            # not read as a stop request.
            self._stop = True
            return report
        if state["status"] == "paused":
            return report

        verdict = check_session(state, self.budgets)
        if not verdict:
            self.store.pause(verdict.reason)
            self.notify(f"Autonomous loop paused: {verdict.reason}")
            return report

        self.check_runs(report)
        return report

    def run(self, interval: int | None = None) -> None:
        interval = interval or self.settings.kaggle.poll_interval_seconds
        self.recover()
        self.start_session()
        self._install_signal_handlers()
        self.notify("ResearchOS daemon online.")
        try:
            while not self._stop:
                self.tick()
                for _ in range(interval):
                    if self._stop:
                        break
                    time.sleep(1)
        finally:
            self.stop("daemon exiting")

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):  # noqa: ARG001
            self._stop = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass  # not on the main thread, or unsupported on this platform
