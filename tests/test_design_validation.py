"""Candidate geometry must be checked before touching the working revision."""

from pathlib import Path

import pytest

from cad.design_validation import _corner_overrides, validate_candidate
from cad.params import ParameterInfo


PLATE = 'from cad import Part\ndef build():\n    p = Part("plate")\n    p.box(40, 30, 5)\n    return p\n'
CROSSED = '''from cad import Part, Parameter
a = Parameter("a", 10, min=5, max=15)
b = Parameter("b", 10, min=5, max=15)
def build():
    p = Part("plate")
    p.box(30, a - b + 5, 3)
    return p
'''


def project(root: Path):
    (root / "cad.toml").write_text('[project]\nentry = "model.py"\n')
    (root / "model.py").write_text(PLATE)
    state = root / ".cad"
    state.mkdir()
    (state / "HEAD").write_text("7")
    return root


def test_valid_candidate_leaves_live_project_and_cwd_unchanged(tmp_path):
    root = project(tmp_path)
    cwd = Path.cwd()
    result = validate_candidate(root, PLATE)
    assert result["ok"], result
    assert result["checks"][0]["parts"]["plate"]["solids"] == 1
    assert (root / "model.py").read_text() == PLATE
    assert (root / ".cad" / "HEAD").read_text() == "7"
    assert Path.cwd() == cwd


def test_snapshot_preserves_imports_assets_and_overlay_without_live_writes(tmp_path):
    root = project(tmp_path)
    (root / "helper.py").write_text("HEIGHT = 3\n")
    (root / "input.dat").write_text("20")
    (root / "params.toml").write_text('[parameters]\nwidth = 42\n')
    source = '''from pathlib import Path
from cad import Part, Parameter
from helper import HEIGHT
width = Parameter("width", 10)
depth = float(Path("input.dat").read_text())
Path("output.txt").write_text(str(Path.cwd()))
Path("helper.py").write_text("HEIGHT = 100")
def build():
    p = Part("plate")
    p.box(width, depth, HEIGHT)
    return p
'''
    result = validate_candidate(root, source)
    assert result["ok"], result
    assert result["checks"][0]["parts"]["plate"]["size_mm"] == pytest.approx([42, 20, 3])
    assert result["summary"]["skipped_parameters"] == ["width"]
    assert not (root / "output.txt").exists()
    assert (root / "helper.py").read_text() == "HEIGHT = 3\n"


@pytest.mark.parametrize("empty", ["{}", "[]", "()"])
def test_intentionally_empty_candidate_is_valid(tmp_path, empty):
    result = validate_candidate(project(tmp_path), f"def build():\n    return {empty}\n", message="clear the board")
    assert result["ok"], result
    assert result["summary"]["empty_model"]
    assert result["checks"][0]["parts"] == {}


@pytest.mark.parametrize("source", ["", "def build():\n return None", "def build(:"])
def test_missing_or_invalid_candidate_is_not_published(tmp_path, source):
    root = project(tmp_path)
    result = validate_candidate(root, source)
    assert not result["ok"]
    assert result["error"]["message"]
    assert (root / "model.py").read_text() == PLATE
    assert (root / ".cad" / "HEAD").read_text() == "7"


def test_crossed_parameter_bounds_are_actionable_and_block_design_change(tmp_path):
    result = validate_candidate(project(tmp_path), CROSSED, message="build a parameterized bracket")
    assert not result["ok"], result
    assert result["checks"][0]["ok"]
    failures = result["error"]["details"]["failed_combinations"]
    assert any(f["parameters"] == {"a": 5, "b": 15} for f in failures)
    assert result["summary"]["possible_corners"] == 4
    assert result["summary"]["tested_corners"] == 4


def test_corner_failure_is_advisory_for_unrelated_edit(tmp_path):
    result = validate_candidate(project(tmp_path), CROSSED, message="change the color to blue")
    assert result["ok"], result
    assert result["warning"]["code"] == "PARAMETER_COMBINATION_FAILED"


def test_followup_preserves_strict_range_validation(tmp_path):
    result = validate_candidate(project(tmp_path), CROSSED, message="keep going")
    assert not result["ok"]
    assert result["error"]["code"] == "PARAMETER_COMBINATION_FAILED"


def test_variants_start_with_pristine_helpers_and_inputs(tmp_path):
    root = project(tmp_path)
    (root / "helper.py").write_text("HEIGHT = 3\n")
    source = '''from pathlib import Path
from cad import Part, Parameter
from helper import HEIGHT
w = Parameter("width", 10, min=5, max=15)
assert not Path("from_previous_build.txt").exists()
Path("from_previous_build.txt").write_text("built")
Path("helper.py").write_text("HEIGHT = -1")
def build():
    p = Part("plate")
    p.box(w, 10, HEIGHT)
    return p
'''
    result = validate_candidate(root, source)
    assert result["ok"], result
    assert len(result["checks"]) == 3
    assert all(c["parts"]["plate"]["size_mm"][2] == pytest.approx(3) for c in result["checks"])


def test_many_parameters_have_deterministic_bounded_crossed_samples():
    parameters = {f"p{i}": ParameterInfo(f"p{i}", 5, 5, min=1, max=10) for i in range(8)}
    samples, coverage = _corner_overrides(parameters, {"material": "PETG"})
    assert len(samples) <= 12
    assert (samples, coverage) == _corner_overrides(parameters, {"material": "PETG"})
    assert coverage["possible_corners"] == 256
    assert coverage["endpoint_pairs_covered"]
    assert all(s["material"] == "PETG" for s in samples)
    assert any(s["p0"] == 1 and s["p1"] == 10 for s in samples)


def test_mentioned_parameter_is_prioritized_when_parameter_budget_is_exceeded():
    parameters = {f"p{i}": ParameterInfo(f"p{i}", 5, 5, min=1, max=10) for i in range(12)}
    _, coverage = _corner_overrides(parameters, {}, focus="increase p9")
    assert coverage["parameters"][0] == "p9"
    assert len(coverage["parameters"]) == 8
    assert len(coverage["skipped_parameters"]) == 4


def test_corner_bed_overflow_is_advisory_and_reported_at_failing_setting(tmp_path):
    source = '''from cad import Part, Parameter
w = Parameter("width", 40, min=20, max=350)
def build():
    p = Part("plate")
    p.box(w, 20, 3)
    return p
'''
    result = validate_candidate(project(tmp_path), source, message="build a parameterized tray")
    assert result["ok"], result
    corner = next(c for c in result["checks"] if c["parameters"].get("width") == 350)
    assert not corner["parts"]["plate"]["print"]["bed"]["fits"]
    assert any(w["code"] == "BED_OVERFLOW" for w in corner["warnings"])


def test_timeout_is_reported_and_original_unchanged(tmp_path, monkeypatch):
    import cad.design_validation as validation

    root = project(tmp_path)
    monkeypatch.setattr(validation, "VALIDATION_TIMEOUT", 0.2)
    result = validate_candidate(root, "import time\ntime.sleep(30)\ndef build():\n return {}")
    assert result["error"]["code"] == "VALIDATION_TIMEOUT"
    assert (root / "model.py").read_text() == PLATE


def test_symlink_input_is_copied_before_candidate_writes(tmp_path):
    root = project(tmp_path)
    target = tmp_path / "original.txt"
    target.write_text("untouched")
    (root / "linked.txt").symlink_to(target)
    result = validate_candidate(root, 'from pathlib import Path\nPath("linked.txt").write_text("changed")\ndef build():\n return {}')
    assert result["ok"], result
    assert target.read_text() == "untouched"


def test_recursive_symlink_is_rejected_without_following_forever(tmp_path):
    root = project(tmp_path)
    (root / "recursive").symlink_to(root, target_is_directory=True)
    result = validate_candidate(root, PLATE)
    assert not result["ok"]
    assert "recursive" in result["error"]["message"]


def test_reference_body_and_disconnected_solids_are_not_blanket_rejected(tmp_path):
    source = '''from cad import Part
def build():
    one = Part("one")
    one.box(3, 3, 3)
    one.box(3, 3, 3, at=(20, 0, 0))
    envelope = Part("envelope", reference=True)
    envelope.box(20, 20, 20)
    return {"one": one, "envelope": envelope}
'''
    result = validate_candidate(project(tmp_path), source)
    assert result["ok"], result
    assert result["checks"][0]["parts"]["envelope"]["reference"]
    assert "print" not in result["checks"][0]["parts"]["envelope"]
    assert result["checks"][0]["parts"]["one"]["solids"] == 2
    assert any(w["code"] == "MULTIPLE_SOLIDS" for w in result["checks"][0]["warnings"])


@pytest.mark.parametrize("message,ok", [
    ("Build a cable dock", False),
    ("Create a new empty model", True),
    ("Clear the board", True),
    ("Explain how to build a cable dock", True),
    ("Review the model", True),
    ("", True),
])
def test_empty_model_respects_explicit_construction_intent(tmp_path, message, ok):
    result = validate_candidate(project(tmp_path), "def build():\n return {}\n", message=message)
    assert result["ok"] is ok, result
    if not ok:
        assert result["error"]["code"] == "DESIGN_INTENT_FAILED"


@pytest.mark.parametrize("message,ok", [
    ("Build one connected solid bracket", False),
    ("Make a single solid body", False),
    ("Build an assembly with separate parts", True),
    ("Build an assembly with one connected solid base and a separate lid", True),
    ("Review whether this should be one connected solid", True),
])
def test_connectivity_only_enforces_explicit_whole_model_requirement(tmp_path, message, ok):
    source = '''from cad import Part
def build():
    p = Part("two_blocks")
    p.box(3, 3, 3)
    p.box(3, 3, 3, at=(20, 0, 0))
    return p
'''
    result = validate_candidate(project(tmp_path), source, message=message)
    assert result["ok"] is ok, result
    if not ok:
        assert result["error"]["code"] == "DESIGN_INTENT_FAILED"


def test_connectivity_requirement_applies_to_parameter_corners(tmp_path):
    source = '''from cad import Part, Parameter
gap = Parameter("gap", 0, min=0, max=20)
def build():
    p = Part("blocks")
    p.box(5, 5, 5)
    p.box(5, 5, 5, at=(gap, 0, 0))
    return p
'''
    result = validate_candidate(project(tmp_path), source, message="Build one connected solid bracket")
    assert not result["ok"], result
    assert result["checks"][0]["ok"]
    assert result["error"]["code"] == "PARAMETER_COMBINATION_FAILED"
    failed = result["error"]["details"]["failed_combinations"]
    assert failed[0]["parameters"] == {"gap": 20}
    assert failed[0]["error"]["code"] == "DESIGN_INTENT_FAILED"


def test_noisy_process_failure_returns_only_bounded_stderr_tail(tmp_path):
    source = '''import os
os.write(2, b"x" * 100000)
os.write(2, b"last useful diagnostic")
os._exit(3)
'''
    result = validate_candidate(project(tmp_path), source)
    assert not result["ok"]
    error = result["error"]
    assert error["code"] == "VALIDATION_PROCESS_FAILED"
    assert len(error["details"]["stderr"]) <= 2000
    assert error["details"]["stderr"].endswith("last useful diagnostic")


def test_cancellation_stops_worker_without_waiting_for_timeout(tmp_path):
    import time

    root = project(tmp_path)
    start = time.monotonic()
    result = validate_candidate(root, "import time\ntime.sleep(30)\ndef build():\n return {}",
                                cancelled=lambda: time.monotonic() - start > 0.5)
    assert not result["ok"]
    assert result["error"]["code"] == "VALIDATION_CANCELLED"
    assert time.monotonic() - start < 5
    assert (root / "model.py").read_text() == PLATE


def test_already_cancelled_validation_does_not_start_a_subprocess(tmp_path, monkeypatch):
    import cad.design_validation as validation

    def unexpected_process(*args, **kwargs):
        pytest.fail("cancelled validation must not execute candidate code")

    monkeypatch.setattr(validation.subprocess, "Popen", unexpected_process)
    result = validate_candidate(project(tmp_path), PLATE, cancelled=lambda: True)
    assert result["error"]["code"] == "VALIDATION_CANCELLED"
