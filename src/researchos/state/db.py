"""Persistent state engine.

SQLite is authoritative. Every status change goes through a transition table so
an experiment can never be silently moved into a nonsensical state, and so a
crashed daemon can tell on restart which runs were left in flight.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..redaction import redact

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1

# status -> statuses reachable from it. Terminal states map to an empty set.
EXPERIMENT_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"approved", "rejected", "cancelled"},
    "approved": {"preparing", "cancelled"},
    "preparing": {"queued", "failed", "cancelled"},
    "queued": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "cancelled", "paused", "imported"},
    "paused": {"running", "queued", "cancelled"},
    # A failed experiment may be retried, which sends it back to the queue.
    "failed": {"queued", "rejected", "cancelled"},
    "imported": {"completed", "failed"},
    "completed": set(),
    "rejected": set(),
    "cancelled": set(),
}

RUN_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"submitted", "error", "cancelled"},
    "submitted": {"running", "error", "cancelled", "timeout"},
    "running": {"complete", "error", "cancelled", "timeout"},
    "complete": set(),
    "error": set(),
    "cancelled": set(),
    "timeout": set(),
}

# Runs in these states when the daemon died were still in flight.
IN_FLIGHT_RUN_STATUSES = ("submitted", "running")


class TransitionError(Exception):
    """Raised when a caller asks for a status change the lifecycle forbids."""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        if row["v"] is None:
            self.conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utcnow()),
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO daemon_state (id, status) VALUES (1, 'stopped')"
        )
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        SQLite has no ADD COLUMN IF NOT EXISTS, and CREATE TABLE IF NOT EXISTS
        silently skips an existing table, so new columns need adding by hand.
        """
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(daemon_state)").fetchall()
        }
        if "last_synced_event_id" not in existing:
            self.conn.execute(
                "ALTER TABLE daemon_state ADD COLUMN last_synced_event_id"
                " INTEGER NOT NULL DEFAULT 0"
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------- projects ----------------

    def create_project(
        self,
        project_id: str,
        name: str,
        root_path: str,
        mode: str = "manual",
        policy_path: str | None = None,
        methodology_version: str | None = None,
    ) -> str:
        now = utcnow()
        self.conn.execute(
            "INSERT INTO projects (id, name, root_path, mode, policy_path,"
            " methodology_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, name, root_path, mode, policy_path, methodology_version, now, now),
        )
        self.add_event(kind="project.created", message=f"Project {name} created",
                       project_id=project_id)
        return project_id

    def get_project(self, project_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()

    def set_mode(self, project_id: str, mode: str) -> None:
        if mode not in ("manual", "auto_exploration", "locked_evaluation"):
            raise ValueError(f"unknown mode: {mode}")
        self.conn.execute(
            "UPDATE projects SET mode = ?, updated_at = ? WHERE id = ?",
            (mode, utcnow(), project_id),
        )
        self.add_event(kind="project.mode_changed", message=f"Mode set to {mode}",
                       project_id=project_id)

    # ---------------- experiments ----------------

    def next_experiment_id(self, project_id: str) -> str:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM experiments WHERE project_id = ?", (project_id,)
        ).fetchone()
        return f"EXP-{row['n'] + 1:04d}"

    def create_experiment(
        self,
        project_id: str,
        hypothesis: str | None = None,
        parent_id: str | None = None,
        experiment_id: str | None = None,
        **fields,
    ) -> str:
        project = self.get_project(project_id)
        if project is None:
            raise ValueError(f"unknown project: {project_id}")
        exp_id = experiment_id or self.next_experiment_id(project_id)
        cols = {
            "id": exp_id,
            "project_id": project_id,
            "parent_id": parent_id,
            "hypothesis": hypothesis,
            "status": "proposed",
            "mode": project["mode"],
            "methodology_version": project["methodology_version"],
            "created_at": utcnow(),
        }
        cols.update(fields)
        if isinstance(cols.get("seeds"), (list, tuple)):
            cols["seeds"] = json.dumps(list(cols["seeds"]))
        placeholders = ", ".join("?" for _ in cols)
        self.conn.execute(
            f"INSERT INTO experiments ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(cols.values()),
        )
        self.add_event(kind="experiment.created", message=f"{exp_id} proposed",
                       project_id=project_id, experiment_id=exp_id)
        return exp_id

    def get_experiment(self, experiment_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()

    def transition_experiment(self, experiment_id: str, to_status: str, **fields) -> None:
        exp = self.get_experiment(experiment_id)
        if exp is None:
            raise ValueError(f"unknown experiment: {experiment_id}")
        current = exp["status"]
        allowed = EXPERIMENT_TRANSITIONS.get(current, set())
        if to_status not in allowed:
            raise TransitionError(
                f"{experiment_id}: cannot move {current} -> {to_status}"
                f" (allowed: {sorted(allowed) or 'none, terminal state'})"
            )
        updates = {"status": to_status, **fields}
        if to_status == "running" and exp["started_at"] is None:
            updates["started_at"] = utcnow()
        if to_status in ("completed", "failed", "cancelled", "rejected"):
            updates["ended_at"] = utcnow()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE experiments SET {assignments} WHERE id = ?",
            (*updates.values(), experiment_id),
        )
        self.add_event(
            kind="experiment.status",
            message=f"{experiment_id}: {current} -> {to_status}",
            project_id=exp["project_id"],
            experiment_id=experiment_id,
            level="error" if to_status == "failed" else "info",
        )

    def list_experiments(self, project_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM experiments WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()

    def children_of(self, experiment_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM experiments WHERE parent_id = ? ORDER BY created_at",
            (experiment_id,),
        ).fetchall()

    # ---------------- runs ----------------

    def create_run(self, experiment_id: str, backend: str = "kaggle", **fields) -> str:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        cols = {
            "id": new_id("RUN"),
            "experiment_id": experiment_id,
            "attempt": row["n"] + 1,
            "backend": backend,
            "status": "pending",
            "created_at": utcnow(),
        }
        cols.update(fields)
        placeholders = ", ".join("?" for _ in cols)
        self.conn.execute(
            f"INSERT INTO runs ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(cols.values()),
        )
        self.add_event(kind="run.created", message=f"Run {cols['id']} (attempt {cols['attempt']})",
                       experiment_id=experiment_id, run_id=cols["id"])
        return cols["id"]

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    def transition_run(self, run_id: str, to_status: str, **fields) -> None:
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        current = run["status"]
        allowed = RUN_TRANSITIONS.get(current, set())
        if to_status not in allowed:
            raise TransitionError(
                f"{run_id}: cannot move {current} -> {to_status}"
                f" (allowed: {sorted(allowed) or 'none, terminal state'})"
            )
        updates = {"status": to_status, **fields}
        if to_status == "running" and run["started_at"] is None:
            updates["started_at"] = utcnow()
        if to_status in ("complete", "error", "cancelled", "timeout"):
            updates["ended_at"] = utcnow()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE runs SET {assignments} WHERE id = ?", (*updates.values(), run_id)
        )
        self.add_event(
            kind="run.status",
            message=f"{run_id}: {current} -> {to_status}",
            experiment_id=run["experiment_id"],
            run_id=run_id,
            level="error" if to_status in ("error", "timeout") else "info",
        )

    def runs_for(self, experiment_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM runs WHERE experiment_id = ? ORDER BY attempt", (experiment_id,)
        ).fetchall()

    # ---------------- metrics ----------------

    def record_metric(
        self,
        experiment_id: str,
        name: str,
        value: float,
        run_id: str | None = None,
        split: str | None = None,
        seed: int | None = None,
        step: int | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO metrics (experiment_id, run_id, name, value, split, seed, step,"
            " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (experiment_id, run_id, name, value, split, seed, step, utcnow()),
        )

    def metrics_for(self, experiment_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM metrics WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()

    def set_primary_metric(self, experiment_id: str, name: str, value: float,
                           direction: str) -> None:
        self.conn.execute(
            "UPDATE experiments SET primary_metric_name = ?, primary_metric = ?,"
            " metric_direction = ? WHERE id = ?",
            (name, value, direction, experiment_id),
        )

    def best_experiment(self, project_id: str) -> sqlite3.Row | None:
        """Best completed experiment by its own primary metric direction."""
        rows = [
            r
            for r in self.conn.execute(
                "SELECT * FROM experiments WHERE project_id = ? AND status = 'completed'"
                " AND primary_metric IS NOT NULL",
                (project_id,),
            ).fetchall()
        ]
        if not rows:
            return None
        minimize = rows[0]["metric_direction"] == "minimize"
        return sorted(rows, key=lambda r: r["primary_metric"], reverse=not minimize)[0]

    # ---------------- events ----------------

    def add_event(
        self,
        kind: str,
        message: str,
        level: str = "info",
        project_id: str | None = None,
        experiment_id: str | None = None,
        run_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        # Single choke point for the event stream, which is synced off-machine
        # and rendered in a browser. Scrubbing here means no caller can leak a
        # credential by logging an exception that embeds one in a URL.
        self.conn.execute(
            "INSERT INTO events (project_id, experiment_id, run_id, level, kind, message,"
            " data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                experiment_id,
                run_id,
                level,
                kind,
                redact(message),
                redact(json.dumps(data)) if data else None,
                utcnow(),
            ),
        )

    def recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ---------------- daemon state & crash recovery ----------------

    def heartbeat(self, pid: int, active_experiment_id: str | None = None) -> None:
        """Record liveness only.

        Deliberately does not touch `status`: a heartbeat that set it to
        'running' would silently un-pause a paused daemon on the next tick.
        Status changes go through start_session/pause/stop.
        """
        self.conn.execute(
            "UPDATE daemon_state SET pid = ?, last_heartbeat = ?,"
            " active_experiment_id = ? WHERE id = 1",
            (pid, utcnow(), active_experiment_id),
        )

    def daemon_state(self) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM daemon_state WHERE id = 1").fetchone()

    def pause(self, reason: str) -> None:
        self.conn.execute(
            "UPDATE daemon_state SET status = 'paused', pause_reason = ? WHERE id = 1",
            (reason,),
        )
        self.add_event(kind="daemon.paused", message=reason, level="warn")

    def events_since(self, event_id: int, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (event_id, limit)
        ).fetchall()

    def mark_events_synced(self, event_id: int) -> None:
        self.conn.execute(
            "UPDATE daemon_state SET last_synced_event_id = ? WHERE id = 1", (event_id,)
        )

    def pending_proposals(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM proposals WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()

    def find_orphaned_runs(self) -> list[sqlite3.Row]:
        """Runs still marked in-flight, i.e. the daemon died while they ran.

        These are recoverable rather than lost: the Kaggle kernel kept running
        server-side, so the monitor can re-attach on the next start.
        """
        placeholders = ", ".join("?" for _ in IN_FLIGHT_RUN_STATUSES)
        return self.conn.execute(
            f"SELECT * FROM runs WHERE status IN ({placeholders})", IN_FLIGHT_RUN_STATUSES
        ).fetchall()
