"""Credentials must never reach the event stream.

The event stream is synced off-machine and rendered in a browser, so a secret
that lands there has left the researcher's control. These tests exist because a
real Telegram bot token was written to the log and synced to Supabase: the
Telegram API puts the token in the URL path, so any connection error produced an
exception string containing it.
"""

import pytest

from researchos.redaction import contains_secret, redact
from researchos.state.db import Store

TELEGRAM_ERROR = (
    "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded "
    "with url: /bot8951586512:AAHMhgXclDGIZqfh1iQpb4ZZNWScagDpN9w/getUpdates "
    "(Caused by NameResolutionError(...))"
)


def test_telegram_token_in_url_is_redacted():
    cleaned = redact(TELEGRAM_ERROR)
    assert "AAHMhgXclDGIZqfh1iQpb4ZZNWScagDpN9w" not in cleaned
    assert "8951586512" not in cleaned
    assert "/bot[redacted]/getUpdates" in cleaned


def test_surrounding_context_is_preserved():
    """Redaction must stay debuggable: keep everything that is not a secret."""
    cleaned = redact(TELEGRAM_ERROR)
    assert "api.telegram.org" in cleaned
    assert "Max retries exceeded" in cleaned


@pytest.mark.parametrize(
    "secret",
    [
        "8951586512:AAHMhgXclDGIZqfh1iQpb4ZZNWScagDpN9w",
        "sbp_abcdef0123456789abcdef0123456789abcdef01",
        "ghp_aBcDeF0123456789aBcDeF0123456789aBcDeF",
        "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_known_credential_shapes_are_redacted(secret):
    assert secret not in redact(f"failure involving {secret} while connecting")


def test_query_parameter_credentials_are_redacted():
    cleaned = redact("GET /rest/v1/runners?apikey=abcdef123456789012345 failed")
    assert "abcdef123456789012345" not in cleaned


def test_bearer_header_is_redacted():
    cleaned = redact("sent Authorization: Bearer abcdef1234567890abcdef1234567890")
    assert "abcdef1234567890abcdef1234567890" not in cleaned


def test_ordinary_text_is_untouched():
    message = "EXP-0042: running -> failed (CUDA out of memory at step 400)"
    assert redact(message) == message
    assert not contains_secret(message)


def test_none_and_empty_are_safe():
    assert redact(None) is None
    assert redact("") == ""


def test_contains_secret_detects_and_clears():
    assert contains_secret(TELEGRAM_ERROR)
    assert not contains_secret(redact(TELEGRAM_ERROR))


# ---------------- the choke point ----------------


def test_add_event_scrubs_the_message(tmp_path):
    """Regression: this exact string was stored verbatim and synced to Supabase."""
    with Store(tmp_path / "s.sqlite3") as store:
        store.add_event(kind="telegram.error", message=TELEGRAM_ERROR, level="warn")
        stored = store.recent_events(1)[0]["message"]
    assert "AAHMhgXclDGIZqfh1iQpb4ZZNWScagDpN9w" not in stored
    assert "[redacted]" in stored


def test_add_event_scrubs_structured_data(tmp_path):
    with Store(tmp_path / "s.sqlite3") as store:
        store.add_event(
            kind="telegram.error",
            message="call failed",
            data={"url": "/bot8951586512:AAHMhgXclDGIZqfh1iQpb4ZZNWScagDpN9w/getMe"},
        )
        stored = store.recent_events(1)[0]["data"]
    assert "AAHMhgXclDGIZqfh1iQpb4ZZNWScagDpN9w" not in stored


def test_no_event_written_by_the_suite_contains_a_secret(tmp_path):
    """Belt and braces: scan everything a normal session would produce."""
    with Store(tmp_path / "s.sqlite3") as store:
        store.create_project("p", "P", str(tmp_path))
        exp = store.create_experiment("p", hypothesis="h")
        store.transition_experiment(exp, "approved")
        store.add_event(kind="telegram.error", message=TELEGRAM_ERROR, level="warn")
        rows = store.recent_events(100)
    assert not any(contains_secret(r["message"]) for r in rows)
