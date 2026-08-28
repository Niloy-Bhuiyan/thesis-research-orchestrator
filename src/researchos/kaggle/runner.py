"""Kaggle execution backend.

Source of truth for research code is normal .py/.yaml in git. This module is
only packaging: it turns a source file plus a config into the notebook or
script artifact Kaggle actually executes, pushes it, and reports status.

Every Kaggle call goes through `_invoke`, which tests replace. Nothing here
burns GPU quota during CI.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Kaggle reports worker state as e.g. 'has status "KernelWorkerStatus.COMPLETE"'.
_STATUS_RE = re.compile(r"KernelWorkerStatus\.([A-Z_]+)")

# Kaggle worker state -> our run status vocabulary (see state/schema.sql).
STATUS_MAP = {
    "QUEUED": "submitted",
    "RUNNING": "running",
    "COMPLETE": "complete",
    "ERROR": "error",
    "CANCEL_REQUESTED": "cancelled",
    "CANCEL_ACKNOWLEDGED": "cancelled",
}

_QUOTA_PATTERNS = [
    r"exceeded.*quota",
    r"quota.*(?:exceeded|exhausted)",
    r"no gpu (?:hours|quota) (?:remaining|left)",
    r"you have used all",
]


class KaggleError(RuntimeError):
    pass


class QuotaUnavailable(KaggleError):
    """GPU quota is gone. Triggers the external-run bundle path, not a retry."""


def looks_like_quota_exhaustion(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in _QUOTA_PATTERNS)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class KernelSpec:
    """Maps onto Kaggle's kernel-metadata.json, whose field names are fixed."""

    slug: str
    title: str
    username: str
    code_file: str
    kernel_type: str = "notebook"
    language: str = "python"
    is_private: bool = True
    enable_gpu: bool = True
    enable_tpu: bool = False
    enable_internet: bool = True
    machine_shape: str = ""
    dataset_sources: list[str] = field(default_factory=list)
    competition_sources: list[str] = field(default_factory=list)
    kernel_sources: list[str] = field(default_factory=list)
    model_sources: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.username}/{self.slug}"

    def to_metadata(self) -> dict:
        # Kaggle expects the booleans as lowercase strings, matching the
        # template emitted by `kaggle kernels init`.
        return {
            "id": self.ref,
            "title": self.title,
            "code_file": self.code_file,
            "language": self.language,
            "kernel_type": self.kernel_type,
            "is_private": str(self.is_private).lower(),
            "enable_gpu": str(self.enable_gpu).lower(),
            "enable_tpu": str(self.enable_tpu).lower(),
            "enable_internet": str(self.enable_internet).lower(),
            "machine_shape": self.machine_shape,
            "dataset_sources": self.dataset_sources,
            "competition_sources": self.competition_sources,
            "kernel_sources": self.kernel_sources,
            "model_sources": self.model_sources,
        }


def build_notebook(cells: list[str]) -> dict:
    """Minimal nbformat-4 notebook. Packaging only, never the editable source."""
    return {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": src.splitlines(keepends=True),
            }
            for src in cells
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def stage_package(
    dest: Path,
    spec: KernelSpec,
    cells: list[str] | None = None,
    source_file: Path | None = None,
    extra_files: list[Path] | None = None,
) -> Path:
    """Write the upload folder: code artifact + kernel-metadata.json."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    target = dest / spec.code_file
    target.parent.mkdir(parents=True, exist_ok=True)
    if spec.kernel_type == "notebook":
        if cells is None:
            if source_file is None:
                raise ValueError("notebook kernels need cells or a source_file")
            cells = [Path(source_file).read_text(encoding="utf-8")]
        target.write_text(json.dumps(build_notebook(cells), indent=1), encoding="utf-8")
    else:
        if source_file is None:
            raise ValueError("script kernels need a source_file")
        shutil.copyfile(source_file, target)

    for extra in extra_files or []:
        shutil.copyfile(extra, dest / Path(extra).name)

    (dest / "kernel-metadata.json").write_text(
        json.dumps(spec.to_metadata(), indent=2), encoding="utf-8"
    )
    return dest


class KaggleRunner:
    def __init__(self, executable: str = "kaggle", timeout: int = 600):
        self.executable = executable
        self.timeout = timeout

    def _resolve(self) -> str:
        return shutil.which(self.executable) or self.executable

    def _invoke(self, args: list[str], timeout: int | None = None) -> tuple[int, str]:
        """Single seam for every Kaggle CLI call. Tests override this."""
        proc = subprocess.run(
            [self._resolve(), *args],
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
        )
        return proc.returncode, f"{proc.stdout}\n{proc.stderr}"

    def whoami(self) -> str:
        code, out = self._invoke(["config", "view"])
        match = re.search(r"username:\s*(\S+)", out)
        if not match:
            raise KaggleError(f"could not determine Kaggle account: {out.strip()[:300]}")
        return match.group(1)

    def push(self, folder: Path, run_timeout: int | None = None,
             accelerator: str | None = None) -> str:
        args = ["kernels", "push", "-p", str(folder)]
        if run_timeout:
            args += ["-t", str(run_timeout)]
        if accelerator:
            args += ["--accelerator", accelerator]
        code, out = self._invoke(args)
        if looks_like_quota_exhaustion(out):
            raise QuotaUnavailable(out.strip()[:500])
        if code != 0:
            raise KaggleError(f"kernels push failed: {out.strip()[:500]}")
        return out.strip()

    def status(self, ref: str) -> str:
        """Current run status in our vocabulary, or 'unknown'."""
        code, out = self._invoke(["kernels", "status", ref])
        if looks_like_quota_exhaustion(out):
            raise QuotaUnavailable(out.strip()[:500])
        match = _STATUS_RE.search(out)
        if not match:
            if code != 0:
                raise KaggleError(f"kernels status failed: {out.strip()[:500]}")
            return "unknown"
        return STATUS_MAP.get(match.group(1), "unknown")

    def fetch_output(self, ref: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        code, out = self._invoke(["kernels", "output", ref, "-p", str(dest)])
        if code != 0:
            raise KaggleError(f"kernels output failed: {out.strip()[:500]}")
        return dest

    def pull(self, ref: str, dest: Path) -> Path:
        """Retrieve the executed notebook itself, including its outputs."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        code, out = self._invoke(["kernels", "pull", ref, "-p", str(dest), "-m"])
        if code != 0:
            raise KaggleError(f"kernels pull failed: {out.strip()[:500]}")
        return dest


def parse_metrics(path: Path) -> dict[str, float]:
    """Read metrics.json produced by the experiment.

    Only numeric scalars are accepted: a metric the run did not actually emit
    must stay absent rather than becoming a fabricated zero.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        k: float(v)
        for k, v in data.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
