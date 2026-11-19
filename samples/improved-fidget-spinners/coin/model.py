import math

from cad import Part, Profile, captive_race_profiles
from cad.design_checks import require_points


def build():
    # The race supplies 45-degree retaining ramps and relieved ends.
    # Its clearance is the shortest normal surface-to-surface gap.
    bearing_radius = 8.0
    bearing_height = 8.0
    running_clearance = 0.35
    retaining_ridge = 1.6

    spindle, bore = captive_race_profiles(
        radius=bearing_radius,
        height=bearing_height,
        clearance=running_clearance,
        ridge=retaining_ridge,
    )

    hub = Part("hub", material="PLA")
    hub.revolve(spindle, name="spindle")

    # A raised, supported thumb pad; the hub remains within the 9 mm limit.
    pad_radius = 7.0
    hub.cylinder(
        2 * pad_radius,
        1.0,
        at=(0, 0, bearing_height),
        name="thumb_pad",
    )
    hub.fillet(
        hub.edges().circular(radius=pad_radius).max("z").one(),
        0.4,
    )

    rotor = Part("rotor", material="PLA")

    # Smooth eight-lobed plan outline. The maximum radius remains 21 mm,
    # while each shallow scallop recesses the rim by 0.8 mm.
    outer_radius = 21.0
    scallop_depth = 0.8
    edge_chamfer = 0.8
    segments = 192

    def rim_profile(inset):
        points = []
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            scallop = 0.5 + 0.5 * math.cos(8 * angle)
            radius = outer_radius - inset - scallop_depth * scallop
            points.append(
                (radius * math.cos(angle), radius * math.sin(angle))
            )
        return Profile.polygon(points)

    # Equal radial and axial offsets produce 45-degree lower and upper
    # chamfers. The lower chamfer preserves a broad, flat annular bed face.
    rotor.loft(
        [
            (rim_profile(edge_chamfer), 0.0),
            (rim_profile(0.0), edge_chamfer),
            (rim_profile(0.0), bearing_height - edge_chamfer),
            (rim_profile(edge_chamfer), bearing_height),
        ],
        ruled=True,
    )
    rotor.revolve(bore, op="cut", name="bearing_bore")

    # Check every scallop and intervening grip lobe at mid-height.
    scallop_voids = []
    lobe_material = []
    for i in range(8):
        scallop_angle = i * math.pi / 4
        lobe_angle = scallop_angle + math.pi / 8
        scallop_voids.append(
            (
                20.4 * math.cos(scallop_angle),
                20.4 * math.sin(scallop_angle),
                4.0,
            )
        )
        lobe_material.append(
            (
                20.8 * math.cos(lobe_angle),
                20.8 * math.sin(lobe_angle),
                4.0,
            )
        )

    require_points(
        rotor,
        scallop_voids,
        material=False,
        label="eight shallow rim scallops",
    )
    require_points(
        rotor,
        lobe_material,
        material=True,
        label="material between rim scallops",
    )

    hub.print_on(hub.faces().facing("-z").nearest((0, 0, 0)))
    rotor.print_on(rotor.faces().facing("-z").nearest((0, 0, 0)))

    hub.assert_valid(solids=1)
    rotor.assert_valid(solids=1)

    measured_clearance = hub.distance_to(rotor)
    assert measured_clearance >= 0.30 - 1e-6, (
        f"Bearing clearance is only {measured_clearance:.3f} mm"
    )

    return {"hub": hub, "rotor": rotor}
