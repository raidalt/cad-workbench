from cad import Part, Profile
from cad.design_checks import require_bounds


def build():
    reducer = Part("eccentric_reducer")

    lower_section = (
        Profile.circle(32, center=(0, 0))
        - Profile.circle(28, center=(0, 0))
    )
    upper_section = (
        Profile.circle(18, center=(8, 0))
        - Profile.circle(14, center=(8, 0))
    )

    reducer.loft(
        [(lower_section, 0), (upper_section, 30)],
        ruled=True,
        name="continuous_eccentric_passage",
    )

    reducer.assert_valid()
    require_bounds(
        reducer,
        minimum=(-16, -16, 0),
        maximum=(17, 16, 30),
        tolerance=0.05,
    )
    return reducer
