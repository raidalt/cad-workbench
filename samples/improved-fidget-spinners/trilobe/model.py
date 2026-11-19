from cad import Part, Profile
from cad import captive_race_profiles
from math import acos, cos, pi, sin


def build():
    # The race supplies the shortest normal running clearance directly.
    bearing_radius = 7.0
    bearing_height = 8.0
    running_clearance = 0.35
    ridge_height = 1.6

    spindle_profile, bore_profile = captive_race_profiles(
        radius=bearing_radius,
        height=bearing_height,
        clearance=running_clearance,
        ridge=ridge_height,
    )

    # Rotor plan: three 25 mm lobes on a 20 mm pitch radius.
    # Its circumscribed diameter is 65 mm and its widest X bounding
    # dimension is about 59.6 mm in this orientation.
    rotor = Part("rotor", material="PLA")
    center_radius = 15.0
    lobe_radius = 12.5
    lobe_pitch_radius = 20.0
    rotor_height = bearing_height
    lobe_angles = (pi / 2.0, 7.0 * pi / 6.0, 11.0 * pi / 6.0)
    lobe_centers = [
        (
            lobe_pitch_radius * cos(angle),
            lobe_pitch_radius * sin(angle),
        )
        for angle in lobe_angles
    ]

    rotor.cylinder(
        2.0 * center_radius,
        rotor_height,
        at=(0.0, 0.0, 0.0),
        name="center_web",
    )
    for index, (x, y) in enumerate(lobe_centers, start=1):
        rotor.cylinder(
            2.0 * lobe_radius,
            rotor_height,
            at=(x, y, 0.0),
            name="lobe_%d" % index,
        )

    # Round the six reentrant plan corners. Exact intersection locations
    # avoid accidentally selecting cylindrical seam edges.
    intersection_angle = acos(
        (
            center_radius * center_radius
            + lobe_pitch_radius * lobe_pitch_radius
            - lobe_radius * lobe_radius
        )
        / (2.0 * center_radius * lobe_pitch_radius)
    )
    valley_edges = []
    for angle in lobe_angles:
        for side in (-1.0, 1.0):
            polar_angle = angle + side * intersection_angle
            valley_edges.append(
                rotor.edges()
                .nearest(
                    (
                        center_radius * cos(polar_angle),
                        center_radius * sin(polar_angle),
                        rotor_height / 2.0,
                    )
                )
                .one()
            )
    rotor.fillet(valley_edges, 3.0, name="rounded_lobe_valleys")

    # Finger-friendly upper perimeter and a printable 45-degree bed chamfer.
    rotor.fillet(
        rotor.edges().max("z"),
        1.0,
        name="rounded_upper_perimeter",
    )
    rotor.chamfer(
        rotor.edges().min("z"),
        0.8,
        name="lower_perimeter_chamfer",
    )

    # One round through-hole per lobe, with gently chamfered rims.
    hole_radius = 5.5
    for index, (x, y) in enumerate(lobe_centers, start=1):
        rotor.cylinder(
            2.0 * hole_radius,
            rotor_height + 2.0,
            at=(x, y, -1.0),
            op="cut",
            name="lightening_hole_%d" % index,
        )
    rotor.chamfer(
        rotor.edges().circular(radius=hole_radius).max("z"),
        0.5,
        name="upper_hole_rim_chamfers",
    )
    rotor.chamfer(
        rotor.edges().circular(radius=hole_radius).min("z"),
        0.5,
        name="lower_hole_rim_chamfers",
    )

    # Cut the matching captive bore only after completing the exterior.
    rotor.revolve(
        bore_profile,
        plane="XZ",
        axis="v",
        op="cut",
        name="bearing_bore",
    )

    # The spindle and lower finger pad are part of the fixed hub.
    hub = Part("hub", material="PLA")
    hub.revolve(
        spindle_profile,
        plane="XZ",
        axis="v",
        name="spindle",
    )

    # A neck overlaps the spindle for a single connected solid. The grip
    # flares outward at 45 degrees, so its underside needs no support.
    # Its lowest flare point is 0.4 mm above the rotor's top plane.
    grip_profile = Profile.polygon(
        [
            (0.0, 7.5),
            (bearing_radius, 7.5),
            (bearing_radius, 8.4),
            (9.0, 10.4),
            (9.0, 11.8),
            (0.0, 11.8),
        ]
    )
    hub.revolve(
        grip_profile,
        plane="XZ",
        axis="v",
        name="finger_grip",
    )
    hub.fillet(
        hub.edges().circular(radius=9.0).max("z").one(),
        0.8,
        name="rounded_grip_top",
    )
    hub.fillet(
        hub.edges().circular(radius=9.0).min("z").one(),
        0.5,
        name="rounded_grip_shoulder",
    )

    # Both intended bodies are single solids on the same Z=0 bed.
    hub.print_on(hub.faces().facing("-z").min("z"))
    rotor.print_on(rotor.faces().facing("-z").min("z"))
    hub.assert_valid(solids=1)
    rotor.assert_valid(solids=1)

    # Check the assembled rest geometry, including the sloping race.
    interference = hub.interference(rotor)
    if interference > 1.0e-6:
        raise ValueError(
            "Hub and rotor overlap by %.6g mm^3" % interference
        )
    actual_clearance = hub.distance_to(rotor)
    if actual_clearance < 0.30 - 1.0e-6:
        raise ValueError(
            "Moving-interface clearance is only %.6g mm" % actual_clearance
        )

    return {"hub": hub, "rotor": rotor}
