from math import cos, radians, sin

from cad import Assembly, Parameter, Part
from cad.design_checks import require_bounds


PIVOT_DIA = 5.2
LINK_BOSS_DIA = 18.0
LINK_THICKNESS = 10.0

# The turret ends at local Y = 16. The links start at Y = 17 and 28,
# giving 1 mm axial gaps that are unchanged by all three joints.
LOWER_Y = 22.0
UPPER_Y = 33.0


def cut_y_pivot(part, x, y, z, name):
    # The cutting cylinder extends 2 mm beyond both side faces.
    part.cylinder(
        PIVOT_DIA,
        LINK_THICKNESS + 4.0,
        axis="y",
        at=(x, y, z),
        align="center",
        op="cut",
        name=name,
    )


def build():
    yaw = Parameter("yaw", 0, min=-90, max=90, unit="deg")
    shoulder = Parameter("shoulder", 0, min=-45, max=45, unit="deg")
    elbow = Parameter("elbow", 0, min=-60, max=60, unit="deg")

    # Base: exact requested envelope, with an empty central yaw-pivot hole.
    base = Part("base", material="PETG")
    base.cylinder(
        80.0,
        10.0,
        axis="z",
        at=(0, 0, 0),
        align="min",
        name="round_foot",
    )
    base.cylinder(
        PIVOT_DIA,
        12.0,
        axis="z",
        at=(0, 0, -1),
        align="min",
        op="cut",
        name="yaw_pivot_hole",
    )
    require_bounds(
        base,
        minimum=(-40, -40, 0),
        maximum=(40, 40, 10),
        tolerance=0.05,
    )
    base.print_on(base.faces().facing("-z").nearest((0, 0, 0)))
    base.assert_valid(solids=1)

    # Turret: bottom is exactly 0.5 mm above the base. Its blind yaw hole
    # stops well below the transverse shoulder hole, so the holes do not meet.
    turret = Part("turret", material="PETG")
    turret.cylinder(
        32.0,
        39.0,
        axis="z",
        at=(0, 0, 10.5),
        align="min",
        name="turret_body",
    )
    turret.cylinder(
        PIVOT_DIA,
        8.1,
        axis="z",
        at=(0, 0, 10.4),
        align="min",
        op="cut",
        name="yaw_pivot_hole",
    )
    turret.cylinder(
        PIVOT_DIA,
        40.0,
        axis="y",
        at=(0, 0, 40),
        align="center",
        op="cut",
        name="shoulder_pivot_hole",
    )
    turret.print_on(turret.faces().facing("-z").nearest((0, 0, 10.5)))
    turret.assert_valid(solids=1)

    # Lower arm: 120 mm between pivot centres. The box and round bosses
    # form a constant-thickness capsule with 6.4 mm radial hole surrounds.
    lower_arm = Part("lower_arm", material="PETG")
    lower_arm.box(
        LINK_BOSS_DIA,
        LINK_THICKNESS,
        120.0,
        at=(0, LOWER_Y, 40),
        name="link_web",
    )
    lower_arm.cylinder(
        LINK_BOSS_DIA,
        LINK_THICKNESS,
        axis="y",
        at=(0, LOWER_Y, 40),
        align="center",
        name="shoulder_boss",
    )
    lower_arm.cylinder(
        LINK_BOSS_DIA,
        LINK_THICKNESS,
        axis="y",
        at=(0, LOWER_Y, 160),
        align="center",
        name="elbow_boss",
    )
    cut_y_pivot(lower_arm, 0, LOWER_Y, 40, "shoulder_pivot_hole")
    cut_y_pivot(lower_arm, 0, LOWER_Y, 160, "elbow_pivot_hole")
    lower_arm.print_on(
        lower_arm.faces().facing("-y").nearest((0, 17, 100))
    )
    lower_arm.assert_valid(solids=1)

    # Upper arm: 100 mm between pivot centres, initially horizontal along +X.
    upper_arm = Part("upper_arm", material="PETG")
    upper_arm.box(
        100.0,
        LINK_THICKNESS,
        LINK_BOSS_DIA,
        at=(50, UPPER_Y, 151),
        name="link_web",
    )
    upper_arm.cylinder(
        LINK_BOSS_DIA,
        LINK_THICKNESS,
        axis="y",
        at=(0, UPPER_Y, 160),
        align="center",
        name="elbow_boss",
    )
    upper_arm.cylinder(
        LINK_BOSS_DIA,
        LINK_THICKNESS,
        axis="y",
        at=(100, UPPER_Y, 160),
        align="center",
        name="lamp_head_boss",
    )
    cut_y_pivot(upper_arm, 0, UPPER_Y, 160, "elbow_pivot_hole")
    cut_y_pivot(upper_arm, 100, UPPER_Y, 160, "lamp_head_pivot_hole")
    upper_arm.print_on(
        upper_arm.faces().facing("-y").nearest((50, 28, 160))
    )
    upper_arm.assert_valid(solids=1)

    # Verify the modelled rest pose. Motion clearance is provided by the
    # disjoint local-Y slabs above, rather than by this single-pose check.
    for first, second in (
        (base, turret),
        (base, lower_arm),
        (base, upper_arm),
        (turret, lower_arm),
        (turret, upper_arm),
        (lower_arm, upper_arm),
    ):
        assert first.interference(second) <= 1e-6
        assert first.distance_to(second) >= 0.5 - 1e-6

    # Analytical worst case over the declared shoulder/elbow ranges:
    # shoulder=45 deg and elbow=-45 deg put the head pivot at Z=24.8528.
    # Its 9 mm boss radius therefore leaves its bottom at Z=15.8528,
    # at least 5.8528 mm above the base throughout the joint ranges.
    worst_head_z = 40 + 120 * cos(radians(45)) - 100 * sin(radians(90))
    assert worst_head_z - LINK_BOSS_DIA / 2 - 10 >= 0.5

    asm = Assembly("desk_lamp_arm")
    asm.add(base, ground=True)
    asm.add(turret)
    asm.add(lower_arm)
    asm.add(upper_arm)

    # Axes are (point, direction). +Y gives the requested shoulder sign;
    # -Y reverses the elbow rotation so positive elbow raises the head.
    asm.revolute(
        "yaw",
        base,
        turret,
        axis=((0, 0, 0), (0, 0, 1)),
        angle=yaw,
        limits=(-90, 90),
    )
    asm.revolute(
        "shoulder",
        turret,
        lower_arm,
        axis=((0, 0, 40), (0, 1, 0)),
        angle=shoulder,
        limits=(-45, 45),
    )
    asm.revolute(
        "elbow",
        lower_arm,
        upper_arm,
        axis=((0, 0, 160), (0, -1, 0)),
        angle=elbow,
        limits=(-60, 60),
    )
    return asm
