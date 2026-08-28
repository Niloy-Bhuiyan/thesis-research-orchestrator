"""Provider router with structured handoff.

Claude and Codex never read each other's conversation transcripts. When one
provider is exhausted, the router passes a `Handoff` - a self-contained record
of what is known about the experiment - to the next provider, so continuing
work does not depend on chat history that no longer exists.

If every provider is exhausted the loop pauses and the human is notified. It
never escalates to a paid API on its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

from .base import (
    FAILOVER_OUTCOMES,
    Provider,
    ProviderResult,
    new_call_id,
)


@dataclass
class Handoff:
    """Everything a fresh provider needs to continue, with no transcript."""

    project_id: str
    experiment_id: str
    status: str
    parent_experiment: str | None = None
    failure_class: str | None = None
    diagnosis: str | None = None
    methodology_version: str | None = None
    git_sha: str | None = None
    last_good_sha: str | None = None
    primary_metric: float | None = None
    proposed_change: str | None = None
    scientific_impact: str | None = None
    next_action: str | None = None
    editable_files: list[str] = field(default_factory=list)
    locked_files: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    def as_prompt(self, task: str) -> str:
        return (
            f"{task}\n\n"
            "You are continuing an experiment. All context you have is the "
            "structured state below; there is no prior conversation.\n\n"
            f"```json\n{self.to_json()}\n```\n"
        )


class AllProvidersExhausted(RuntimeError):
    def __init__(self, attempts: list[ProviderResult]):
        self.attempts = attempts
        summary = ", ".join(f"{a.provider}={a.outcome}" for a in attempts)
        super().__init__(f"every provider is unavailable ({summary})")


class ProviderRouter:
    def __init__(
        self,
        providers: Sequence[Provider],
        store=None,
        on_exhausted: Callable[[list[ProviderResult]], None] | None = None,
    ):
        if not providers:
            raise ValueError("router needs at least one provider")
        self.providers = list(providers)
        self.store = store
        self.on_exhausted = on_exhausted

    @property
    def order(self) -> list[str]:
        return [p.name for p in self.providers]

    def statuses(self) -> dict[str, str]:
        return {p.name: p.status() for p in self.providers}

    def run(
        self,
        prompt: str,
        timeout: int = 900,
        experiment_id: str | None = None,
        task: str = "generic",
        prefer: str | None = None,
    ) -> ProviderResult:
        """Try providers in order until one succeeds.

        `prefer` moves a named provider to the front for this call only.
        """
        providers = list(self.providers)
        if prefer:
            providers.sort(key=lambda p: p.name != prefer)

        attempts: list[ProviderResult] = []
        for provider in providers:
            result = provider.run(prompt, timeout=timeout)
            self._record(result, experiment_id, task)
            attempts.append(result)
            if result.ok:
                return result
            if result.outcome not in FAILOVER_OUTCOMES:
                # A non-failover outcome (e.g. invalid output) is this
                # provider's answer, not a reason to burn the next one.
                return result

        if self.on_exhausted:
            self.on_exhausted(attempts)
        if self.store is not None:
            self.store.pause("all providers exhausted")
        raise AllProvidersExhausted(attempts)

    def _record(self, result: ProviderResult, experiment_id: str | None, task: str) -> None:
        if self.store is None:
            return
        from ..state.db import utcnow

        self.store.conn.execute(
            "INSERT INTO provider_calls (id, experiment_id, provider, model, task,"
            " outcome, error_class, duration_ms, started_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_call_id(),
                experiment_id,
                result.provider,
                result.model,
                task,
                result.outcome,
                None if result.ok else result.outcome,
                result.duration_ms,
                utcnow(),
                utcnow(),
            ),
        )
