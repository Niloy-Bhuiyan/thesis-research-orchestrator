"""Configuration.

Secrets are never stored here. Config holds *paths* to secret files, which are
read at use time and never logged, so a config file is always safe to commit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

CONFIG_NAME = "researchos.yaml"
DEFAULT_DB = "data/state.sqlite3"


@dataclass
class ProviderSettings:
    order: list[str] = field(default_factory=lambda: ["codex", "claude_code"])
    codex_model: str = "gpt-5.6-terra"
    claude_model: str | None = None
    allow_paid_fallback: bool = False
    timeout_seconds: int = 900


@dataclass
class KaggleSettings:
    enable_gpu: bool = True
    enable_internet: bool = True
    accelerator: str | None = None
    run_timeout_seconds: int = 32400  # Kaggle's 9h ceiling for GPU sessions
    poll_interval_seconds: int = 120


@dataclass
class TelegramSettings:
    enabled: bool = True
    token_file: str = ".secrets/telegram_token"
    allowlist_file: str = ".secrets/telegram_allowlist"
    poll_timeout_seconds: int = 30


@dataclass
class BudgetSettings:
    max_retries_per_run: int = 3
    max_consecutive_failures: int = 5
    max_experiments_per_session: int = 20
    max_session_hours: float = 12.0
    max_provider_calls: int = 200
    cooldown_minutes_after_failures: int = 15


@dataclass
class Settings:
    workspace_root: str
    active_project: str | None = None
    database: str = DEFAULT_DB
    providers: ProviderSettings = field(default_factory=ProviderSettings)
    kaggle: KaggleSettings = field(default_factory=KaggleSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    budgets: BudgetSettings = field(default_factory=BudgetSettings)

    @property
    def root(self) -> Path:
        return Path(self.workspace_root)

    @property
    def db_path(self) -> Path:
        return self.root / self.database

    def secret_path(self, relative: str) -> Path:
        return self.root / relative

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(
            workspace_root=data.get("workspace_root", str(Path(path).parent)),
            active_project=data.get("active_project"),
            database=data.get("database", DEFAULT_DB),
            providers=ProviderSettings(**(data.get("providers") or {})),
            kaggle=KaggleSettings(**(data.get("kaggle") or {})),
            telegram=TelegramSettings(**(data.get("telegram") or {})),
            budgets=BudgetSettings(**(data.get("budgets") or {})),
        )

    @classmethod
    def discover(cls, start: str | Path | None = None) -> "Settings":
        """Find researchos.yaml walking up from `start`, else use defaults."""
        current = Path(start or Path.cwd()).resolve()
        for directory in (current, *current.parents):
            candidate = directory / CONFIG_NAME
            if candidate.is_file():
                return cls.load(candidate)
        return cls(workspace_root=str(current))

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(yaml.safe_dump(asdict(self), sort_keys=False), encoding="utf-8")
        return path


def is_within_workspace(settings: Settings, target: str | Path) -> bool:
    """Machine safety: reject operations outside the configured workspace.

    The daemon runs shell commands and edits files, so every path it is asked
    to touch is checked against this before anything happens.
    """
    try:
        root = settings.root.resolve()
        candidate = Path(target).resolve()
    except OSError:
        return False
    return candidate == root or root in candidate.parents
