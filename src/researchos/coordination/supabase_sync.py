"""Outbound sync between the local daemon and the Supabase coordination layer.

The daemon polls; nothing connects inward. That is what allows the laptop to
sit behind NAT with no exposed port while the dashboard still reaches it.

Only coordination metadata leaves the machine: liveness, a command queue,
approval requests and short event lines. Logs, code, checkpoints, datasets and
credentials stay local. The remote database is never authoritative - if the two
disagree, local SQLite wins, because that is the record the research depends on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

# Commands older than this are refused rather than executed late: a command
# queued while the laptop was closed should not fire hours afterwards.
DEFAULT_COMMAND_TTL_SECONDS = 3600


class CoordinationError(RuntimeError):
    pass


class OwnerNotFound(CoordinationError):
    """No Supabase auth user exists yet, so nothing can be attributed."""


def read_secret(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


@dataclass
class SupabaseConfig:
    project_ref: str
    service_key: str
    runner_id: str
    owner: str | None = None

    @property
    def base_url(self) -> str:
        return f"https://{self.project_ref}.supabase.co"

    @classmethod
    def from_secrets(cls, secrets_dir: str | Path, runner_id: str) -> "SupabaseConfig":
        secrets_dir = Path(secrets_dir)
        return cls(
            project_ref=read_secret(secrets_dir / "supabase_project_ref"),
            service_key=read_secret(secrets_dir / "supabase_service_role_key"),
            runner_id=runner_id,
        )


class SupabaseSync:
    def __init__(self, config: SupabaseConfig, session=None, timeout: int = 20):
        self.config = config
        self.session = session or requests.Session()
        self.timeout = timeout

    # ---- transport ----

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "apikey": self.config.service_key,
            "Authorization": f"Bearer {self.config.service_key}",
            "Content-Type": "application/json",
        }
        headers.update(extra or {})
        return headers

    def _request(self, method: str, path: str, **kwargs):
        response = self.session.request(
            method,
            f"{self.config.base_url}{path}",
            headers=self._headers(kwargs.pop("extra_headers", None)),
            timeout=self.timeout,
            **kwargs,
        )
        if response.status_code >= 400:
            raise CoordinationError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}"
            )
        return response

    # ---- owner ----

    def resolve_owner(self) -> str:
        """The single account this runner belongs to.

        Looked up through the admin API rather than configured by hand, so the
        researcher only has to sign in on the dashboard once.
        """
        if self.config.owner:
            return self.config.owner
        response = self._request("GET", "/auth/v1/admin/users?per_page=2")
        users = response.json().get("users", [])
        if not users:
            raise OwnerNotFound(
                "no Supabase user yet; sign in on the dashboard once to create one"
            )
        self.config.owner = users[0]["id"]
        return self.config.owner

    # ---- push ----

    def heartbeat(self, state, project=None) -> None:
        payload = {
            "id": self.config.runner_id,
            "owner": self.resolve_owner(),
            "status": state["status"],
            "project_id": project["id"] if project else None,
            "mode": project["mode"] if project else None,
            "active_experiment": state["active_experiment_id"],
            "pause_reason": state["pause_reason"],
            "last_heartbeat": state["last_heartbeat"],
        }
        self._request(
            "POST",
            "/rest/v1/runners",
            data=json.dumps(payload),
            extra_headers={"Prefer": "resolution=merge-duplicates"},
        )

    def push_events(self, events) -> int:
        rows = [
            {
                "owner": self.resolve_owner(),
                "runner_id": self.config.runner_id,
                "level": event["level"],
                "kind": event["kind"],
                # Truncated on purpose: this feed is for orientation, not logs.
                "message": (event["message"] or "")[:500],
                "experiment_id": event["experiment_id"],
                "occurred_at": event["created_at"],
            }
            for event in events
        ]
        if not rows:
            return 0
        self._request("POST", "/rest/v1/events", data=json.dumps(rows))
        return len(rows)

    def push_proposals(self, proposals) -> int:
        rows = [
            {
                "owner": self.resolve_owner(),
                "runner_id": self.config.runner_id,
                "local_id": proposal["id"],
                "experiment_id": proposal["experiment_id"],
                "kind": proposal["kind"],
                "summary": proposal["summary"],
                "detail": proposal["detail"],
                "target_field": proposal["target_field"],
                "scientific_impact": proposal["scientific_impact"],
                "status": proposal["status"],
            }
            for proposal in proposals
        ]
        if not rows:
            return 0
        self._request(
            "POST",
            "/rest/v1/proposals",
            data=json.dumps(rows),
            extra_headers={
                "Prefer": "resolution=merge-duplicates",
                "on_conflict": "runner_id,local_id",
            },
            params={"on_conflict": "runner_id,local_id"},
        )
        return len(rows)

    # ---- pull ----

    def pull_commands(self) -> list[dict]:
        """Pending, unexpired commands for this runner."""
        response = self._request(
            "GET",
            "/rest/v1/commands",
            params={
                "runner_id": f"eq.{self.config.runner_id}",
                "status": "eq.pending",
                "expires_at": "gt.now()",
                "order": "created_at.asc",
                "select": "*",
            },
        )
        return response.json()

    def ack_command(self, command_id: str, status: str, result: str | None = None) -> None:
        if status not in ("acked", "running", "done", "failed", "rejected", "expired"):
            raise ValueError(f"invalid command status: {status}")
        payload: dict = {"status": status}
        if result is not None:
            payload["result"] = result[:1000]
        if status == "acked":
            payload["acked_at"] = "now()"
        if status in ("done", "failed", "rejected"):
            payload["completed_at"] = "now()"
        self._request(
            "PATCH",
            "/rest/v1/commands",
            params={"id": f"eq.{command_id}"},
            data=json.dumps(payload),
        )

    def expire_stale(self) -> None:
        self._request("POST", "/rest/v1/rpc/expire_stale_commands", data="{}")
