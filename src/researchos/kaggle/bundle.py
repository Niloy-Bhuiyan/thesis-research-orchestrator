"""External manual run fallback.

When Kaggle GPU quota is gone, the experiment is packaged into a portable zip
that a person can run on their own account, and the results are imported back
afterwards. This is deliberately human-in-the-loop: we never automate a second
Kaggle account or handle anyone else's credentials.

The import path is the security-sensitive half. A returned bundle arrives from
outside the system, so it is validated before anything touches the database:
the experiment must exist, the manifest must match the experiment it claims to
be, and metrics must be numeric. A mismatched bundle is rejected rather than
silently attached to the wrong lineage.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "run_manifest.json"
RESULTS_NAME = "metrics.json"

INSTRUCTIONS = """# {experiment_id} - external run

This experiment could not run on the originating Kaggle account because GPU
quota was unavailable. It needs to be executed once on any Kaggle account.

## Requirements

- Accelerator: {accelerator}
- Internet: {internet}
- Dataset: {dataset}
- Estimated runtime: {runtime}

## Steps

1. Create a new Kaggle notebook.
2. Upload `{code_file}` from this bundle.
3. Attach the dataset listed above.
4. Enable the accelerator and internet setting listed above.
5. Run All, and wait for it to finish.
6. Download the notebook output, including `metrics.json`.
7. Return the output archive to the person who sent this bundle.

## Do not

- Do not change hyperparameters, the dataset, or the evaluation code.
  The results are only usable if the run matches the manifest in this bundle.
"""


class BundleError(Exception):
    pass


class ManifestMismatch(BundleError):
    """The returned bundle does not correspond to the experiment claimed."""


def config_hash(payload: dict) -> str:
    """Stable hash of a config, used to detect tampering on return."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class RunManifest:
    experiment_id: str
    project_id: str
    git_sha: str | None = None
    methodology_version: str | None = None
    config_hash: str | None = None
    dataset: str | None = None
    dataset_version: str | None = None
    accelerator: str = "GPU"
    internet: bool = True
    seeds: list[int] = field(default_factory=list)
    primary_metric: str | None = None
    estimated_runtime: str = "unknown"

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> RunManifest:
        return cls(**json.loads(text))


def create_bundle(
    dest: Path,
    manifest: RunManifest,
    code_file: Path,
    extra_files: list[Path] | None = None,
) -> Path:
    """Write EXP-xxxx-run-bundle.zip containing everything needed to re-run."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    code_file = Path(code_file)
    if not code_file.is_file():
        raise BundleError(f"code file not found: {code_file}")

    instructions = INSTRUCTIONS.format(
        experiment_id=manifest.experiment_id,
        accelerator=manifest.accelerator,
        internet="ON" if manifest.internet else "OFF",
        dataset=manifest.dataset or "none",
        runtime=manifest.estimated_runtime,
        code_file=code_file.name,
    )

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, manifest.to_json())
        archive.writestr("README.md", instructions)
        archive.write(code_file, code_file.name)
        for extra in extra_files or []:
            archive.write(extra, Path(extra).name)
    return dest


def read_bundle_manifest(path: Path) -> RunManifest:
    with zipfile.ZipFile(path) as archive:
        if MANIFEST_NAME not in archive.namelist():
            raise BundleError(f"{path.name} has no {MANIFEST_NAME}")
        return RunManifest.from_json(archive.read(MANIFEST_NAME).decode("utf-8"))


def _find_member(archive: zipfile.ZipFile, filename: str) -> str | None:
    """Locate a file anywhere in the archive; Kaggle nests output directories."""
    for name in archive.namelist():
        if Path(name).name == filename:
            return name
    return None


@dataclass
class ImportResult:
    experiment_id: str
    metrics: dict[str, float]
    manifest: RunManifest
    warnings: list[str] = field(default_factory=list)


def import_results(
    archive_path: Path,
    expected_experiment_id: str | None = None,
    expected_config_hash: str | None = None,
) -> ImportResult:
    """Validate and read a returned external run.

    Raises rather than importing anything questionable: attaching a foreign or
    altered result to the wrong experiment would corrupt the record silently,
    which is worse than a failed import.
    """
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise BundleError(f"no such bundle: {archive_path}")

    with zipfile.ZipFile(archive_path) as archive:
        manifest_member = _find_member(archive, MANIFEST_NAME)
        if manifest_member is None:
            raise BundleError(f"{archive_path.name} has no {MANIFEST_NAME}")
        manifest = RunManifest.from_json(archive.read(manifest_member).decode("utf-8"))

        if expected_experiment_id and manifest.experiment_id != expected_experiment_id:
            raise ManifestMismatch(
                f"bundle is for {manifest.experiment_id}, expected "
                f"{expected_experiment_id}"
            )

        warnings: list[str] = []
        if expected_config_hash and manifest.config_hash != expected_config_hash:
            raise ManifestMismatch(
                f"config hash mismatch for {manifest.experiment_id}: the run was "
                "not executed with the configuration this experiment specifies"
            )

        results_member = _find_member(archive, RESULTS_NAME)
        if results_member is None:
            raise BundleError(f"{archive_path.name} has no {RESULTS_NAME}")

        raw = json.loads(archive.read(results_member).decode("utf-8"))

    metrics = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            warnings.append(f"ignored non-numeric metric {key!r}")
            continue
        metrics[key] = float(value)

    if not metrics:
        raise BundleError("no numeric metrics found in the returned run")

    if manifest.primary_metric and manifest.primary_metric not in metrics:
        warnings.append(
            f"primary metric {manifest.primary_metric!r} absent from the results"
        )

    return ImportResult(manifest.experiment_id, metrics, manifest, warnings)
