"""Telegram control surface.

Long polling from the local daemon, so the laptop never exposes an inbound
port to the internet. Every update is checked against an allowlist of chat IDs
before anything is dispatched: an unknown sender is dropped, not answered, so
the bot does not confirm its own existence to strangers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

API = "https://api.telegram.org"

HELP = """ResearchOS commands

/status      current experiment, mode, provider, Kaggle state
/experiments recent experiments and their metrics
/mode        show or set mode (manual|auto|locked)
/providers   provider availability
/kaggle      Kaggle auth and run state
/pause       pause the autonomous loop
/resume      resume the loop
/approve ID  approve a pending proposal
/reject ID   reject a pending proposal
/logs        recent events
/help        this message"""


class Unauthorized(Exception):
    """Raised when a chat id is not on the allowlist."""


def load_secret(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def load_allowlist(path: str | Path) -> set[int]:
    text = Path(path).read_text(encoding="utf-8")
    return {int(line.strip()) for line in text.replace(",", "\n").splitlines()
            if line.strip()}


@dataclass
class Update:
    update_id: int
    chat_id: int | None
    text: str
    callback_data: str | None = None
    callback_id: str | None = None
    username: str | None = None

    @property
    def command(self) -> str:
        return self.text.split()[0].lower() if self.text.strip() else ""

    @property
    def args(self) -> list[str]:
        return self.text.split()[1:]


def parse_update(raw: dict) -> Update:
    """Normalise the two shapes Telegram sends: messages and button presses."""
    if "callback_query" in raw:
        cq = raw["callback_query"]
        chat = (cq.get("message") or {}).get("chat") or {}
        return Update(
            update_id=raw["update_id"],
            chat_id=chat.get("id"),
            text=cq.get("data", ""),
            callback_data=cq.get("data"),
            callback_id=cq.get("id"),
            username=(cq.get("from") or {}).get("username"),
        )
    message = raw.get("message") or raw.get("edited_message") or {}
    chat = message.get("chat") or {}
    return Update(
        update_id=raw["update_id"],
        chat_id=chat.get("id"),
        text=message.get("text", "") or "",
        username=(message.get("from") or {}).get("username"),
    )


def approval_keyboard(proposal_id: str, with_logs: bool = True) -> dict:
    buttons = [
        {"text": "Approve", "callback_data": f"approve:{proposal_id}"},
        {"text": "Reject", "callback_data": f"reject:{proposal_id}"},
    ]
    if with_logs:
        buttons.append({"text": "Logs", "callback_data": f"logs:{proposal_id}"})
    return {"inline_keyboard": [buttons]}


class TelegramBot:
    def __init__(self, token: str, allowlist: set[int], session=None):
        if not allowlist:
            raise ValueError("refusing to start with an empty allowlist")
        self.token = token
        self.allowlist = set(allowlist)
        self.session = session or requests.Session()
        self.offset = 0
        self.handlers: dict[str, Callable[[Update], str]] = {}

    # ---- transport (single seam; tests replace _api) ----

    def _api(self, method: str, payload: dict | None = None, timeout: int = 60) -> dict:
        response = self.session.post(
            f"{API}/bot{self.token}/{method}", json=payload or {}, timeout=timeout
        )
        return response.json()

    def send(self, chat_id: int, text: str, keyboard: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = json.dumps(keyboard)
        return self._api("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> dict:
        return self._api("answerCallbackQuery", {"callback_query_id": callback_id,
                                                 "text": text})

    def get_me(self) -> dict:
        return self._api("getMe")

    # ---- authorization ----

    def is_authorized(self, chat_id: int | None) -> bool:
        return chat_id is not None and chat_id in self.allowlist

    # ---- dispatch ----

    def on(self, command: str, handler: Callable[[Update], str]) -> None:
        self.handlers[command] = handler

    def dispatch(self, update: Update) -> str | None:
        """Handle one update. Returns the reply text, or None if dropped.

        Unauthorized senders get no reply at all.
        """
        if not self.is_authorized(update.chat_id):
            raise Unauthorized(f"chat {update.chat_id} is not allowlisted")

        if update.callback_data:
            action = update.callback_data.split(":", 1)[0]
            handler = self.handlers.get(f"@{action}")
            if handler is None:
                return None
            reply = handler(update)
            if update.callback_id:
                self.answer_callback(update.callback_id, reply[:200])
            return reply

        handler = self.handlers.get(update.command)
        if handler is None:
            return None
        return handler(update)

    def poll_once(self, timeout: int = 30) -> list[Update]:
        """Fetch pending updates and advance the offset past them.

        The offset advances even for dropped unauthorized updates, otherwise a
        stranger could wedge the queue by repeatedly messaging the bot.
        """
        data = self._api(
            "getUpdates", {"offset": self.offset, "timeout": timeout}, timeout=timeout + 15
        )
        updates = [parse_update(raw) for raw in data.get("result", [])]
        if updates:
            self.offset = max(u.update_id for u in updates) + 1
        return updates

    def run_once(self, timeout: int = 30) -> list[tuple[Update, str | None]]:
        handled = []
        for update in self.poll_once(timeout=timeout):
            try:
                reply = self.dispatch(update)
            except Unauthorized:
                handled.append((update, None))
                continue
            if reply and update.chat_id and not update.callback_data:
                self.send(update.chat_id, reply)
            handled.append((update, reply))
        return handled

    def broadcast(self, text: str, keyboard: dict | None = None) -> None:
        for chat_id in sorted(self.allowlist):
            self.send(chat_id, text, keyboard)
