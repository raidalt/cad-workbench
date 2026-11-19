import math

import pytest

from cad import CadError, Part, Profile, captive_race_profiles
from cad.design_skills import skill_context


@pytest.mark.parametrize("radius,height,gap,ridge", [(7, 8, .35, 1.6), (4, 5, .4, 1.2)])
def test_captive_profiles_build_matching_clear_bodies(radius, height, gap, ridge):
    spindle, bore = captive_race_profiles(radius, height, clearance=gap, ridge=ridge)
    hub, rotor = Part("hub"), Part("rotor")
    hub.revolve(spindle)
    rotor.cylinder(2 * (radius + ridge + 4), height)
    rotor.revolve(bore, op="cut")
    for part in (hub, rotor):
        part.assert_valid(solids=1)
        assert part.bbox.zmin == pytest.approx(0, abs=1e-6)
    assert hub.distance_to(rotor) == pytest.approx(gap, abs=1e-6)
    assert hub.interference(rotor) == pytest.approx(0)
    for sign in (-1, 1):
        shifted = rotor.copy()
        shifted.translate((0, 0, sign * (height - 2 * ridge) / 3 + sign * ridge))
        assert hub.interference(shifted) > .01


@pytest.mark.parametrize("kwargs", [
    {"clearance": 0}, {"ridge": .2}, {"height": 2}, {"radius": math.nan},
])
def test_unusable_races_produce_a_dimension_error(kwargs):
    with pytest.raises(CadError) as failure:
        captive_race_profiles(**({"radius": 6, "height": 8} | kwargs))
    assert failure.value.code == "INVALID_ARGUMENT"


def test_rotary_guidance_is_scoped():
    assert "captive_race_profiles" in skill_context("Make a print-in-place fidget spinner")
    assert "captive_race_profiles" not in skill_context("Draw a square mounting plate")


def test_fillet_failure_identifies_revolve_seam_and_rim_selector_works():
    p = Part("grip")
    p.revolve(Profile.polygon([(0, 0), (4, 0), (4, 5), (9.4, 10.4), (0, 10.4)]))
    with pytest.raises(CadError) as failure:
        p.fillet(p.edges().nearest((9.4, 0, 10.4)), .8)
    assert failure.value.details["failing_edges"][0]["seam"]
    assert "midpoints" in failure.value.hint
    p.fillet(p.edges().circular(radius=9.4).at("z", 10.4).one(), .8)
    p.assert_valid()


def test_gap_probe_distinguishes_hitting_material_from_a_boundary():
    from cad.design_checks import require_points

    spindle, _ = captive_race_profiles(7, 8)
    hub = Part("hub")
    hub.revolve(spindle)
    for point, observed in [((7.2, 0, 4), "material"), ((8.6, 0, 4), "boundary")]:
        with pytest.raises(CadError) as failure:
            require_points(hub, [point], material=False, label="running gap")
        assert failure.value.details["observed"] == observed
