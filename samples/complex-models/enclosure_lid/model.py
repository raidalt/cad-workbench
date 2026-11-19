from cad import Part, Parameter
from cad.design_checks import require_bounds, require_points


def rounded_rect_prism(part, width, depth, radius, z, height, op):
    """Add or cut a rounded-rectangle prism centred on the Z axis."""
    part.box(
        width - 2 * radius,
        depth,
        height,
        at=(0, 0, z),
        op=op,
    )
    part.box(
        width,
        depth - 2 * radius,
        height,
        at=(0, 0, z),
        op=op,
    )

    cx = width / 2 - radius
    cy = depth / 2 - radius
    for x, y in ((-cx, -cy), (-cx, cy), (cx, -cy), (cx, cy)):
        part.cylinder(
            2 * radius,
            height,
            axis="z",
            at=(x, y, z),
            align="min",
            op=op,
        )


def build():
    width = 100.0
    depth = 70.0
    box_height = 40.0
    wall = 2.5
    floor = 2.5
    outer_radius = 6.0

    lid_thickness = 3.0
    lid_bottom = box_height
    lip_depth = 3.0
    lip_wall = 2.0
    clearance = Parameter("lip_clearance", 0.25, min=0.2, max=0.6)

    # Box: subtract a rounded cavity, leaving the floor and uniform walls.
    box = Part("box")
    rounded_rect_prism(
        box, width, depth, outer_radius, 0.0, box_height, "add"
    )

    inner_width = width - 2 * wall
    inner_depth = depth - 2 * wall
    inner_radius = outer_radius - wall
    rounded_rect_prism(
        box,
        inner_width,
        inner_depth,
        inner_radius,
        floor,
        box_height - floor + 1.0,
        "cut",
    )

    # Lid lip: concentric offsets maintain clearance and wall thickness
    # around both the straight sides and the rounded corners.
    lip_outer_width = inner_width - 2 * clearance
    lip_outer_depth = inner_depth - 2 * clearance
    lip_outer_radius = inner_radius - clearance

    lip_inner_width = lip_outer_width - 2 * lip_wall
    lip_inner_depth = lip_outer_depth - 2 * lip_wall
    lip_inner_radius = lip_outer_radius - lip_wall

    lip_bottom = lid_bottom - lip_depth
    lid = Part("lid")

    # The hidden 0.2 mm extension overlaps the plate to ensure one fused
    # solid; the exposed locating lip remains exactly 3 mm deep.
    rounded_rect_prism(
        lid,
        lip_outer_width,
        lip_outer_depth,
        lip_outer_radius,
        lip_bottom,
        lip_depth + 0.2,
        "add",
    )
    rounded_rect_prism(
        lid,
        lip_inner_width,
        lip_inner_depth,
        lip_inner_radius,
        lip_bottom,
        lip_depth,
        "cut",
    )
    rounded_rect_prism(
        lid,
        width,
        depth,
        outer_radius,
        lid_bottom,
        lid_thickness,
        "add",
    )

    box.assert_valid()
    lid.assert_valid()

    require_bounds(
        box,
        minimum=(-50.0, -35.0, 0.0),
        maximum=(50.0, 35.0, 40.0),
        tolerance=0.05,
    )
    require_bounds(
        lid,
        minimum=(-50.0, -35.0, 37.0),
        maximum=(50.0, 35.0, 43.0),
        tolerance=0.05,
    )

    # Probe the floor, walls, open cavity, lip, and local running clearance.
    lip_mid_z = lip_bottom + lip_depth / 2
    lip_x = lip_outer_width / 2 - lip_wall / 2
    lip_y = lip_outer_depth / 2 - lip_wall / 2
    clearance_x = (inner_width / 2 + lip_outer_width / 2) / 2

    require_points(
        box,
        [(0, 0, floor / 2), (48.0, 0, lip_mid_z), (0, 33.75, 20.0)],
        material=True,
        label="box floor and walls",
    )
    require_points(
        box,
        [(0, 0, 20.0), (clearance_x, 0, lip_mid_z)],
        material=False,
        label="open box cavity and lip clearance",
    )
    require_points(
        lid,
        [(0, 0, lid_bottom + lid_thickness / 2)],
        material=True,
        label="lid plate",
    )
    require_points(
        lid,
        [(lip_x, 0, lip_mid_z), (0, lip_y, lip_mid_z)],
        material=True,
        label="locating lip walls",
    )
    require_points(
        lid,
        [
            (0, 0, lip_mid_z),
            (clearance_x, 0, lip_mid_z),
            (48.0, 0, lip_mid_z),
        ],
        material=False,
        label="lip opening and separation from box wall",
    )

    return {"box": box, "lid": lid}
