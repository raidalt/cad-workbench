import math

from cad import Part, Parameter, Profile
from cad.design_checks import require_bounds, require_points


def hex_profile(cx, cy, across_flats):
    # A vertex on the +X axis gives horizontal upper/lower flats,
    # so those two flats are parallel to the X axis.
    radius = across_flats / math.sqrt(3.0)
    apothem = across_flats / 2.0
    return Profile.polygon([
        (cx + radius, cy),
        (cx + radius / 2.0, cy + apothem),
        (cx - radius / 2.0, cy + apothem),
        (cx - radius, cy),
        (cx - radius / 2.0, cy - apothem),
        (cx + radius / 2.0, cy - apothem),
    ])


def build():
    length = 16.0
    width = 30.0
    half_height = 12.0

    shaft_diameter = 8.0
    shaft_radial_clearance = Parameter(
        "shaft_radial_clearance", 0.1, min=0.05, max=0.5, unit="mm"
    )
    bore_diameter = shaft_diameter + 2.0 * shaft_radial_clearance

    screw_clearance_diameter = 3.4
    screw_positions = [(-10.0, "neg_y"), (10.0, "pos_y")]

    nut_fit_allowance = Parameter(
        "nut_fit_allowance", 0.2, min=0.1, max=0.4, unit="mm"
    )
    nut_across_flats = 5.5 + nut_fit_allowance
    nut_pocket_depth = 2.4 + nut_fit_allowance

    top = Part("clamp_top")
    top.box(
        length, width, half_height,
        at=(0.0, 0.0, 0.0),
        name="base",
    )
    # Print with the split face on the bed; this does not move the
    # assembled model from its specified Z = 0..12 location.
    top.print_on(top.faces("base.bottom"))

    bottom = Part("clamp_bottom")
    bottom.box(
        length, width, half_height,
        at=(0.0, 0.0, -half_height),
        name="base",
    )
    # The bottom half is flipped for printing, putting its split face
    # on the bed and leaving the nut pockets open away from the bed.
    bottom.print_on(bottom.faces("base.top"))

    # One coaxial X-axis tool cuts the appropriate semicircular channel
    # in each half. The tool overhangs both X ends to ensure a through bore.
    for part in (top, bottom):
        part.cylinder(
            bore_diameter,
            length + 2.0,
            axis="x",
            at=(0.0, 0.0, 0.0),
            align="center",
            op="cut",
            name="shaft_bore",
        )

    for y, side in screw_positions:
        # With no counterbore, the screw heads remain seated on Z = 12.
        top.hole(
            screw_clearance_diameter,
            at=(0.0, y),
            depth=None,
            name=f"screw_clearance_{side}",
        )
        bottom.hole(
            screw_clearance_diameter,
            at=(0.0, y),
            depth=None,
            name=f"screw_clearance_{side}",
        )

        # XY extrusion starts at Z = -12 and cuts upward, exactly 2.6 mm
        # deep at the default allowance.
        bottom.extrude(
            hex_profile(0.0, y, nut_across_flats),
            nut_pocket_depth,
            plane="XY",
            offset=-half_height,
            op="cut",
            name=f"captive_nut_pocket_{side}",
        )

    top.assert_valid(solids=1)
    bottom.assert_valid(solids=1)

    # The disjoint Z ranges permit contact at Z = 0 but no solid overlap.
    require_bounds(
        top,
        minimum=(-8.0, -15.0, 0.0),
        maximum=(8.0, 15.0, 12.0),
        tolerance=0.05,
    )
    require_bounds(
        bottom,
        minimum=(-8.0, -15.0, -12.0),
        maximum=(8.0, 15.0, 0.0),
        tolerance=0.05,
    )

    require_points(
        top,
        [(-7.0, 0.0, 2.0), (0.0, 0.0, 2.0), (7.0, 0.0, 2.0)],
        material=False,
        label="upper shaft bore is open along X",
    )
    require_points(
        bottom,
        [(-7.0, 0.0, -2.0), (0.0, 0.0, -2.0), (7.0, 0.0, -2.0)],
        material=False,
        label="lower shaft bore is open along X",
    )
    require_points(
        top,
        [(0.0, -10.0, 6.0), (0.0, 10.0, 6.0)],
        material=False,
        label="top M3 clearance holes",
    )
    require_points(
        bottom,
        [(0.0, -10.0, -6.0), (0.0, 10.0, -6.0)],
        material=False,
        label="bottom M3 clearance holes",
    )

    # Check pocket depth away from the central screw holes.
    require_points(
        bottom,
        [(2.5, -10.0, -11.0), (2.5, 10.0, -11.0)],
        material=False,
        label="hex nut pocket interiors",
    )
    require_points(
        bottom,
        [(2.5, -10.0, -8.5), (2.5, 10.0, -8.5)],
        material=True,
        label="material above hex nut pockets",
    )

    # These inside/outside samples also verify the X-parallel flat
    # orientation throughout the declared nut-allowance range.
    require_points(
        bottom,
        [(0.0, -12.5, -11.0), (0.0, 12.5, -11.0)],
        material=False,
        label="inside X-parallel nut flats",
    )
    require_points(
        bottom,
        [(0.0, -13.3, -11.0), (0.0, 13.3, -11.0)],
        material=True,
        label="material outside X-parallel nut flats",
    )

    return {
        "clamp_top": top,
        "clamp_bottom": bottom,
    }
