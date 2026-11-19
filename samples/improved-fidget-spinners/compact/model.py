from cad import Part, Parameter, captive_race_profiles
from cad.design_checks import require_points


def build():
    outer_diameter = 36.0
    height = 6.0
    hole_diameter = 3.0
    pitch_circle_radius = 13.0

    race_radius = 6.0
    retention_ridge = 1.2
    clearance = Parameter("clearance", 0.45, min=0.40, max=0.60)
    clearance_value = float(clearance)

    # The paired profiles provide relieved ends, 45-degree support-free
    # retention ramps, and positive capture in both axial directions.
    spindle, bore = captive_race_profiles(
        radius=race_radius,
        height=height,
        clearance=clearance_value,
        ridge=retention_ridge,
    )

    hub = Part("hub", material="PETG")
    hub.revolve(spindle, plane="XZ", axis="v", name="spindle")

    rotor = Part("rotor", material="PETG")
    rotor.cylinder(
        outer_diameter,
        height,
        axis="z",
        at=(0, 0, 0),
        align="min",
        name="base",
    )

    outer_radius = outer_diameter / 2.0

    # Support-free lower edge and a rounded upper grip rim.
    rotor.chamfer(
        rotor.edges().circular(radius=outer_radius).min("z").one(),
        1.0,
    )
    rotor.fillet(
        rotor.edges().circular(radius=outer_radius).max("z").one(),
        1.2,
    )

    rotor.revolve(
        bore,
        plane="XZ",
        axis="v",
        op="cut",
        name="bearing_bore",
    )

    hole_centres = [
        (13.0, 0.0),
        (0.0, 13.0),
        (-13.0, 0.0),
        (0.0, -13.0),
    ]
    for x, y in hole_centres:
        rotor.hole(hole_diameter, at=(x, y), depth=None)

    # Verify the holes are open near both faces and at mid-height.
    require_points(
        rotor,
        [
            (x, y, z)
            for x, y in hole_centres
            for z in (0.5, height / 2.0, height - 0.5)
        ],
        material=False,
        label="index holes open through the outer ring",
    )

    # Verify material remains between the bearing track and index holes.
    require_points(
        rotor,
        [(10.0, 0.0, 3.0), (0.0, 10.0, 3.0),
         (-10.0, 0.0, 3.0), (0.0, -10.0, 3.0)],
        material=True,
        label="inner ring webs",
    )

    hub.assert_valid(solids=1)
    rotor.assert_valid(solids=1)

    overlap = hub.interference(rotor)
    assert overlap <= 1e-6, f"Hub/rotor interference: {overlap} mm^3"

    gap = hub.distance_to(rotor)
    assert gap >= 0.40 - 0.005, f"Running clearance is only {gap} mm"
    assert gap >= clearance_value - 0.02, (
        f"Race gap {gap} mm is below the requested {clearance_value} mm"
    )

    hub.print_on(hub.faces().facing("-z").min("z").one())
    rotor.print_on(rotor.faces().facing("-z").min("z").one())

    return {"hub": hub, "rotor": rotor}
