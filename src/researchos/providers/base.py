"""Provider abstraction over subscription-authenticated coding CLIs.

Both supported providers are local CLIs driven over subprocess, using the
user's existing subscription session. Neither is allowed to fall back to a
billable API key: see `assert_no_paid_fallback`.

We never invent usage numbers. When a CLI does not expose remaining quota, the
status stays `unknown` rather than being guessed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Terminal outcome of a single provider invocation.
SUCCESS = "success"
AUTH_REQUIRED = "auth_required"
USAGE_LIMITED = "usage_limited"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
COMMAND_FAILED = "command_failed"
INVALID_OUTPUT = "invalid_output"

# Outcomes where trying the next provider is the right move. An auth problem is
# included because the *other* provider may still be authenticated.
FAILOVER_OUTCOMES = frozenset(
    {AUTH_REQUIRED, USAGE_LIMITED, RATE_LIMITED, TIMEOUT, COMMAND_FAILED}
)

# Reported provider availability. Deliberately coarse - see module docstring.
STATUS_AVAILABLE = "available"
STATUS_AUTH_REQUIRED = "auth_required"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNKNOWN = "unknown"

_USAGE_PATTERNS = [
    r"usage limit reached",
    r"you'?ve (?:hit|reached) your .*limit",
    r"quota (?:exceeded|exhausted)",
    r"insufficient quota",
]
_RATE_PATTERNS = [
    r"rate.?limit",
    r"\b429\b",
    r"too many requests",
]
_AUTH_PATTERNS = [
    r"not (?:logged in|authenticated)",
    r"please (?:run )?/?login",
    r"authentication (?:required|failed)",
    r"\b401\b",
    r"unauthorized",
    r"invalid api key",
]


def new_call_id() -> str:
    return f"PC-{uuid.uuid4().hex[:12]}"


class PaidFallbackBlocked(RuntimeError):
    """Raised when a billable API key would have been used without opt-in."""


def assert_no_paid_fallback(allow_paid: bool = False) -> None:
    """Refuse to run if a billable key is present but paid fallback is off.

    The default configuration is subscription-only. Silently spending money on
    the user's behalf is the failure mode this guards against.
    """
    if allow_paid:
        return
    live = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if os.environ.get(k)]
    if live:
        raise PaidFallbackBlocked(
            f"{', '.join(live)} set in the environment but paid API fallback is OFF. "
            "Unset it, or explicitly enable paid fallback."
        )


def classify_output(text: str, returncode: int) -> str:
    """Map CLI output onto an outcome. Order matters: usage beats rate."""
    lowered = text.lower()
    for pattern in _USAGE_PATTERNS:
        if re.search(pattern, lowered):
            return USAGE_LIMITED
    for pattern in _RATE_PATTERNS:
        if re.search(pattern, lowered):
            return RATE_LIMITED
    for pattern in _AUTH_PATTERNS:
        if re.search(pattern, lowered):
            return AUTH_REQUIRED
    if returncode != 0:
        return COMMAND_FAILED
    return SUCCESS


@dataclass
class ProviderResult:
    provider: str
    outcome: str
    text: str = ""
    model: str | None = None
    duration_ms: int = 0
    error: str | None = None
    raw: str = field(default="", repr=False)

    @property
    def ok(self) -> bool:
        return self.outcome == SUCCESS


class Provider(ABC):
    name: str

    @abstractmethod
    def status(self) -> str:
        """Coarse availability. Must not fabricate remaining-usage numbers."""

    @abstractmethod
    def run(self, prompt: str, timeout: int = 900) -> ProviderResult:
        """Execute one non-interactive task."""


class CliProvider(Provider):
    """Shared subprocess driver for the CLI-backed providers."""

    def __init__(self, executable: str, model: str | None = None,
                 allow_paid: bool = False):
        self.executable = executable
        self.model = model
        self.allow_paid = allow_paid

    @abstractmethod
    def _command(self, prompt: str) -> list[str]:
        ...

    @abstractmethod
    def _status_command(self) -> list[str]:
        ...

    def _resolve(self) -> str:
        """Absolute path to the CLI.

        On Windows these tools are installed as `.cmd` shims by npm, and
        subprocess does not consult PATHEXT the way a shell does. Resolving up
        front is what makes `codex` reachable from a non-shell process.
        """
        return shutil.which(self.executable) or self.executable

    def status(self) -> str:
        try:
            proc = subprocess.run(
                self._status_command(), capture_output=True, text=True, timeout=60
            )
        except FileNotFoundError:
            return STATUS_UNAVAILABLE
        except subprocess.TimeoutExpired:
            return STATUS_UNKNOWN
        combined = f"{proc.stdout}\n{proc.stderr}"
        outcome = classify_output(combined, proc.returncode)
        if outcome == AUTH_REQUIRED:
            return STATUS_AUTH_REQUIRED
        if proc.returncode != 0:
            return STATUS_UNAVAILABLE
        return STATUS_AVAILABLE

    def run(self, prompt: str, timeout: int = 900) -> ProviderResult:
        assert_no_paid_fallback(self.allow_paid)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                self._command(prompt), capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError:
            return ProviderResult(
                self.name, COMMAND_FAILED, model=self.model,
                error=f"{self.executable} not found on PATH",
            )
        except subprocess.TimeoutExpired:
            return ProviderResult(
                self.name, TIMEOUT, model=self.model,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=f"exceeded {timeout}s",
            )
        elapsed = int((time.monotonic() - started) * 1000)
        combined = f"{proc.stdout}\n{proc.stderr}"
        outcome = classify_output(combined, proc.returncode)
        return ProviderResult(
            provider=self.name,
            outcome=outcome,
            text=proc.stdout.strip(),
            model=self.model,
            duration_ms=elapsed,
            error=None if outcome == SUCCESS else proc.stderr.strip()[:2000],
            raw=combined,
        )


class ClaudeCodeProvider(CliProvider):
    name = "claude_code"

    def __init__(self, executable: str = "claude", **kwargs):
        super().__init__(executable, **kwargs)

    def _command(self, prompt: str) -> list[str]:
        cmd = [self._resolve(), "-p", prompt]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def _status_command(self) -> list[str]:
        return [self._resolve(), "--version"]


class CodexProvider(CliProvider):
    """Codex CLI.

    The model is passed explicitly on every call: this machine's global
    ~/.codex/config.toml pins a model the ChatGPT-account API rejects, and we
    do not mutate the user's global config to work around it.
    """

    name = "codex"

    def __init__(self, executable: str = "codex", model: str = "gpt-5.6-terra", **kwargs):
        super().__init__(executable, model=model, **kwargs)

    def _command(self, prompt: str) -> list[str]:
        return [
            self._resolve(), "exec", "--skip-git-repo-check",
            "-m", self.model, prompt,
        ]

    def _status_command(self) -> list[str]:
        return [self._resolve(), "login", "status"]
