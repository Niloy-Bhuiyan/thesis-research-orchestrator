import pytest

from researchos.telegram.bot import (
    TelegramBot,
    Unauthorized,
    Update,
    approval_keyboard,
    load_allowlist,
    parse_update,
)

OWNER = 2088881866
STRANGER = 999999


def message(text, chat_id=OWNER, update_id=1):
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "from": {"username": "u"}, "text": text},
    }


def callback(data, chat_id=OWNER, update_id=1):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb1",
            "data": data,
            "from": {"username": "u"},
            "message": {"chat": {"id": chat_id}},
        },
    }


class FakeBot(TelegramBot):
    """Bot with the HTTP seam replaced. No network."""

    def __init__(self, allowlist=None, queue=None):
        super().__init__("test-token", allowlist or {OWNER})
        self.sent = []
        self.answered = []
        self.queue = list(queue or [])

    def _api(self, method, payload=None, timeout=60):
        if method == "getUpdates":
            batch, self.queue = self.queue, []
            return {"ok": True, "result": batch}
        if method == "sendMessage":
            self.sent.append(payload)
            return {"ok": True, "result": {"message_id": len(self.sent)}}
        if method == "answerCallbackQuery":
            self.answered.append(payload)
            return {"ok": True}
        return {"ok": True}


# ---------------- parsing ----------------


def test_parse_message_update():
    u = parse_update(message("/status"))
    assert u.chat_id == OWNER
    assert u.command == "/status"


def test_parse_command_arguments():
    u = parse_update(message("/approve EXP-0042"))
    assert u.command == "/approve"
    assert u.args == ["EXP-0042"]


def test_parse_callback_query():
    u = parse_update(callback("approve:PROP-1"))
    assert u.callback_data == "approve:PROP-1"
    assert u.callback_id == "cb1"
    assert u.chat_id == OWNER


def test_empty_text_has_no_command():
    assert parse_update(message("")).command == ""


# ---------------- authorization ----------------


def test_owner_is_authorized():
    assert FakeBot().is_authorized(OWNER)


def test_stranger_is_not_authorized():
    assert not FakeBot().is_authorized(STRANGER)


def test_unauthorized_dispatch_raises_and_sends_nothing():
    bot = FakeBot()
    bot.on("/status", lambda u: "secret state")
    with pytest.raises(Unauthorized):
        bot.dispatch(parse_update(message("/status", chat_id=STRANGER)))
    assert bot.sent == []


def test_unauthorized_update_is_dropped_silently_during_run():
    bot = FakeBot(queue=[message("/status", chat_id=STRANGER)])
    bot.on("/status", lambda u: "secret state")
    handled = bot.run_once()
    assert handled[0][1] is None
    assert bot.sent == []  # stranger gets no reply at all


def test_empty_allowlist_is_refused():
    with pytest.raises(ValueError):
        TelegramBot("t", set())


def test_allowlist_file_parsing(tmp_path):
    p = tmp_path / "allow"
    p.write_text("2088881866\n123, 456\n")
    assert load_allowlist(p) == {2088881866, 123, 456}


# ---------------- dispatch ----------------


def test_known_command_is_handled_and_replied():
    bot = FakeBot(queue=[message("/status")])
    bot.on("/status", lambda u: "EXP-0001 running")
    bot.run_once()
    assert bot.sent[0]["text"] == "EXP-0001 running"
    assert bot.sent[0]["chat_id"] == OWNER


def test_unknown_command_produces_no_reply():
    bot = FakeBot(queue=[message("/nonsense")])
    bot.run_once()
    assert bot.sent == []


def test_command_receives_arguments():
    bot = FakeBot(queue=[message("/approve PROP-7")])
    seen = {}
    bot.on("/approve", lambda u: seen.setdefault("args", u.args) and "ok" or "ok")
    bot.run_once()
    assert seen["args"] == ["PROP-7"]


# ---------------- inline approvals ----------------


def test_approval_keyboard_has_approve_reject_logs():
    kb = approval_keyboard("PROP-1")
    labels = [b["text"] for b in kb["inline_keyboard"][0]]
    assert labels == ["Approve", "Reject", "Logs"]
    assert kb["inline_keyboard"][0][0]["callback_data"] == "approve:PROP-1"


def test_callback_routes_to_action_handler():
    bot = FakeBot()
    bot.on("@approve", lambda u: f"approved {u.callback_data.split(':')[1]}")
    reply = bot.dispatch(parse_update(callback("approve:PROP-9")))
    assert reply == "approved PROP-9"


def test_callback_is_acknowledged_so_button_stops_spinning():
    bot = FakeBot()
    bot.on("@approve", lambda u: "done")
    bot.dispatch(parse_update(callback("approve:PROP-9")))
    assert bot.answered[0]["callback_query_id"] == "cb1"


def test_duplicate_approval_press_is_handled_by_handler_not_transport():
    """Second press reaches the handler, which decides it is already decided."""
    bot = FakeBot()
    decided = []

    def handler(u):
        pid = u.callback_data.split(":")[1]
        if pid in decided:
            return "already decided"
        decided.append(pid)
        return "approved"

    bot.on("@approve", handler)
    first = bot.dispatch(parse_update(callback("approve:PROP-1")))
    second = bot.dispatch(parse_update(callback("approve:PROP-1")))
    assert (first, second) == ("approved", "already decided")


# ---------------- polling ----------------


def test_offset_advances_past_processed_updates():
    bot = FakeBot(queue=[message("/status", update_id=41), message("/status", update_id=42)])
    bot.on("/status", lambda u: "ok")
    bot.run_once()
    assert bot.offset == 43


def test_offset_advances_even_for_dropped_stranger_updates():
    """Otherwise a stranger could wedge the queue by messaging repeatedly."""
    bot = FakeBot(queue=[message("/status", chat_id=STRANGER, update_id=77)])
    bot.run_once()
    assert bot.offset == 78


def test_broadcast_reaches_every_allowlisted_chat():
    bot = FakeBot(allowlist={OWNER, 555})
    bot.broadcast("daemon offline")
    assert {p["chat_id"] for p in bot.sent} == {OWNER, 555}
