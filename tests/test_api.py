import json
import threading
import urllib.error
import urllib.request

import pytest

from researchos.api import ALLOWED_COMMANDS, TOKEN_FILE, ensure_token, serve
from researchos.config import Settings
from researchos.state.db import Store


@pytest.fixture
def api(tmp_path):
    settings = Settings(workspace_root=str(tmp_path), active_project="thesis")
    store = Store(settings.db_path)
    store.create_project("thesis", "Thesis", str(tmp_path), mode="auto_exploration")
    exp_id = store.create_experiment("thesis", hypothesis="lower LR")
    store.transition_experiment(exp_id, "approved")
    store.close()

    token = ensure_token(settings)
    server = serve(settings, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", token, settings, exp_id
    server.shutdown()
    server.server_close()


def get(base, path, token=None):
    request = urllib.request.Request(f"{base}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.load(response)


def post(base, path, payload, token=None):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="POST"
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.load(response)


# ---------------- auth ----------------


def test_health_is_public_so_offline_differs_from_unauthorized(api):
    base, _, _, _ = api
    status, body = get(base, "/api/health")
    assert status == 200
    assert body["ok"] is True


def test_read_without_token_is_rejected(api):
    base, _, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base, "/api/status")
    assert exc.value.code == 401


def test_read_with_wrong_token_is_rejected(api):
    base, _, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base, "/api/status", token="not-the-token")
    assert exc.value.code == 401


def test_token_is_generated_once_and_reused(tmp_path):
    settings = Settings(workspace_root=str(tmp_path))
    first = ensure_token(settings)
    assert ensure_token(settings) == first
    assert settings.secret_path(TOKEN_FILE).exists()


def test_token_lives_under_secrets_so_gitignore_covers_it():
    assert TOKEN_FILE.startswith(".secrets/")


# ---------------- reads ----------------


def test_status_returns_real_daemon_and_project_state(api):
    base, token, _, _ = api
    _, body = get(base, "/api/status", token)
    assert body["daemon"]["status"] == "stopped"
    assert body["project"]["mode"] == "auto_exploration"


def test_status_reports_null_best_when_nothing_completed(api):
    """No experiment has finished, so there is no best. Do not invent one."""
    base, token, _, _ = api
    _, body = get(base, "/api/status", token)
    assert body["best_experiment"] is None


def test_experiments_list(api):
    base, token, _, exp_id = api
    _, body = get(base, "/api/experiments", token)
    assert [e["id"] for e in body] == [exp_id]
    assert body[0]["hypothesis"] == "lower LR"


def test_experiment_detail_includes_runs_and_metrics(api):
    base, token, _, exp_id = api
    _, body = get(base, f"/api/experiments/{exp_id}", token)
    assert body["experiment"]["id"] == exp_id
    assert body["runs"] == []
    assert body["metrics"] == []


def test_unknown_experiment_is_404(api):
    base, token, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base, "/api/experiments/EXP-9999", token)
    assert exc.value.code == 404


def test_events_endpoint_returns_the_stream(api):
    base, token, _, _ = api
    _, body = get(base, "/api/events", token)
    assert any(e["kind"] == "experiment.created" for e in body)


def test_unknown_path_is_404(api):
    base, token, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base, "/api/nope", token)
    assert exc.value.code == 404


# ---------------- commands ----------------


def test_pause_command_is_queued(api):
    base, token, settings, _ = api
    status, body = post(base, "/api/commands", {"type": "pause"}, token)
    assert status == 202
    assert body["accepted"] is True
    store = Store(settings.db_path)
    row = store.conn.execute("SELECT * FROM commands").fetchone()
    assert row["type"] == "pause"
    assert row["status"] == "pending"
    assert row["source"] == "local_api"
    store.close()


def test_unknown_command_is_refused(api):
    base, token, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(base, "/api/commands", {"type": "rm_rf_everything"}, token)
    assert exc.value.code == 400


def test_critical_command_requires_confirmation(api):
    """stop and set_mode change state that is expensive to get wrong."""
    base, token, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(base, "/api/commands", {"type": "set_mode",
                                     "params": {"mode": "auto_exploration"}}, token)
    assert exc.value.code == 409


def test_critical_command_accepted_with_confirmation(api):
    base, token, _, _ = api
    status, _ = post(
        base, "/api/commands",
        {"type": "set_mode", "confirm": True, "params": {"mode": "manual"}}, token,
    )
    assert status == 202


def test_command_without_token_is_rejected(api):
    base, _, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(base, "/api/commands", {"type": "pause"})
    assert exc.value.code == 401


def test_rejected_post_does_not_corrupt_the_next_request(api):
    """A 401 that leaves the body unread wedges the keep-alive connection.

    Regression: the auth check ran before the body was drained, so an
    unauthorized POST could break the *following* legitimate request.
    """
    base, token, _, _ = api
    for _ in range(5):
        with pytest.raises(urllib.error.HTTPError):
            post(base, "/api/commands", {"type": "pause", "pad": "x" * 2000})
        status, body = get(base, "/api/status", token)
        assert status == 200
        assert body["daemon"]["status"] == "stopped"


def test_rejected_unknown_path_post_also_drains_body(api):
    base, token, _, _ = api
    with pytest.raises(urllib.error.HTTPError):
        post(base, "/api/nope", {"payload": "y" * 2000}, token)
    assert get(base, "/api/experiments", token)[0] == 200


def test_allowlist_is_explicit():
    """A new capability must be added deliberately, not by string coincidence."""
    assert ALLOWED_COMMANDS == {"pause", "resume", "stop", "set_mode", "approve", "reject"}
