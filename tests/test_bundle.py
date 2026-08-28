import json
import zipfile

import pytest

from researchos.kaggle.bundle import (
    MANIFEST_NAME,
    RESULTS_NAME,
    BundleError,
    ImportResult,
    ManifestMismatch,
    RunManifest,
    config_hash,
    create_bundle,
    import_results,
    read_bundle_manifest,
)


@pytest.fixture
def manifest():
    return RunManifest(
        experiment_id="EXP-0038",
        project_id="thesis",
        git_sha="abc123",
        dataset="niloybhuiyan/thesis-data",
        accelerator="GPU T4 x2",
        internet=True,
        seeds=[11, 22, 33],
        primary_metric="E_AURC",
        estimated_runtime="~6 GPU-hours",
        config_hash="deadbeef",
    )


@pytest.fixture
def code_file(tmp_path):
    p = tmp_path / "exp.ipynb"
    p.write_text(json.dumps({"cells": []}), encoding="utf-8")
    return p


def make_return_archive(tmp_path, manifest, metrics, name="returned.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(MANIFEST_NAME, manifest.to_json())
        archive.writestr(RESULTS_NAME, json.dumps(metrics))
    return path


# ---------------- creating ----------------


def test_bundle_contains_manifest_readme_and_code(tmp_path, manifest, code_file):
    out = create_bundle(tmp_path / "EXP-0038-run-bundle.zip", manifest, code_file)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    assert MANIFEST_NAME in names
    assert "README.md" in names
    assert "exp.ipynb" in names


def test_readme_states_requirements(tmp_path, manifest, code_file):
    out = create_bundle(tmp_path / "b.zip", manifest, code_file)
    with zipfile.ZipFile(out) as archive:
        readme = archive.read("README.md").decode()
    assert "GPU T4 x2" in readme
    assert "Internet: ON" in readme
    assert "niloybhuiyan/thesis-data" in readme
    assert "~6 GPU-hours" in readme


def test_readme_forbids_changing_hyperparameters(tmp_path, manifest, code_file):
    out = create_bundle(tmp_path / "b.zip", manifest, code_file)
    with zipfile.ZipFile(out) as archive:
        readme = archive.read("README.md").decode()
    assert "Do not change hyperparameters" in readme


def test_extra_files_are_bundled(tmp_path, manifest, code_file):
    cfg = tmp_path / "baseline.yaml"
    cfg.write_text("lr: 0.001", encoding="utf-8")
    out = create_bundle(tmp_path / "b.zip", manifest, code_file, extra_files=[cfg])
    with zipfile.ZipFile(out) as archive:
        assert "baseline.yaml" in archive.namelist()


def test_missing_code_file_is_rejected(tmp_path, manifest):
    with pytest.raises(BundleError):
        create_bundle(tmp_path / "b.zip", manifest, tmp_path / "nope.ipynb")


def test_manifest_round_trips(tmp_path, manifest, code_file):
    out = create_bundle(tmp_path / "b.zip", manifest, code_file)
    loaded = read_bundle_manifest(out)
    assert loaded.experiment_id == "EXP-0038"
    assert loaded.seeds == [11, 22, 33]


# ---------------- config hashing ----------------


def test_config_hash_is_order_independent():
    assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})


def test_config_hash_changes_when_value_changes():
    assert config_hash({"lr": 0.001}) != config_hash({"lr": 0.01})


# ---------------- importing ----------------


def test_import_reads_metrics(tmp_path, manifest):
    path = make_return_archive(tmp_path, manifest, {"E_AURC": 0.29, "accuracy": 0.91})
    result = import_results(path)
    assert isinstance(result, ImportResult)
    assert result.experiment_id == "EXP-0038"
    assert result.metrics == {"E_AURC": 0.29, "accuracy": 0.91}


def test_import_validates_experiment_id(tmp_path, manifest):
    """A bundle for another experiment must not attach to this lineage."""
    path = make_return_archive(tmp_path, manifest, {"E_AURC": 0.29})
    with pytest.raises(ManifestMismatch):
        import_results(path, expected_experiment_id="EXP-0099")


def test_import_accepts_matching_experiment_id(tmp_path, manifest):
    path = make_return_archive(tmp_path, manifest, {"E_AURC": 0.29})
    assert import_results(path, expected_experiment_id="EXP-0038").metrics


def test_import_rejects_altered_config(tmp_path, manifest):
    """Results from a run that changed the config are not comparable."""
    path = make_return_archive(tmp_path, manifest, {"E_AURC": 0.29})
    with pytest.raises(ManifestMismatch):
        import_results(path, expected_config_hash="different")


def test_import_finds_files_nested_in_kaggle_output_dirs(tmp_path, manifest):
    path = tmp_path / "nested.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"kaggle/working/{MANIFEST_NAME}", manifest.to_json())
        archive.writestr(f"kaggle/working/{RESULTS_NAME}", json.dumps({"E_AURC": 0.3}))
    assert import_results(path).metrics == {"E_AURC": 0.3}


def test_import_without_manifest_is_rejected(tmp_path):
    path = tmp_path / "bare.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(RESULTS_NAME, json.dumps({"E_AURC": 0.3}))
    with pytest.raises(BundleError):
        import_results(path)


def test_import_without_metrics_is_rejected(tmp_path, manifest):
    path = tmp_path / "nometrics.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(MANIFEST_NAME, manifest.to_json())
    with pytest.raises(BundleError):
        import_results(path)


def test_import_rejects_results_with_no_numeric_metrics(tmp_path, manifest):
    path = make_return_archive(tmp_path, manifest, {"status": "finished"})
    with pytest.raises(BundleError):
        import_results(path)


def test_non_numeric_metrics_are_dropped_with_a_warning(tmp_path, manifest):
    path = make_return_archive(tmp_path, manifest, {"E_AURC": 0.29, "note": "ok"})
    result = import_results(path)
    assert result.metrics == {"E_AURC": 0.29}
    assert any("note" in w for w in result.warnings)


def test_booleans_are_not_treated_as_metrics(tmp_path, manifest):
    path = make_return_archive(tmp_path, manifest, {"E_AURC": 0.29, "converged": True})
    assert "converged" not in import_results(path).metrics


def test_missing_primary_metric_warns_but_imports(tmp_path, manifest):
    path = make_return_archive(tmp_path, manifest, {"accuracy": 0.9})
    result = import_results(path)
    assert any("E_AURC" in w for w in result.warnings)
    assert result.metrics == {"accuracy": 0.9}


def test_missing_bundle_file_is_rejected(tmp_path):
    with pytest.raises(BundleError):
        import_results(tmp_path / "absent.zip")
