import json

import pytest

from researchos.coordination.supabase_sync import (
    CoordinationError,
    OwnerNotFound,
    SupabaseConfig,
    SupabaseSync,
)

OWNER = "11111111-2222-3333-4444-555555555555"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    """Records requests and replays scripted responses by path fragment."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": kwargs.get("params"),
                "body": json.loads(kwargs["data"]) if kwargs.get("data") else None,
            }
        )
        for fragment, response in self.responses.items():
            if fragment in url:
                return response
        return FakeResponse()


def make_sync(responses=None, owner=OWNER):
    config = SupabaseConfig(
        project_ref="testref", service_key="service-key", runner_id="laptop", owner=owner
    )
    session = FakeSession(responses)
    return SupabaseSync(config, session=session), session


# ---------------- config ----------------


def test_base_url_is_the_project_host():
    config = SupabaseConfig("abc123", "k", "laptop")
    assert config.base_url == "https://abc123.supabase.co"


def test_secrets_are_read_from_files_not_config(tmp_path):
    (tmp_path / "supabase_project_ref").write_text("abc123")
    (tmp_path / "supabase_service_role_key").write_text("secret-key")
    config = SupabaseConfig.from_secrets(tmp_path, "laptop")
    assert config.project_ref == "abc123"
    assert config.service_key == "secret-key"


# ---------------- owner resolution ----------------


def test_owner_resolved_from_admin_api():
    sync, _ = make_sync(
        {"/auth/v1/admin/users": FakeResponse(200, {"users": [{"id": OWNER}]})},
        owner=None,
    )
    assert sync.resolve_owner() == OWNER


def test_owner_lookup_is_cached():
    sync, session = make_sync(
        {"/auth/v1/admin/users": FakeResponse(200, {"users": [{"id": OWNER}]})},
        owner=None,
    )
    sync.resolve_owner()
    sync.resolve_owner()
    auth_calls = [c for c in session.calls if "admin/users" in c["url"]]
    assert len(auth_calls) == 1


def test_no_user_yet_raises_actionable_error():
    sync, _ = make_sync(
        {"/auth/v1/admin/users": FakeResponse(200, {"users": []})}, owner=None
    )
    with pytest.raises(OwnerNotFound) as exc:
        sync.resolve_owner()
    assert "sign in on the dashboard" in str(exc.value)


# ---------------- push ----------------


def test_heartbeat_upserts_runner_row():
    sync, session = make_sync()
    sync.heartbeat(
        {
            "status": "running",
            "active_experiment_id": "EXP-0007",
            "pause_reason": None,
            "last_heartbeat": "2026-08-28T19:00:00+00:00",
        },
        {"id": "thesis", "mode": "auto_exploration"},
    )
    call = session.calls[-1]
    assert call["method"] == "POST"
    assert "/rest/v1/runners" in call["url"]
    assert call["headers"]["Prefer"] == "resolution=merge-duplicates"
    assert call["body"]["active_experiment"] == "EXP-0007"
    assert call["body"]["owner"] == OWNER


def test_heartbeat_without_project_sends_nulls():
    sync, session = make_sync()
    sync.heartbeat(
        {
            "status": "stopped",
            "active_experiment_id": None,
            "pause_reason": None,
            "last_heartbeat": None,
        }
    )
    assert session.calls[-1]["body"]["project_id"] is None


def test_events_are_pushed_as_a_batch():
    sync, session = make_sync()
    events = [
        {"level": "info", "kind": "experiment.created", "message": "EXP-0001",
         "experiment_id": "EXP-0001", "created_at": "2026-08-28T19:00:00+00:00"},
        {"level": "warn", "kind": "daemon.paused", "message": "budget",
         "experiment_id": None, "created_at": "2026-08-28T19:01:00+00:00"},
    ]
    assert sync.push_events(events) == 2
    assert len(session.calls[-1]["body"]) == 2


def test_event_messages_are_truncated_not_full_logs():
    """This feed is for orientation. Logs stay on the researcher's machine."""
    sync, session = make_sync()
    sync.push_events([
        {"level": "info", "kind": "k", "message": "x" * 5000,
         "experiment_id": None, "created_at": "2026-08-28T19:00:00+00:00"}
    ])
    assert len(session.calls[-1]["body"][0]["message"]) == 500


def test_empty_push_makes_no_request():
    sync, session = make_sync()
    assert sync.push_events([]) == 0
    assert session.calls == []


def test_proposals_upsert_on_runner_and_local_id():
    sync, session = make_sync()
    sync.push_proposals([
        {"id": "PROP-1", "experiment_id": "EXP-0007", "kind": "loss_function",
         "summary": "change loss", "detail": None, "target_field": "loss_function",
         "scientific_impact": "significant", "status": "pending"}
    ])
    call = session.calls[-1]
    assert call["params"]["on_conflict"] == "runner_id,local_id"
    assert call["body"][0]["local_id"] == "PROP-1"


# ---------------- pull ----------------


def test_pull_requests_only_pending_unexpired_commands():
    sync, session = make_sync(
        {"/rest/v1/commands": FakeResponse(200, [{"id": "c1", "type": "pause"}])}
    )
    commands = sync.pull_commands()
    params = session.calls[-1]["params"]
    assert params["status"] == "eq.pending"
    assert params["expires_at"] == "gt.now()"
    assert params["runner_id"] == "eq.laptop"
    assert commands[0]["type"] == "pause"


def test_pull_is_scoped_to_this_runner():
    """Another machine's commands must never be drained by this daemon."""
    sync, session = make_sync()
    sync.pull_commands()
    assert session.calls[-1]["params"]["runner_id"] == "eq.laptop"


def test_ack_marks_command_done():
    sync, session = make_sync()
    sync.ack_command("c1", "done", result="paused")
    call = session.calls[-1]
    assert call["method"] == "PATCH"
    assert call["params"]["id"] == "eq.c1"
    assert call["body"]["status"] == "done"
    assert call["body"]["completed_at"] == "now()"


def test_ack_rejects_invalid_status():
    sync, _ = make_sync()
    with pytest.raises(ValueError):
        sync.ack_command("c1", "banana")


def test_ack_result_is_truncated():
    sync, session = make_sync()
    sync.ack_command("c1", "failed", result="e" * 4000)
    assert len(session.calls[-1]["body"]["result"]) == 1000


# ---------------- errors ----------------


def test_http_error_is_wrapped_with_context():
    sync, _ = make_sync({"/rest/v1/runners": FakeResponse(500, {"msg": "boom"})})
    with pytest.raises(CoordinationError) as exc:
        sync.heartbeat({"status": "running", "active_experiment_id": None,
                        "pause_reason": None, "last_heartbeat": None})
    assert "500" in str(exc.value)


def test_service_key_is_sent_as_apikey_and_bearer():
    sync, session = make_sync()
    sync.pull_commands()
    headers = session.calls[-1]["headers"]
    assert headers["apikey"] == "service-key"
    assert headers["Authorization"] == "Bearer service-key"
