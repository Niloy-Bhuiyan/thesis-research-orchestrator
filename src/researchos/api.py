"""Local HTTP API for the dashboard.

Binds to loopback only. The laptop never opens a port to the internet: remote
access happens later through the coordination layer, which the daemon polls
outbound. A bearer token is still required, because anything running on this
machine can otherwise reach a loopback port.

Read endpoints return real state or an explicit null. Nothing here fabricates
a metric, a provider status, or a Kaggle state that was not observed.
"""

from __future__ import annotations

import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .config import Settings
from .state.db import Store, utcnow

TOKEN_FILE = ".secrets/api_token"

# Commands the dashboard may issue. Anything not listed is refused, so a new
# capability has to be added deliberately rather than by string coincidence.
ALLOWED_COMMANDS = {"pause", "resume", "stop", "set_mode", "approve", "reject"}

# Commands that change scientific or destructive state need confirmation.
CONFIRM_REQUIRED = {"stop", "set_mode"}


def ensure_token(settings: Settings) -> str:
    """Read the API token, generating one on first use."""
    path = settings.secret_path(TOKEN_FILE)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


class ApiState:
    """Everything a handler needs, kept off the handler class itself."""

    def __init__(self, settings: Settings, token: str):
        self.settings = settings
        self.token = token

    def store(self) -> Store:
        return Store(self.settings.db_path)


def build_handler(state: ApiState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the daemon's stdout readable
            pass

        # ---- plumbing ----

        def _send(self, payload: dict | list, status: int = 200) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "authorization,content-type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            return header == f"Bearer {state.token}"

        def do_OPTIONS(self):  # noqa: N802
            self._send({})

        # ---- routing ----

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/api/health":
                # Unauthenticated on purpose: the dashboard uses it to tell
                # "daemon offline" from "wrong token".
                return self._send({"ok": True, "service": "researchos"})
            if not self._authorized():
                return self._send({"error": "unauthorized"}, 401)

            store = state.store()
            try:
                return self._route_get(path, store)
            finally:
                store.close()

        def _route_get(self, path: str, store: Store):
            project_id = state.settings.active_project

            if path == "/api/status":
                daemon = dict(store.daemon_state())
                project = store.get_project(project_id) if project_id else None
                best = store.best_experiment(project_id) if project_id else None
                active = None
                if daemon.get("active_experiment_id"):
                    row = store.get_experiment(daemon["active_experiment_id"])
                    active = dict(row) if row else None
                return self._send({
                    "daemon": daemon,
                    "project": dict(project) if project else None,
                    "active_experiment": active,
                    "best_experiment": dict(best) if best else None,
                })

            if path == "/api/experiments":
                if not project_id:
                    return self._send([])
                return self._send(rows_to_dicts(store.list_experiments(project_id)))

            match = re.fullmatch(r"/api/experiments/([A-Za-z0-9_-]+)", path)
            if match:
                exp = store.get_experiment(match.group(1))
                if exp is None:
                    return self._send({"error": "not found"}, 404)
                return self._send({
                    "experiment": dict(exp),
                    "runs": rows_to_dicts(store.runs_for(exp["id"])),
                    "metrics": rows_to_dicts(store.metrics_for(exp["id"])),
                    "children": rows_to_dicts(store.children_of(exp["id"])),
                })

            if path == "/api/events":
                return self._send(rows_to_dicts(store.recent_events(100)))

            if path == "/api/proposals":
                rows = store.conn.execute(
                    "SELECT * FROM proposals ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                return self._send(rows_to_dicts(rows))

            if path == "/api/providers":
                rows = store.conn.execute(
                    "SELECT provider, outcome, model, task, started_at FROM provider_calls"
                    " ORDER BY rowid DESC LIMIT 25"
                ).fetchall()
                return self._send(rows_to_dicts(rows))

            if path == "/api/runs":
                rows = store.conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                return self._send(rows_to_dicts(rows))

            return self._send({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            if not self._authorized():
                return self._send({"error": "unauthorized"}, 401)
            if path != "/api/commands":
                return self._send({"error": "not found"}, 404)

            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send({"error": "invalid json"}, 400)

            command = payload.get("type")
            if command not in ALLOWED_COMMANDS:
                return self._send({"error": f"unknown command: {command}"}, 400)
            if command in CONFIRM_REQUIRED and not payload.get("confirm"):
                return self._send(
                    {"error": f"{command} requires confirm: true"}, 409
                )

            store = state.store()
            try:
                command_id = f"CMD-{secrets.token_hex(6)}"
                store.conn.execute(
                    "INSERT INTO commands (id, project_id, type, params, issued_by,"
                    " source, status, created_at) VALUES (?,?,?,?,?,?,'pending',?)",
                    (
                        command_id, state.settings.active_project, command,
                        json.dumps(payload.get("params") or {}), "dashboard",
                        "local_api", utcnow(),
                    ),
                )
                store.add_event(kind=f"command.{command}", message=command_id)
                return self._send({"accepted": True, "command_id": command_id}, 202)
            finally:
                store.close()

    return Handler


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765):
    """Create the server. Caller decides whether to block or run in a thread."""
    handler = build_handler(ApiState(settings, ensure_token(settings)))
    return ThreadingHTTPServer((host, port), handler)
