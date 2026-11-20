"""Exercise the VM job entry point with a real OCCT build and opaque checkpoint."""
import json
import shutil
from pathlib import Path

from cad.isolated_job import execute


def test_double_ring_job_reopens_checks_both_interfaces_and_exports(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "samples/improved-fidget-spinners/duo/model.py").read_text()
    build = tmp_path / "build"
    build.mkdir()
    status, _, body = execute(build, {"action": "build", "args": {"source": source.replace("clearance = 0.35", "clearance = 0.38")}})
    assert status == 200, body
    for action, args in [("clearance", {}), ("export", {"format": "3mf", "mode": "multimaterial"})]:
        work = tmp_path / action
        work.mkdir()
        shutil.copyfile(build / "candidate.tar.gz", work / "project.tar.gz")
        status, mime, data = execute(work, {"action": action, "args": args})
        assert status == 200, data[:500]
        if action == "clearance":
            assert len([pair for pair in json.loads(data)["pairs"] if abs(pair["min_distance"] - .38) < 1e-5]) == 2
        else:
            assert len(data) > 1000 and mime != "application/json"
        assert not (work / "candidate.tar.gz").exists()


def test_undo_restores_hosted_assets_source_and_parameters(tmp_path):
    import base64
    from cad.project import read_overlay

    def job(name, request, previous=None):
        work = tmp_path / name
        work.mkdir()
        if previous:
            shutil.copyfile(previous / "candidate.tar.gz", work / "project.tar.gz")
        status, _, body = execute(work, request)
        assert status == 200, body
        return work

    model = "from cad import Parameter, Part\nw = Parameter('width', 12, min=5, max=20)\ndef build():\n    p = Part('plate')\n    p.box(w, 10, 4)\n    return p\n"
    before = job("before", {"action": "build", "args": {"source": model, "params": {"width": 10}}})
    after = job("after", {"action": "build", "args": {"source": model + "# edited\n", "params": {"width": 16},
                "files": {"size.dat": base64.b64encode(b"16").decode()}}}, before)
    restored = job("undo", {"action": "undo"}, after) / "project"
    assert read_overlay(restored) == {"width": 10}
    assert (restored / "model.py").read_text() == model
    assert not (restored / "size.dat").exists()
