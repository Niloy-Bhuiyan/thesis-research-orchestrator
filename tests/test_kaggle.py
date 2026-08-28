import json

import pytest

from researchos.kaggle.runner import (
    KaggleError,
    KaggleRunner,
    KernelSpec,
    QuotaUnavailable,
    build_notebook,
    looks_like_quota_exhaustion,
    parse_metrics,
    stage_package,
)


class FakeRunner(KaggleRunner):
    """KaggleRunner with the CLI seam replaced. No network, no GPU quota."""

    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = []

    def _invoke(self, args, timeout=None):
        self.calls.append(args)
        for key, value in self.responses.items():
            if key in " ".join(args):
                return value
        return (0, "")


def spec(**kw):
    base = dict(slug="exp-0001", title="EXP-0001", username="niloybhuiyan",
                code_file="exp.ipynb")
    base.update(kw)
    return KernelSpec(**base)


# ---------------- metadata ----------------


def test_metadata_matches_kaggle_template_fields():
    """Field names come from `kaggle kernels init`; drift breaks push."""
    meta = spec().to_metadata()
    assert set(meta) == {
        "id", "title", "code_file", "language", "kernel_type", "is_private",
        "enable_gpu", "enable_tpu", "enable_internet", "machine_shape",
        "dataset_sources", "competition_sources", "kernel_sources", "model_sources",
    }


def test_booleans_are_lowercase_strings():
    meta = spec(enable_gpu=True, is_private=True, enable_internet=False).to_metadata()
    assert meta["enable_gpu"] == "true"
    assert meta["is_private"] == "true"
    assert meta["enable_internet"] == "false"


def test_ref_is_username_slash_slug():
    assert spec().ref == "niloybhuiyan/exp-0001"


def test_defaults_are_gpu_on_internet_on_private():
    s = spec()
    assert (s.enable_gpu, s.enable_internet, s.is_private) == (True, True, True)


# ---------------- packaging ----------------


def test_notebook_is_valid_nbformat_4():
    nb = build_notebook(["print(1)", "print(2)"])
    assert nb["nbformat"] == 4
    assert len(nb["cells"]) == 2
    assert nb["cells"][0]["cell_type"] == "code"


def test_stage_package_writes_notebook_and_metadata(tmp_path):
    src = tmp_path / "train.py"
    src.write_text("print('train')", encoding="utf-8")
    out = stage_package(tmp_path / "pkg", spec(), source_file=src)
    meta = json.loads((out / "kernel-metadata.json").read_text())
    nb = json.loads((out / "exp.ipynb").read_text())
    assert meta["id"] == "niloybhuiyan/exp-0001"
    assert "print('train')" in "".join(nb["cells"][0]["source"])


def test_stage_package_script_mode_copies_source_verbatim(tmp_path):
    src = tmp_path / "train.py"
    src.write_text("x = 1\n", encoding="utf-8")
    out = stage_package(
        tmp_path / "pkg", spec(kernel_type="script", code_file="train.py"), source_file=src
    )
    assert (out / "train.py").read_text() == "x = 1\n"


def test_notebook_without_source_or_cells_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        stage_package(tmp_path / "pkg", spec())


def test_extra_files_are_included(tmp_path):
    src = tmp_path / "train.py"
    src.write_text("pass", encoding="utf-8")
    cfg = tmp_path / "baseline.yaml"
    cfg.write_text("lr: 0.001", encoding="utf-8")
    out = stage_package(tmp_path / "pkg", spec(), source_file=src, extra_files=[cfg])
    assert (out / "baseline.yaml").read_text() == "lr: 0.001"


# ---------------- status parsing ----------------


@pytest.mark.parametrize(
    "worker,expected",
    [
        ("COMPLETE", "complete"),
        ("ERROR", "error"),
        ("RUNNING", "running"),
        ("QUEUED", "submitted"),
        ("CANCEL_ACKNOWLEDGED", "cancelled"),
    ],
)
def test_status_maps_kaggle_worker_states(worker, expected):
    runner = FakeRunner({"kernels status": (0, f'ref has status "KernelWorkerStatus.{worker}"')})
    assert runner.status("u/k") == expected


def test_unrecognised_status_is_unknown_not_a_guess():
    runner = FakeRunner({"kernels status": (0, 'has status "KernelWorkerStatus.WEIRD_NEW_STATE"')})
    assert runner.status("u/k") == "unknown"


def test_status_failure_raises(tmp_path):
    runner = FakeRunner({"kernels status": (1, "404 not found")})
    with pytest.raises(KaggleError):
        runner.status("u/k")


# ---------------- quota handling ----------------


@pytest.mark.parametrize(
    "text",
    [
        "You have exceeded your GPU quota for this week",
        "quota exhausted",
        "No GPU hours remaining",
    ],
)
def test_quota_exhaustion_detected(text):
    assert looks_like_quota_exhaustion(text)


def test_normal_error_is_not_mistaken_for_quota():
    assert not looks_like_quota_exhaustion("ValueError: shape mismatch")


def test_push_raises_quota_unavailable_not_generic_error(tmp_path):
    runner = FakeRunner({"kernels push": (1, "You have exceeded your GPU quota")})
    with pytest.raises(QuotaUnavailable):
        runner.push(tmp_path)


def test_push_failure_is_kaggle_error(tmp_path):
    runner = FakeRunner({"kernels push": (1, "invalid metadata")})
    with pytest.raises(KaggleError):
        runner.push(tmp_path)


def test_push_passes_accelerator_and_timeout(tmp_path):
    runner = FakeRunner({"kernels push": (0, "ok")})
    runner.push(tmp_path, run_timeout=3600, accelerator="nvidiaTeslaT4")
    args = " ".join(runner.calls[0])
    assert "-t 3600" in args
    assert "--accelerator nvidiaTeslaT4" in args


# ---------------- metrics ----------------


def test_parse_metrics_keeps_numbers_only(tmp_path):
    p = tmp_path / "metrics.json"
    p.write_text(json.dumps({"E_AURC": 0.31, "epochs": 10, "notes": "n/a", "ok": True}))
    assert parse_metrics(p) == {"E_AURC": 0.31, "epochs": 10.0}


def test_missing_metric_stays_absent_rather_than_zero(tmp_path):
    p = tmp_path / "metrics.json"
    p.write_text(json.dumps({"accuracy": 0.9}))
    assert "E_AURC" not in parse_metrics(p)
