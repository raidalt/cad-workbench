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
    for action, args in [("clearance", {}), ("export", {"format": "3mf"})]:
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
