from math import cos, sin, pi

from cad import Part, captive_race_profiles


def build():
    # All dimensions are in mm. The races and all exterior blanks share Z=0.
    height = 10.0
    clearance = 0.35
    ridge = 1.6
    inner_race_radius = 7.0
    outer_race_radius = 15.0
    outside_radius = 26.0

    inner_spindle, inner_bore = captive_race_profiles(
        radius=inner_race_radius,
        height=height,
        clearance=clearance,
        ridge=ridge,
    )
    outer_spindle, outer_bore = captive_race_profiles(
        radius=outer_race_radius,
        height=height,
        clearance=clearance,
        ridge=ridge,
    )

    # Central finger hub and the inner half of the first captive bearing.
    hub = Part("hub", material="PLA")
    hub.revolve(
        inner_spindle,
        plane="XZ",
        axis="v",
        name="hub_spindle",
    )

    # The middle body is the outer spindle and carries the inner bearing bore.
    # The paired profiles provide the retaining ramps and relieved end faces.
    middle = Part("middle", material="PLA")
    middle.revolve(
        outer_spindle,
        plane="XZ",
        axis="v",
        name="middle_outer_spindle",
    )
    middle.revolve(
        inner_bore,
        plane="XZ",
        axis="v",
        op="cut",
        name="middle_inner_bearing_bore",
    )

    # Outer grip ring: rounded top perimeter and a printable 45-degree
    # lower chamfer. Its maximum diameter remains exactly 52 mm.
    outer = Part("outer", material="PLA")
    outer.cylinder(
        2.0 * outside_radius,
        height,
        at=(0, 0, 0),
        name="outer_blank",
    )
    outer.chamfer(
        outer.edges().circular(radius=outside_radius).min("z").one(),
        0.8,
    )
    outer.fillet(
        outer.edges().circular(radius=outside_radius).max("z").one(),
        1.0,
    )
    outer.revolve(
        outer_bore,
        plane="XZ",
        axis="v",
        op="cut",
        name="outer_bearing_bore",
    )

    # Six rotation markers. Conservative wall calculations use the largest
    # possible bore radius, including its retaining ridge and running gap.
    marker_diameter = 3.0
    marker_circle_radius = 21.5
    largest_outer_bore_radius = outer_race_radius + ridge + clearance
    inner_marker_wall = (
        marker_circle_radius
        - marker_diameter / 2.0
        - largest_outer_bore_radius
    )
    outer_marker_wall = (
        outside_radius - marker_circle_radius - marker_diameter / 2.0
    )
    assert inner_marker_wall >= 3.0
    assert outer_marker_wall >= 3.0

    for index in range(6):
        angle = 2.0 * pi * index / 6.0
        outer.hole(
            marker_diameter,
            at=(
                marker_circle_radius * cos(angle),
                marker_circle_radius * sin(angle),
            ),
            depth=None,
        )

    # Keep every independent body on the same bed without merging them.
    hub.print_on(hub.faces("hub_spindle.bottom"))
    middle.print_on(middle.faces("middle_outer_spindle.bottom"))
    outer.print_on(outer.faces("outer_blank.bottom"))

    hub.assert_valid(solids=1)
    middle.assert_valid(solids=1)
    outer.assert_valid(solids=1)

    # Check the finished solids, including the sloping retaining surfaces.
    required_gap = 0.30
    tolerance = 1e-6
    inner_gap = hub.distance_to(middle)
    outer_gap = middle.distance_to(outer)
    assert inner_gap >= required_gap - tolerance, (
        "Hub/middle clearance is below 0.30 mm: "
        f"{inner_gap:.6f} mm"
    )
    assert outer_gap >= required_gap - tolerance, (
        "Middle/outer clearance is below 0.30 mm: "
        f"{outer_gap:.6f} mm"
    )

    return {
        "hub": hub,
        "middle": middle,
        "outer": outer,
    }
