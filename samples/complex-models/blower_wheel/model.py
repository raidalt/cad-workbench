import math

from cad import Part, Profile


BLADE_COUNT = 7
BLADE_SEGMENTS = 64
BLADE_THICKNESS = 2.0
BLADE_R_IN = 14.0
BLADE_R_OUT = 38.0
BLADE_END_ANGLE = math.radians(-35.0)


def base_blade_outline():
    """Return a closed plan outline offset either side of the centerline."""
    side_a = []
    side_b = []
    half_thickness = BLADE_THICKNESS / 2.0
    angle_per_radius = BLADE_END_ANGLE / (BLADE_R_OUT - BLADE_R_IN)

    for j in range(BLADE_SEGMENTS + 1):
        fraction = j / BLADE_SEGMENTS
        radius = BLADE_R_IN + fraction * (BLADE_R_OUT - BLADE_R_IN)
        theta = fraction * BLADE_END_ANGLE

        cos_theta = math.cos(theta)
        sin_theta = math.sin(theta)
        x = radius * cos_theta
        y = radius * sin_theta

        # Derivative of the polar centerline with respect to radius.
        dx = cos_theta - radius * angle_per_radius * sin_theta
        dy = sin_theta + radius * angle_per_radius * cos_theta
        tangent_length = math.hypot(dx, dy)

        # Unit normal in the XY plane, giving nominal 2 mm thickness.
        nx = -dy / tangent_length
        ny = dx / tangent_length

        side_a.append((x + half_thickness * nx, y + half_thickness * ny))
        side_b.append((x - half_thickness * nx, y - half_thickness * ny))

    return side_a + list(reversed(side_b))


def rotated_outline(outline, angle):
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    return [
        (x * cos_angle - y * sin_angle, x * sin_angle + y * cos_angle)
        for x, y in outline
    ]


def build():
    wheel = Part("wheel")

    wheel.cylinder(
        80, 2, axis="z", at=(0, 0, 0), name="rear_disc"
    )
    wheel.cylinder(
        80, 2, axis="z", at=(0, 0, 11), name="front_shroud"
    )

    outline = base_blade_outline()
    for index in range(BLADE_COUNT):
        angle = index * 2.0 * math.pi / BLADE_COUNT
        profile = Profile.polygon(rotated_outline(outline, angle))

        # The 0.1 mm overlaps are buried in the disc and shroud.
        # Exposed blade surfaces extend from z=2 to z=11.
        wheel.loft(
            [(profile, 1.9), (profile, 11.1)],
            ruled=True,
        )

    # Cut the intake before adding the hub so the hub remains continuous.
    wheel.cylinder(
        28,
        2,
        axis="z",
        at=(0, 0, 11),
        op="cut",
        name="annular_intake",
    )

    # The portion below z=2 overlaps the rear disc; exposed hub reaches z=15.
    wheel.cylinder(
        18, 15, axis="z", at=(0, 0, 0), name="shaft_hub"
    )

    # Overrun both ends slightly to guarantee a clean through bore.
    wheel.cylinder(
        6,
        17,
        axis="z",
        at=(0, 0, -1),
        op="cut",
        name="shaft_bore",
    )

    wheel.assert_valid()
    return wheel
