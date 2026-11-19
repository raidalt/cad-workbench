from cad import Part, Path, Profile
from cad.design_checks import require_bounds


def build():
    route = Path.polyline(
        [(0, 0, 0), (0, 0, 28), (32, 0, 28)],
        radius=12,
    )

    tube = Part("hollow_90_bent_tube")
    tube.sweep(
        Profile.circle(8) - Profile.circle(5),
        route,
        name="tube",
    )

    tube.assert_valid()
    require_bounds(
        tube,
        minimum=(-4, -4, 0),
        maximum=(32, 4, 32),
        tolerance=0.05,
    )
    return tube
