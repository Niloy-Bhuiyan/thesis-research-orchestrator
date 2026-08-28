import os

import pytest

from researchos.providers.base import (
    AUTH_REQUIRED,
    COMMAND_FAILED,
    RATE_LIMITED,
    SUCCESS,
    USAGE_LIMITED,
    ClaudeCodeProvider,
    CodexProvider,
    PaidFallbackBlocked,
    Provider,
    ProviderResult,
    assert_no_paid_fallback,
    classify_output,
)
from researchos.providers.router import AllProvidersExhausted, Handoff, ProviderRouter
from researchos.state.db import Store


class FakeProvider(Provider):
    """Scripted provider: returns queued outcomes, then repeats the last one."""

    def __init__(self, name, outcomes, availability="available"):
        self.name = name
        self.outcomes = list(outcomes)
        self.availability = availability
        self.calls = 0

    def status(self):
        return self.availability

    def run(self, prompt, timeout=900):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        return ProviderResult(self.name, outcome, text=f"{self.name} handled it")


# ---------------- output classification ----------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Claude usage limit reached. Resets at 4pm", USAGE_LIMITED),
        ("Error: quota exceeded for this account", USAGE_LIMITED),
        ("HTTP 429 Too Many Requests", RATE_LIMITED),
        ("rate-limit hit, retry later", RATE_LIMITED),
        ("You are not logged in. Please run /login", AUTH_REQUIRED),
        ("401 Unauthorized", AUTH_REQUIRED),
        ("all good", SUCCESS),
    ],
)
def test_classify_output_detects_failure_modes(text, expected):
    assert classify_output(text, returncode=0) == expected


def test_usage_limit_takes_precedence_over_rate_limit():
    text = "usage limit reached (rate limit 429)"
    assert classify_output(text, 1) == USAGE_LIMITED


def test_nonzero_exit_without_known_pattern_is_command_failed():
    assert classify_output("segfault", returncode=139) == COMMAND_FAILED


# ---------------- paid fallback guard ----------------


def test_paid_fallback_blocked_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    with pytest.raises(PaidFallbackBlocked):
        assert_no_paid_fallback(allow_paid=False)


def test_paid_fallback_allowed_only_with_explicit_optin(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-whatever")
    assert_no_paid_fallback(allow_paid=True)  # must not raise


def test_no_keys_present_is_fine(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert_no_paid_fallback(allow_paid=False)


# ---------------- routing and failover ----------------


def test_first_provider_wins_when_healthy():
    codex = FakeProvider("codex", [SUCCESS])
    claude = FakeProvider("claude_code", [SUCCESS])
    result = ProviderRouter([codex, claude]).run("do the thing")
    assert result.provider == "codex"
    assert claude.calls == 0


def test_falls_over_to_claude_when_codex_usage_limited():
    codex = FakeProvider("codex", [USAGE_LIMITED])
    claude = FakeProvider("claude_code", [SUCCESS])
    result = ProviderRouter([codex, claude]).run("do the thing")
    assert result.provider == "claude_code"
    assert result.ok


def test_falls_back_to_codex_when_claude_usage_limited():
    claude = FakeProvider("claude_code", [USAGE_LIMITED])
    codex = FakeProvider("codex", [SUCCESS])
    result = ProviderRouter([claude, codex]).run("do the thing")
    assert result.provider == "codex"


def test_auth_failure_triggers_failover():
    a = FakeProvider("codex", [AUTH_REQUIRED])
    b = FakeProvider("claude_code", [SUCCESS])
    assert ProviderRouter([a, b]).run("x").provider == "claude_code"


def test_prefer_reorders_for_one_call_only():
    codex = FakeProvider("codex", [SUCCESS])
    claude = FakeProvider("claude_code", [SUCCESS])
    router = ProviderRouter([codex, claude])
    assert router.run("x", prefer="claude_code").provider == "claude_code"
    assert router.run("x").provider == "codex"
    assert router.order == ["codex", "claude_code"]


def test_all_exhausted_pauses_and_notifies(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    notified = []
    router = ProviderRouter(
        [FakeProvider("codex", [USAGE_LIMITED]), FakeProvider("claude_code", [USAGE_LIMITED])],
        store=store,
        on_exhausted=notified.append,
    )
    with pytest.raises(AllProvidersExhausted):
        router.run("x")
    assert len(notified) == 1
    assert store.daemon_state()["status"] == "paused"
    assert store.daemon_state()["pause_reason"] == "all providers exhausted"
    store.close()


def test_provider_calls_are_recorded(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    router = ProviderRouter(
        [FakeProvider("codex", [USAGE_LIMITED]), FakeProvider("claude_code", [SUCCESS])],
        store=store,
    )
    router.run("x", task="diagnose")
    rows = store.conn.execute(
        "SELECT provider, outcome, task FROM provider_calls ORDER BY rowid"
    ).fetchall()
    assert [(r["provider"], r["outcome"]) for r in rows] == [
        ("codex", USAGE_LIMITED),
        ("claude_code", SUCCESS),
    ]
    assert rows[0]["task"] == "diagnose"
    store.close()


def test_router_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        ProviderRouter([])


# ---------------- handoff ----------------


def test_handoff_is_self_contained_json():
    handoff = Handoff(
        project_id="thesis",
        experiment_id="EXP-0042",
        parent_experiment="EXP-0041",
        status="failed",
        failure_class="infrastructure",
        diagnosis="CUDA out of memory during validation",
        proposed_change="reduce validation batch size",
        scientific_impact="none_expected",
        next_action="patch_and_rerun",
    )
    prompt = handoff.as_prompt("Fix it.")
    assert "EXP-0042" in prompt
    assert "CUDA out of memory" in prompt
    assert "no prior conversation" in prompt


def test_handoff_survives_provider_switch():
    """The point of the handoff: provider B gets full context from JSON alone."""
    handoff = Handoff(project_id="p", experiment_id="EXP-0001", status="failed",
                      diagnosis="shape mismatch")
    seen = {}

    class Recorder(FakeProvider):
        def run(self, prompt, timeout=900):
            seen[self.name] = prompt
            return super().run(prompt, timeout)

    router = ProviderRouter(
        [Recorder("codex", [USAGE_LIMITED]), Recorder("claude_code", [SUCCESS])]
    )
    router.run(handoff.as_prompt("Continue."))
    assert "shape mismatch" in seen["claude_code"]


# ---------------- real CLI shape (no network) ----------------


def test_claude_provider_builds_expected_command():
    cmd = ClaudeCodeProvider()._command("hello")
    assert "claude" in cmd[0].lower()  # resolved to an absolute path on Windows
    assert cmd[1] == "-p"
    assert cmd[2] == "hello"


def test_codex_provider_pins_model_explicitly():
    """Global config pins an unsupported model, so -m must always be passed."""
    cmd = CodexProvider()._command("hello")
    assert "-m" in cmd
    assert cmd[cmd.index("-m") + 1] == "gpt-5.6-terra"
    assert "--skip-git-repo-check" in cmd


def test_missing_executable_reports_command_failed(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = ClaudeCodeProvider(executable="definitely-not-a-real-binary-xyz")
    result = provider.run("hi", timeout=10)
    assert result.outcome == COMMAND_FAILED
    assert "not found" in result.error
