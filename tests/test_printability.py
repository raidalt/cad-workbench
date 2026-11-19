"""Print readiness: cad.printability.report and its /api/print endpoint.

The module tests build small parts directly (no project needed).  The endpoint
tests start one :class:`cad.serve.Service` on port 0 and talk to it with the
stdlib; a second service covers the multi-part project.  Waits are bounded
(see :func:`wait_idle`), never bare sleeps.
"""

import json
import time
import urllib.error
import urllib.request

import pytest

from cad import CadError, Part, _occ
from cad.printability import parse_bed, report
from cad.serve import Service
from cad.tessellate import tessellate

MODEL = '''from cad import Part


def build():
    p = Part("plate")
    p.box(40, 30, 4, name="base")
    return p
'''

TWO_PARTS = '''from cad import Part


def build():
    left = Part("left")
    left.box(10, 10, 10, name="left_box")
    right = Part("right")
    right.box(10, 10, 10, name="right_box")
    return [left, right]
'''


# --------------------------------------------------------------------------- helpers


def codes(rep: dict) -> list[str]:
    return [w["code"] for w in rep["warnings"]]


def cantilever() -> Part:
    """A post with an arm sticking out over empty space: one solid, one overhang."""
    p = Part("cantilever")
    p.box(10, 10, 21, name="post")
    p.box(20, 10, 4, at=(10, 0, 20), op="add", name="arm")
    return p


def chamfered_block() -> Part:
    """A block whose bottom perimeter is a 45-degree chamfer (one 45-degree face)."""
    p = Part("chamfered")
    p.box(40, 30, 10, name="base")
    p.bottom_chamfer(3)
    return p


def write_project(root, model: str = MODEL, name: str = "demo"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "cad.toml").write_text(f'[project]\nname = "{name}"\nentry = "model.py"\n')
    (root / "model.py").write_text(model)
    return root


def request(svc: Service, path: str, timeout: float = 10.0):
    """(status, body) for one GET; 4xx is returned, not raised."""
    url = svc.url.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def call(svc: Service, path: str):
    code, body = request(svc, path)
    return code, json.loads(body)


def wait_idle(svc: Service, deadline: float = 10.0) -> dict:
    """Wait until the initial background build has finished (bounded)."""
    end = time.monotonic() + deadline
    state: dict = {}
    while time.monotonic() < end:
        state = call(svc, "/api/state")[1]
        if not state["building"] and state.get("revision") is not None:
            return state
        time.sleep(0.01)
    raise AssertionError(f"build did not settle within {deadline}s; state={state}")


@pytest.fixture(scope="module")
def svc(tmp_path_factory):
    """One service for the single-part endpoint tests."""
    root = write_project(tmp_path_factory.mktemp("print"))
    with Service(root, port=0) as service:
        wait_idle(service)
        yield service


# --------------------------------------------------------------------------- mass


def test_mass_and_volume_are_consistent():
    p = Part("plate")
    p.box(40, 30, 4)

    rep = report(p, material="PLA")

    assert rep["geometry"]["volume_mm3"] == pytest.approx(4800)
    assert rep["geometry"]["bbox_mm"] == pytest.approx([40, 30, 4])
    assert rep["material"] == {"name": "PLA", "density_g_cm3": 1.24}
    # report() rounds mass_g to 0.01 g (printability.py:178), so the consistency
    # check is to the nearest hundredth, not 1e-6 relative precision.
    expected = 4800 / 1000 * 1.24
    assert rep["mass_g"] == pytest.approx(expected, abs=0.01)
    assert rep["mass_g"] == pytest.approx(round(expected, 2))


# --------------------------------------------------------------------------- overhangs


def test_the_bed_face_is_not_an_overhang():
    p = Part("plate")
    p.box(40, 30, 4, name="base")

    rep = report(p)

    # A plain box rests on its bottom face: it is printed on the plate, not over
    # empty space.  The regression is counting that face as a 0-degree overhang.
    assert rep["overhang"]["area_mm2"] == 0
    assert rep["overhang"]["count"] == 0
    assert "OVERHANG" not in codes(rep)
    # 4 mm is five nozzle-width walls: no thin-wall warning either.
    assert "THIN_WALL" not in codes(rep)

    # Even at 60 degrees, where the bottom face's 0-degree angle would qualify,
    # proximity to the bed must still exclude it.
    assert report(p, overhang_deg=60)["overhang"]["area_mm2"] == 0


def test_a_real_overhang_is_counted():
    p = cantilever()
    assert p.solid_count == 1

    rep = report(p)

    assert rep["overhang"]["area_mm2"] > 0
    assert rep["overhang"]["count"] >= 1
    assert "OVERHANG" in codes(rep)
    assert all(isinstance(f["face"], int) for f in rep["overhang"]["faces"])
    assert all(0.0 <= f["angle_deg"] <= rep["overhang"]["threshold_deg"]
               for f in rep["overhang"]["faces"])
    # The arm's underside is the face that hangs over nothing.
    assert any("arm.bottom" in f["refs"] for f in rep["overhang"]["faces"])


def test_overhang_threshold_moves_the_boundary():
    p = chamfered_block()

    gentle = report(p, overhang_deg=30)
    steep = report(p, overhang_deg=60)

    # A symmetric bottom chamfer is a 45-degree plane: its slope from horizontal
    # is 45 deg, so 45 <= 60 reports it, while 45 <= 30 does not.
    assert gentle["overhang"]["area_mm2"] == 0
    assert "OVERHANG" not in codes(gentle)
    assert steep["overhang"]["area_mm2"] > 0
    assert "OVERHANG" in codes(steep)
    assert all(f["angle_deg"] <= 60 for f in steep["overhang"]["faces"])
    assert all(f["angle_deg"] > 30 for f in steep["overhang"]["faces"])


# --------------------------------------------------------------------------- thin walls


def test_thin_wall_flagged_below_two_perimeters():
    shim = Part("shim")
    shim.box(40, 30, 0.6)

    rep = report(shim)
    assert rep["thin"]["threshold_mm"] == pytest.approx(0.8)
    assert rep["thin"]["min_mm"] == pytest.approx(0.6, abs=0.01)
    assert rep["thin"]["count"] >= 1
    assert "THIN_WALL" in codes(rep)
    assert all(f["thickness"] < 0.8 for f in rep["thin"]["faces"])

    plate = Part("plate")
    plate.box(40, 30, 3)

    solid = report(plate)
    assert solid["thin"]["min_mm"] == pytest.approx(3, abs=0.01)
    assert solid["thin"]["count"] == 0
    assert "THIN_WALL" not in codes(solid)


# --------------------------------------------------------------------------- bed fit


def test_bed_fit_and_parse_bed():
    big = Part("big")
    big.box(300, 10, 10)

    rep = report(big)
    assert rep["bed"]["size_mm"] == pytest.approx([256, 256, 256])
    assert rep["bed"]["fits"] is False
    assert "BED_OVERFLOW" in codes(rep)

    medium = Part("medium")
    medium.box(200, 10, 10)
    assert report(medium, bed="A1 mini")["bed"]["fits"] is False
    assert report(medium, bed="256,256,256")["bed"]["fits"] is True

    assert parse_bed("A1 mini") == (180.0, 180.0, 180.0)
    assert parse_bed((1, 2, 3)) == (1.0, 2.0, 3.0)
    assert parse_bed("200x150x100") == (200.0, 150.0, 100.0)

    with pytest.raises(CadError) as e:
        parse_bed("a bed of nails")
    assert e.value.code == "INVALID_ARGUMENT"


def test_axis_aligned_rotation_can_rescue_an_oversized_pose():
    p = Part("bar")
    p.box(90, 10, 10)  # 90 mm along X does not fit a 50 mm bed in this pose
    bed = (50, 50, 200)

    rep = report(p, bed=bed)

    assert rep["bed"]["fits"] is False
    assert rep["orientation"]["as_modelled"] is True
    assert "BED_OVERFLOW" in codes(rep)

    rotated = rep["orientation"]["fits_if_rotated"]
    assert rotated, "standing the bar on its end makes it fit"
    assert any(all(side <= limit + 1e-9 for side, limit in zip(sides, bed)) for sides in rotated)
    # The physically available rescue: [10, 10, 90].
    assert [10.0, 10.0, 90.0] in rotated


def test_print_on_sets_the_assessed_orientation():
    p = Part("tower")
    p.box(10, 10, 90)

    as_modelled = report(p)
    assert as_modelled["geometry"]["print_down"] is None
    assert as_modelled["orientation"]["as_modelled"] is True
    assert "NO_PRINT_ORIENTATION" in codes(as_modelled)

    p.print_on("+z")
    printed = report(p)
    assert tuple(printed["geometry"]["print_down"]) == (0.0, 0.0, 1.0)
    assert printed["orientation"]["as_modelled"] is False
    assert "NO_PRINT_ORIENTATION" not in codes(printed)


# --------------------------------------------------------------------------- materials


def test_material_resolution_and_unknown_material():
    p = Part("bracket", material="PETG")
    p.box(40, 30, 4)

    rep = report(p)  # no material= argument: the part's own material is used
    assert rep["material"] == {"name": "PETG", "density_g_cm3": 1.27}
    assert rep["mass_g"] == pytest.approx(4800 / 1000 * 1.27, abs=0.01)

    with pytest.raises(CadError) as e:
        report(p, material="unobtainium")
    assert e.value.code == "INVALID_ARGUMENT"  # same code `materials.resolve` uses for a bad name
    assert "unobtainium" in e.value.message


# --------------------------------------------------------------------------- mixed bodies


def test_disjoint_solids_report_once_with_summed_volume():
    p = Part("mixed")
    p.box(10, 10, 10, name="left")
    p.box(10, 10, 10, at=(30, 0, 0), name="right")
    assert p.solid_count == 2

    rep = report(p)  # one report, no exception, volumes summed

    assert rep["part"] == "mixed"
    assert rep["geometry"]["volume_mm3"] == pytest.approx(2000)
    assert rep["geometry"]["bbox_mm"] == pytest.approx([40, 10, 10])
    assert rep["bed"]["fits"] is True


# --------------------------------------------------------------------------- face ids


def test_face_ids_match_the_tessellation_and_occ_order():
    def check(part: Part, rep: dict) -> None:
        mesh_ids = {int(i) for i in tessellate(part.shape).face_ids}
        faces = _occ.faces(part.shape)
        reported = rep["thin"]["faces"] + rep["overhang"]["faces"]
        assert reported, "the part must exercise at least one reported face"
        for f in reported:
            fid = f["face"]
            assert isinstance(fid, int)
            assert fid in mesh_ids, f"face {fid} is not in the mesh face_ids"
            assert 0 <= fid < len(faces)
            assert not faces[fid].IsNull()

    shim = Part("shim")
    shim.box(40, 30, 0.6)
    check(shim, report(shim))

    block = chamfered_block()
    rep = report(block, overhang_deg=60)
    assert rep["overhang"]["faces"]
    check(block, rep)


# --------------------------------------------------------------------------- endpoint


def test_endpoint_reports_print_readiness(svc):
    code, body = call(svc, "/api/print?part=plate&material=PETG")

    assert code == 200
    assert isinstance(body["revision"], int)
    assert body["material"]["name"] == "PETG"
    assert body["mass_g"] == pytest.approx(4800 / 1000 * 1.27, abs=0.01)
    assert body["bed"]["fits"] is True
    assert isinstance(body["thin"], dict)
    assert isinstance(body["overhang"], dict)
    assert isinstance(body["warnings"], list)


def test_endpoint_falls_back_and_honours_min_wall(svc):
    # Non-numeric query values must fall back to the defaults, not blow up.
    code, body = call(svc, "/api/print?part=plate&angle=abc&per_face=xyz")
    assert code == 200
    assert body["overhang"]["threshold_deg"] == 45.0
    assert body["thin"]["threshold_mm"] == pytest.approx(0.8)

    code, body = call(svc, "/api/print?part=plate&min_wall=1.5")
    assert code == 200
    assert body["thin"]["threshold_mm"] == pytest.approx(1.5)


def test_endpoint_errors_are_structured(svc):
    for path, expected in (
        ("/api/print?part=ghost", "NOT_FOUND"),
        ("/api/print?part=plate&material=unobtainium", "INVALID_ARGUMENT"),
        ("/api/print?part=plate&bed=nonsense", "INVALID_ARGUMENT"),
    ):
        code, body = call(svc, path)
        assert 400 <= code < 500, f"{path} returned {code}"
        assert body["ok"] is False
        assert body["error"]["code"] == expected


def test_capabilities_list_print_options(svc):
    state = call(svc, "/api/state")[1]
    caps = state["capabilities"]

    assert "A1 mini" in caps["beds"]
    assert caps["default_bed"] == pytest.approx([256, 256, 256])
    assert {"PLA", "PETG"} <= set(caps["materials"])


def test_endpoint_cache_returns_identical_json_and_varies_with_angle(svc):
    code, first = call(svc, "/api/print?part=plate")
    code2, second = call(svc, "/api/print?part=plate")
    assert code == code2 == 200
    assert first == second, "identical requests must return identical JSON (cache hit)"

    code3, other = call(svc, "/api/print?part=plate&angle=10")
    assert code3 == 200
    assert other != first, "a different angle must not reuse the cached report"
    assert other["overhang"]["threshold_deg"] == 10.0


def test_endpoint_requires_a_part_in_a_multi_part_project(tmp_path):
    root = write_project(tmp_path / "two", TWO_PARTS, name="two")
    with Service(root, port=0) as service:
        state = wait_idle(service)
        assert set(state["parts"]) == {"left", "right"}

        code, body = call(service, "/api/print")
        assert 400 <= code < 500
        err = body["error"]
        assert err["code"] == "INVALID_ARGUMENT"
        assert "parts" in err["message"]
        assert set(err.get("details", {}).get("parts", [])) == {"left", "right"}

        # Naming one part works.
        assert call(service, "/api/print?part=left")[0] == 200
