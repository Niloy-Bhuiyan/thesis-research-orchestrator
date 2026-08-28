"""Budgets that stop the autonomous loop from running away.

Every limit is checked against state read from SQLite rather than from memory,
so budgets survive a daemon restart: killing and relaunching the process must
not hand the agent a fresh allowance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class Budgets:
    max_retries_per_run: int = 3
    max_consecutive_failures: int = 5
    max_experiments_per_session: int = 20
    max_session_hours: float = 12.0
    max_provider_calls: int = 200
    cooldown_minutes_after_failures: int = 15


@dataclass
class BudgetVerdict:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


OK = BudgetVerdict(True)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def check_retry(retry_count: int, budgets: Budgets) -> BudgetVerdict:
    if retry_count >= budgets.max_retries_per_run:
        return BudgetVerdict(
            False, f"retry budget exhausted ({retry_count}/{budgets.max_retries_per_run})"
        )
    return OK


def check_session(state, budgets: Budgets, now: datetime | None = None) -> BudgetVerdict:
    """Session-wide limits, evaluated against the persisted daemon row."""
    now = now or datetime.now(timezone.utc)

    if state["consecutive_failures"] >= budgets.max_consecutive_failures:
        return BudgetVerdict(
            False,
            f"{state['consecutive_failures']} consecutive failures "
            f"(limit {budgets.max_consecutive_failures})",
        )

    if state["experiments_this_session"] >= budgets.max_experiments_per_session:
        return BudgetVerdict(
            False,
            f"session experiment limit reached "
            f"({budgets.max_experiments_per_session})",
        )

    if state["provider_calls_count"] >= budgets.max_provider_calls:
        return BudgetVerdict(
            False, f"provider call budget reached ({budgets.max_provider_calls})"
        )

    started = _parse(state["session_started_at"])
    if started is not None:
        elapsed = now - started
        if elapsed >= timedelta(hours=budgets.max_session_hours):
            return BudgetVerdict(
                False,
                f"session wall clock limit reached "
                f"({elapsed.total_seconds() / 3600:.1f}h of "
                f"{budgets.max_session_hours}h)",
            )

    return OK


def cooldown_remaining(
    last_failure_at: str | None, budgets: Budgets, now: datetime | None = None
) -> timedelta:
    """How long to wait after repeated failures. Zero when clear to proceed."""
    failed_at = _parse(last_failure_at)
    if failed_at is None:
        return timedelta(0)
    now = now or datetime.now(timezone.utc)
    elapsed = now - failed_at
    window = timedelta(minutes=budgets.cooldown_minutes_after_failures)
    return max(timedelta(0), window - elapsed)
