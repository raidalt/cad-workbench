from pathlib import Path

import pytest

from cad import CadError, Part
from cad.design_checks import require_points
from cad.project import write_overlay
from cad.serve import Service


MODEL = """from cad import Part, Parameter

def build():
    width = Parameter("width", 10, min=5, max=15)
    part = Part("block")
    part.box(width, 10, 4)
    return part
"""


@pytest.fixture
def project(tmp_path: Path) -> Service:
    (tmp_path / "cad.toml").write_text('[project]\nname="design"\n')
    (tmp_path / "model.py").write_text(MODEL)
    service = Service(tmp_path, port=0)
    service.session.build()
    return service


def test_parameter_override_during_generation_blocks_publish(project: Service):
    original = (project.root / "model.py").read_text()
    _, commit = project.builder.prepare_chat(None, 1)
    write_overlay(project.root, {"width": 14})

    outcome = commit(original.replace("10, 4", "10, 6"))

    assert not outcome["ok"]
    assert outcome["error"]["code"] == "STALE_VIEW"
    assert project.revision == 1
    assert (project.root / "model.py").read_text() == original


def test_point_assertion_locates_material_and_void():
    plate = Part("plate")
    plate.box(20, 20, 4)
    plate.hole(5, at=(0, 0))

    require_points(plate, [(6, 0, 2)], material=True, label="keep the web")
    require_points(plate, [(0, 0, 2)], material=False, label="clear bolt path")

    with pytest.raises(CadError) as caught:
        require_points(plate, [(0, 0, 2)], material=True, label="keep the web")
    assert caught.value.details["point"] == [0, 0, 2]

    with pytest.raises(CadError, match="expected void"):
        require_points(plate, [(10, 0, 2)], material=False, label="boundary is not clearance")
