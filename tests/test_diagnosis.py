import pytest

from researchos.diagnosis.classifier import (
    CONFIGURATION,
    EXPERIMENTAL_VALIDITY,
    IMPLEMENTATION,
    INFRASTRUCTURE,
    classify,
    summarize_for_human,
)

OOM_LOG = """
Epoch 3/50  loss=0.412
Traceback (most recent call last):
  File "/kaggle/working/train.py", line 88, in validate
    logits = model(batch)
torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.20 GiB
"""

SHAPE_LOG = """
RuntimeError: mat1 and mat2 shapes cannot be multiplied (32x768 and 512x256)
"""

DEVICE_LOG = """
RuntimeError: Expected all tensors to be on the same device, but found at least
two devices, cuda:0 and cpu!
"""


def test_gpu_oom_is_infrastructure_and_auto_applicable():
    d = classify(OOM_LOG)
    assert d.failure_class == INFRASTRUCTURE
    assert d.subclass == "gpu_oom"
    assert d.confidence >= 0.9
    assert d.scientific_impact == "none_expected"
    assert d.auto_applicable


def test_oom_evidence_quotes_the_actual_line():
    d = classify(OOM_LOG)
    assert "CUDA out of memory" in d.evidence


def test_shape_mismatch_is_implementation():
    d = classify(SHAPE_LOG)
    assert d.failure_class == IMPLEMENTATION
    assert d.subclass == "shape_mismatch"


def test_device_mismatch_detected():
    assert classify(DEVICE_LOG).subclass == "device_mismatch"


def test_missing_package_detected():
    d = classify("ModuleNotFoundError: No module named 'timm'")
    assert d.subclass == "missing_package"
    assert d.auto_applicable


def test_timeout_detected():
    d = classify("Your kernel exceeded the maximum runtime of 12 hours")
    assert d.failure_class == INFRASTRUCTURE
    assert d.subclass == "timeout"


def test_disk_full_detected():
    assert classify("OSError: [Errno 28] No space left on device").subclass == "disk_full"


def test_network_failure_detected():
    d = classify("requests.exceptions.ConnectionError: Max retries exceeded with url")
    assert d.failure_class == INFRASTRUCTURE


def test_dataloader_worker_crash_is_configuration():
    d = classify("DataLoader worker (pid 231) is killed by signal: Killed")
    assert d.failure_class == CONFIGURATION
    assert d.subclass == "dataloader_workers"


def test_invalid_config_detected():
    d = classify("yaml.scanner.ScannerError: mapping values are not allowed here")
    assert d.subclass == "invalid_config"


# ---------------- safety rules ----------------


def test_leakage_risk_never_auto_applies():
    d = classify("WARNING: possible data leakage, train/test overlap detected")
    assert d.failure_class == EXPERIMENTAL_VALIDITY
    assert d.requires_approval
    assert not d.auto_applicable


def test_test_set_reference_requires_approval_even_though_low_confidence():
    d = classify("Selecting best checkpoint on test set accuracy")
    assert d.failure_class == EXPERIMENTAL_VALIDITY
    assert d.requires_approval


def test_reproducibility_concern_requires_approval():
    d = classify("Warning: non-deterministic algorithm selected")
    assert d.failure_class == EXPERIMENTAL_VALIDITY
    assert d.requires_approval


def test_possible_impact_blocks_auto_apply_despite_class():
    """NaN loss is an implementation issue but the fix changes optimisation."""
    d = classify("RuntimeError: loss became nan at step 400")
    assert d.scientific_impact == "possible"
    assert not d.auto_applicable


def test_low_confidence_never_auto_applies():
    d = classify("Warning: non-deterministic algorithm selected")
    assert d.confidence < 0.8
    assert not d.auto_applicable


# ---------------- fallbacks ----------------


def test_empty_log_is_unclassified_not_a_guess():
    d = classify("")
    assert d.subclass == "unclassified"
    assert d.confidence == 0.0
    assert d.requires_approval


def test_unrecognised_failure_escalates_rather_than_guessing():
    d = classify("Segmentation fault (core dumped) in libmysterious.so")
    assert d.subclass == "unclassified"
    assert "escalate" in d.proposed_action


def test_highest_confidence_rule_wins_when_several_match():
    """A specific OOM must beat the generic traceback text around it."""
    log = OOM_LOG + "\nFileNotFoundError: no such file or directory\n"
    assert classify(log).subclass == "gpu_oom"


# ---------------- human summary ----------------


def test_summary_states_impact_and_whether_approval_needed():
    text = summarize_for_human(classify(OOM_LOG), "EXP-0047", "3h 41m")
    assert "EXP-0047 failed after 3h 41m" in text
    assert "none_expected" in text
    assert "Applied automatically." in text


def test_summary_flags_approval_for_scientific_issues():
    d = classify("data leakage detected between splits")
    text = summarize_for_human(d, "EXP-0052", "1h")
    assert "Needs your approval." in text
